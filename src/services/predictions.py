"""Channel points predictions of idle watching: the outcome strategy, the stake and one bet per event."""

from __future__ import annotations

import asyncio
import logging
import secrets
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from src.config import GQL_OPERATIONS
from src.services.points_common import error_code, remember
from src.utils import task_wrapper


if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from src.config import JsonType
    from src.core.client import Twitch
    from src.models.channel import Channel
    from src.services.idle_watch import IdleWatchService


logger = logging.getLogger("TwitchDrops")

# the bet is placed this long before the prediction window ends, from the outcomes seen last
PREDICTION_DELAY_SECONDS = 6
# Twitch takes 10..250000 points per prediction
MIN_BET = 10
# smart: a user split of the first two outcomes within this many percentage points bets high_odds
SMART_GAP = 20


class PredictionStrategy:
    """Pick an outcome from a prediction's outcomes; ties keep the first outcome."""

    @staticmethod
    def _count(outcome: JsonType, key: str) -> int:
        value = outcome.get(key)
        return value if type(value) is int and value > 0 else 0

    @classmethod
    def choose(cls, strategy: str, outcomes: list[JsonType]) -> int | None:
        """Index of the outcome to bet on, None with fewer than two outcomes.

        most_voted: most users. favourite: most points. high_odds: fewest points (the largest payout
        per point). smart: high_odds when the user split of the first two outcomes is within SMART_GAP
        percentage points (no users counts as an even split), otherwise most_voted.
        """
        if len(outcomes) < 2:
            return None
        users = [cls._count(o, "total_users") for o in outcomes]
        points = [cls._count(o, "total_points") for o in outcomes]
        indexes = range(len(outcomes))
        if strategy == "smart":
            pair = users[0] + users[1]
            gap = abs(users[0] - users[1]) * 100 / pair if pair else 0
            strategy = "high_odds" if gap <= SMART_GAP else "most_voted"
        if strategy == "favourite":
            return max(indexes, key=points.__getitem__)
        if strategy == "high_odds":
            return min(indexes, key=points.__getitem__)
        return max(indexes, key=users.__getitem__)

    @staticmethod
    def amount(balance: int, percent: int, maximum: int) -> int:
        """The stake: percent of the balance, rounded down, at most maximum and never above the balance."""
        return max(min(balance * percent // 100, maximum, balance), 0)


class Predictions:
    """Bet once per prediction event on live listed channels, PREDICTION_DELAY_SECONDS before it locks.

    Events come from the `predictions-channel-v1` topic, results of placed bets from
    `predictions-user-v1`. Every check runs again when the timer fires: the loop runs, points are not
    paused, mining time, the channel is listed, live and not one drops can use, predictions are on,
    the event is still ACTIVE and not bet on, and 10 <= amount <= min(prediction_max_points, balance).
    """

    def __init__(
        self,
        twitch: Twitch,
        service: IdleWatchService,
        send: Callable[..., Awaitable[tuple[JsonType | None, str | None]]],
    ) -> None:
        self._twitch = twitch
        self._service = service
        self._send = send
        # event id -> (channel, the latest event object) of open events seen while the loop runs
        self._latest: dict[str, tuple[Channel, JsonType]] = {}
        self._timers: dict[str, asyncio.Task[None]] = {}
        # events whose timer fired (bet or skipped): never bet on again
        self._handled: dict[str, None] = {}
        # event id -> (channel name, title) of placed bets, for the result lines
        self._placed: dict[str, tuple[str, str]] = {}

    def clear(self) -> None:
        """Drop the open events and their timers when the loop stops; handled ids stay."""
        for task in self._timers.values():
            task.cancel()
        self._timers.clear()
        self._latest.clear()

    def reset(self) -> None:
        """Forget everything on an account change."""
        self.clear()
        self._handled.clear()
        self._placed.clear()

    @task_wrapper
    async def process_event(self, channel_id: int, message: JsonType) -> None:
        """Handle `event-created` / `event-updated` of a channel's predictions topic.

        Shape: {"type": "event-updated", "data": {"event": {"id", "status", "title", "created_at",
        "prediction_window_seconds", "outcomes": [{"id", "title", "total_users", "total_points"}]}}}
        """
        if not isinstance(message, dict) or message.get("type") not in ("event-created", "event-updated"):
            return
        data = message.get("data")
        event = data.get("event") if isinstance(data, dict) else None
        event_id = event.get("id") if isinstance(event, dict) else None
        if not isinstance(event, dict) or not isinstance(event_id, str) or not event_id:
            return
        channel = self._service.listed(str(channel_id))
        if channel is None:
            return
        if event.get("status") != "ACTIVE":
            # locked, resolved or canceled: no bet any more
            if (task := self._timers.pop(event_id, None)) is not None:
                task.cancel()
            self._latest.pop(event_id, None)
            return
        if not self._service.active:
            return
        self._latest[event_id] = (channel, event)
        if event_id in self._handled or event_id in self._timers:
            return
        delay = self._delay(event)
        if delay is None:
            logger.debug("Prediction %s skipped: no usable window", event_id)
            return
        self._timers[event_id] = asyncio.create_task(self._fire(event_id, delay))

    @staticmethod
    def _delay(event: JsonType) -> float | None:
        """Seconds until PREDICTION_DELAY_SECONDS before the window ends; None for a malformed event."""
        window = event.get("prediction_window_seconds")
        created = event.get("created_at")
        if type(window) is not int or window <= 0 or not isinstance(created, str):
            return None
        try:
            start = datetime.fromisoformat(created)
        except ValueError:
            return None
        if start.tzinfo is None:
            return None
        remaining = (start - datetime.now(timezone.utc)).total_seconds() + window - PREDICTION_DELAY_SECONDS
        return max(remaining, 0.0)

    async def _fire(self, event_id: str, delay: float) -> None:
        await asyncio.sleep(delay)
        self._timers.pop(event_id, None)
        await self.bet(event_id)

    def _refusal(self, channel: Channel, event: JsonType) -> tuple[str, int, int] | str:
        """(outcome id, outcome index, amount) of the bet to place, or why there is none."""
        tw = self._twitch
        if not self._service.may_act(channel, "predictions_enabled"):
            return "not allowed now"
        if event.get("status") != "ACTIVE":
            return "not active"
        balance = self._service.balances.get(channel.id)
        if balance is None:
            return "balance unknown"
        amount = PredictionStrategy.amount(balance, tw.settings.prediction_percent, tw.settings.prediction_max_points)
        if amount < MIN_BET:
            return "stake below 10 points"
        outcomes = event.get("outcomes")
        if not isinstance(outcomes, list) or not all(isinstance(o, dict) for o in outcomes):
            return "no outcomes"
        index = PredictionStrategy.choose(tw.settings.prediction_strategy, outcomes)
        if index is None:
            return "fewer than two outcomes"
        outcome_id = outcomes[index].get("id")
        if not isinstance(outcome_id, str) or not outcome_id:
            return "no outcome id"
        return outcome_id, index, amount

    async def bet(self, event_id: str) -> None:
        """Place the one bet of an event, or log why not; the event is handled either way."""
        entry = self._latest.get(event_id)
        if entry is None or event_id in self._handled:
            return
        channel, event = entry
        title = event.get("title") if isinstance(event.get("title"), str) else ""
        # used up before sending: a bet is never sent twice, also not after an unknown outcome
        remember(self._handled, event_id)
        choice = self._refusal(channel, event)
        if isinstance(choice, str):
            logger.info("No bet on %s (%s)", channel.name, choice)
            self._twitch.gui.points.log("bet_skipped", channel=channel.name, title=title)
            return
        outcome_id, index, amount = choice
        op = GQL_OPERATIONS["MakePrediction"].with_variables({"input": {
            "eventID": event_id, "outcomeID": outcome_id, "points": amount,
            "transactionID": secrets.token_hex(16),
        }})
        # sent once: it spends points
        response, code = await self._send(op, single_attempt=True)
        if response is not None:
            code = error_code(response, "makePrediction")
        if code is None:
            outcome = event["outcomes"][index].get("title")
            outcome = outcome if isinstance(outcome, str) else outcome_id
            remember(self._placed, event_id, (channel.name, title))
            logger.info("Bet %d points on %s", amount, channel.name)
            self._twitch.gui.points.log("bet_placed", points=amount, outcome=outcome, channel=channel.name, title=title)
            return
        logger.warning("Bet on %s was not accepted (%s)", channel.name, code)
        self._twitch.gui.points.log("bet_failed", channel=channel.name, code=code)

    @task_wrapper
    async def process_result(self, user_id: int, message: JsonType) -> None:
        """Log the result of a bet this service placed.

        Shape: {"type": "prediction-result", "data": {"prediction": {"event_id", "result":
        {"type": "WIN" | "LOSE" | "REFUND", "points_won": int | null}}}}
        """
        if not isinstance(message, dict) or message.get("type") != "prediction-result":
            return
        data = message.get("data")
        prediction = data.get("prediction") if isinstance(data, dict) else None
        result = prediction.get("result") if isinstance(prediction, dict) else None
        event_id = prediction.get("event_id") if isinstance(prediction, dict) else None
        if not isinstance(result, dict) or not isinstance(event_id, str) or event_id not in self._placed:
            return
        kind, won = result.get("type"), result.get("points_won")
        name, title = self._placed[event_id]
        if kind == "WIN" and type(won) is int:
            self._twitch.gui.points.log("bet_won", points=won, channel=name, title=title)
        elif kind == "LOSE":
            self._twitch.gui.points.log("bet_lost", channel=name, title=title)
        elif kind == "REFUND":
            self._twitch.gui.points.log("bet_refunded", channel=name, title=title)
        else:
            return
        logger.info("Prediction on %s ended: %s", name, kind)
        del self._placed[event_id]
