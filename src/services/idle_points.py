"""Channel points jobs of idle watching: the watch order of listed channels and the periodic points read."""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Any

from src.config import GQL_OPERATIONS


if TYPE_CHECKING:
    from collections.abc import Collection, Mapping

    from src.config import GQLRequest
    from src.core.client import Twitch
    from src.models.channel import Channel
    from src.services.idle_watch import IdleWatchService


logger = logging.getLogger("TwitchDrops")

POINTS_READ_SECONDS = 300
# points_low / points_high: the watched channel stays until another live channel's balance beats it by more
SWITCH_MARGIN = 1000
# a batch that hits a GQL error is split and resent per channel, so it gets more time than one read
POINTS_READ_TIMEOUT = 30


class WatchPriority:
    """Order live listed channels for streak visits and the after-visit pick (setting `idle_priority`)."""

    @staticmethod
    def order(
        live: list[Channel], mode: str, balances: Mapping[int, int], multipliers: Mapping[int, tuple[float, ...]],
        favorites: Collection[str] = (),
    ) -> list[Channel]:
        """live in priority order, favorites (logins) first; the sorts are stable, so ties keep list order.

        points_low / points_high: known balances ascending / descending, unknown balances last.
        subscribed: channels with active multipliers first, by multiplier sum, then the rest in list order.
        """
        if mode == "points_low":
            ordered = sorted(live, key=lambda c: (c.id not in balances, balances.get(c.id, 0)))
        elif mode == "points_high":
            ordered = sorted(live, key=lambda c: (c.id not in balances, -balances.get(c.id, 0)))
        elif mode == "subscribed":
            ordered = sorted(live, key=lambda c: -sum(multipliers.get(c.id, ())))
        else:
            ordered = list(live)
        return sorted(ordered, key=lambda c: c.login not in favorites) if favorites else ordered

    @staticmethod
    def stayable(
        order: list[Channel], options: Mapping[str, Mapping[str, Any]], balances: Mapping[int, int],
    ) -> list[Channel]:
        """The channels the after-visit pick may stay on: not streak_only, balance below points_limit.

        An unknown balance counts as below the limit.
        """
        def stays(channel: Channel) -> bool:
            item = options.get(channel.login, {})
            limit = item.get("points_limit")
            return not item.get("streak_only") and not (
                limit is not None and channel.id in balances and balances[channel.id] >= limit
            )

        return [c for c in order if stays(c)]

    @staticmethod
    def first(order: list[Channel], current: Channel | None, mode: str, balances: Mapping[int, int]) -> Channel | None:
        """The channel to watch after the visits: order[0], or current while it stays in order.

        current stays only with points_low / points_high, while both balances are known and order[0]'s
        beats current's by at most SWITCH_MARGIN, so balances that pass each other do not switch the
        watch back and forth on every tick.
        """
        if not order:
            return None
        best = order[0]
        kept = next((c for c in order if c == current), None)
        if mode in ("points_low", "points_high") and kept is not None and kept.id in balances and best.id in balances:
            lead = balances[kept.id] - balances[best.id]
            if (lead if mode == "points_low" else -lead) <= SWITCH_MARGIN:
                return kept
        return best


class PointsReader:
    """Every POINTS_READ_SECONDS, read ChannelPointsContext of the other live listed channels in one batch.

    Each answer updates the balance and multipliers and claims an open bonus (IdleWatchService.apply_points).
    A failed or partial batch is not retried before the next read.
    """

    def __init__(self, twitch: Twitch, service: IdleWatchService) -> None:
        self._twitch = twitch
        self._service = service
        self._read_at: float | None = None

    def reset(self) -> None:
        self._read_at = None

    async def read_due(self, live: list[Channel], now: float) -> None:
        if not self._service.active or self._service.points_paused:
            return
        # no read on a channel drops can use; the watched channel has its own reads and claim retries
        channels = [c for c in live if c is not self._service.watched and not self._twitch.can_watch(c)]
        if not channels or (self._read_at is not None and now - self._read_at < POINTS_READ_SECONDS):
            return
        self._read_at = now
        ops: list[GQLRequest] = [
            GQL_OPERATIONS["ChannelPointsContext"].with_variables({"channelLogin": c.login}) for c in channels
        ]
        try:
            response = await asyncio.wait_for(self._service.gql(ops, "ChannelPointsContext"), POINTS_READ_TIMEOUT)
        except TimeoutError:
            logger.warning("Twitch request ChannelPointsContext timed out")
            return
        answers = response if isinstance(response, list) else []
        # a short reply leaves the last channels for the next read
        for channel, answer in zip(channels, answers, strict=False):
            await self._service.apply_points(channel, answer)
