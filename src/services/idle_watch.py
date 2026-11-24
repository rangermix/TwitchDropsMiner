"""
Idle watch service: when nothing can be mined, watch the channels the owner lists.

Each live listed channel gets one streak visit per broadcast, otherwise the highest
live listed channel is watched for channel points.
"""

from __future__ import annotations

import asyncio
import contextlib
import copy
import json
import logging
import time
from dataclasses import dataclass
from itertools import zip_longest
from typing import TYPE_CHECKING

import aiohttp

from src.config import GQL_OPERATIONS, MAX_CHANNELS, GQLRawQuery, State, WebsocketTopic
from src.exceptions import ExitRequest, GQLException, MinerException
from src.i18n import _
from src.models.channel import Channel
from src.utils import chunk, json_save, task_wrapper


if TYPE_CHECKING:
    from collections.abc import Sequence
    from pathlib import Path

    from src.config import GQLOperation, GQLQuery, GQLRequest, JsonType
    from src.core.client import Twitch


logger = logging.getLogger("TwitchDrops")

IDLE_VISIT_SECONDS = 420
IDLE_TICK_SECONDS = 30
STREAK_REASON = "WATCH_STREAK"
CLAIM_REASON = "CLAIM"
STREAK_SHARE_CHECK_SECONDS = 300
STREAK_SHARE_ATTEMPTS = 2
STREAK_SHARE_TIMEOUT = 30
# visits kept (and saved); twice the idle list's 20-login cap, oldest dropped first
IDLE_VISITS_KEPT = 40
# the design doc's strings plus an operation name; the share mutation is unverified until seen live
MILESTONES_OPERATION = "ViewerMilestones"
MILESTONES_QUERY = (
    "query ViewerMilestones($login:String!){ user(login:$login){ channel{ id self{ viewerMilestones{ id category value state shareStatus }}}}}"
)
SHARE_MILESTONE_OPERATION = "ShareViewerMilestone"
SHARE_MILESTONE_MUTATION = (
    "mutation ShareViewerMilestone($input: ShareViewerMilestoneInput!) { shareViewerMilestone(input: $input) { milestone { id } error { code } } }"
)


@dataclass
class Visit:
    channel_id: int
    broadcast_id: int
    started: float
    done: bool = False


class IdleWatchService:
    def __init__(self, twitch: Twitch, visits_path: Path | None = None) -> None:
        self._twitch = twitch
        # broadcast id -> channel id of finished visits, kept across restarts in visits_path
        self._visits_path = visits_path
        self.visited: dict[int, int] = self._load_visits()
        self._saved_visits = dict(self.visited)
        self.visit: Visit | None = None
        # when the loop last stopped, so a visit does not count the time spent mining drops
        self._paused_at: float | None = None
        self._task: asyncio.Task[None] | None = None
        self._wake = asyncio.Event()
        # logins track() gave up on (unknown, banned, over the cap) for this idle run
        self._skipped: set[str] = set()
        # skipped logins whose ban ends: login -> expiry (epoch seconds), retried once it has passed
        self._ban_ends: dict[str, float] = {}
        self._seen_list: list[str] | None = None
        self.balances: dict[int, int] = {}
        # claim ids claimed on the channel being watched; cleared when it changes
        self._claimed: set[str] = set()
        # claim ids with a request in flight, so a repeated message does not send twice
        self._claiming: set[str] = set()
        self._raw_logged = False
        # watch streak sharing: (milestone id, value) shared this run, attempts per key, last read, early-check request
        self._shared: set[tuple[str, str | None]] = set()
        self._share_tries: dict[tuple[str, str | None], int] = {}
        self._share_checked: float | None = None
        self._share_now = False
        # the channel the loop chose to watch, and the visit it last announced
        self.watched: Channel | None = None
        self._last_visit: Visit | None = None

    @property
    def active(self) -> bool:
        return self._task is not None and not self._task.done()

    def allowed(self) -> bool:
        tw = self._twitch
        return (
            tw.is_mining_time()
            and tw._auth_state._logged_in.is_set()
            and bool(tw.settings.idle_channels)
        )

    def enter(self) -> None:
        if not self.active:
            self._task = asyncio.create_task(self._run())

    def stop(self) -> asyncio.Task[None] | None:
        """Cancel the loop and return its task, so a caller can await the cancellation."""
        task, self._task = self._task, None
        if task is not None and not task.done():
            self._pause()
            task.cancel()
        return task

    def _pause(self) -> None:
        # the first stop wins: later stop() calls while already down must not shrink the gap
        if self._paused_at is None:
            self._paused_at = time.monotonic()

    def reset(self) -> None:
        """Forget the previous account's visits, balances, claims and lookups."""
        self.visited.clear()
        self._saved_visits = {}
        if self._visits_path is not None:
            try:
                self._visits_path.unlink(missing_ok=True)
            except OSError as exc:
                logger.warning("Idle visits file could not be removed (%s)", type(exc).__name__)
        self.visit = None
        self._last_visit = None
        self._paused_at = None
        self.balances.clear()
        self._claimed.clear()
        self._shared.clear()
        self._share_tries.clear()
        self._share_checked = None
        self._share_now = False
        self._skipped.clear()
        self._ban_ends.clear()
        self._seen_list = None

    def poke(self) -> None:
        self._wake.set()

    def _load_visits(self) -> dict[int, int]:
        """The saved visits; a missing file is empty, an unreadable one is empty with a warning."""
        if self._visits_path is None:
            return {}
        try:
            raw = json.loads(self._visits_path.read_text(encoding="utf8"))
            if not isinstance(raw, dict):
                raise ValueError
            visits = {}
            for broadcast, channel_id in raw.items():
                if not (broadcast.isascii() and broadcast.isdigit()) or type(channel_id) is not int:
                    raise ValueError
                visits[int(broadcast)] = channel_id
        except FileNotFoundError:
            return {}
        except (OSError, ValueError) as exc:
            # exception type only: the file is not echoed into the log
            logger.warning("Idle visits file unreadable (%s), starting empty", type(exc).__name__)
            return {}
        return visits

    def _save_visits(self) -> None:
        """Write the visits when they changed since the last write (or failed write)."""
        if self._visits_path is None or self.visited == self._saved_visits:
            return
        self._saved_visits = dict(self.visited)
        try:
            json_save(self._visits_path, self.visited)
        except OSError as exc:
            logger.warning("Idle visits could not be saved (%s)", type(exc).__name__)

    def show_status(self) -> None:
        """Re-show "Idle watching <channel>" while the loop's channel is still watched."""
        if self.active and self.watched is not None and self.watched is self._twitch.watching_channel.get_with_default(None):
            self._show_status(self.watched)

    async def _run(self) -> None:
        tw = self._twitch
        logger.info("Idle watching started")
        # the last drop's progress panel does not belong to an idle channel
        tw.gui.clear_drop()
        self.watched = None
        # each idle entry retries the logins the previous run gave up on
        self._skipped.clear()
        self._ban_ends.clear()
        if self._paused_at is not None and self.visit is not None:
            self.visit.started += time.monotonic() - self._paused_at
        self._paused_at = None
        while True:
            # cleared before the awaits, so a poke during the tick runs one more tick
            self._wake.clear()
            watching = tw.watching_channel.get_with_default(None)
            if watching is not None and tw.can_watch(watching):
                # Drops won: normal selection takes the channel over; the watch is not stopped.
                logger.info("Idle watching stopped: a drop channel can be watched")
                tw.change_state(State.CHANNEL_SWITCH)
                return
            if not self.allowed():
                self._pause()
                tw.stop_watching()
                logger.info("Idle watching stopped")
                return
            try:
                await self._tick()
            except ExitRequest:
                # shutting down: end the loop quietly, like task_wrapper does for other tasks
                return
            except Exception:
                # one bad tick must not end idle watching; CancelledError still propagates
                logger.exception("Idle watching step failed, retrying")
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(self._wake.wait(), IDLE_TICK_SECONDS)

    async def _tick(self) -> None:
        tw = self._twitch
        try:
            await self.track()
        except ExitRequest:
            raise
        except (MinerException, aiohttp.ClientError, TimeoutError) as exc:
            # no response body: the exception type is enough to diagnose
            logger.warning("Idle channel lookup failed (%s), retrying", type(exc).__name__)
        channel = self.pick(self.live_channels(), time.monotonic())
        self._save_visits()
        if self.visit is not self._last_visit:
            if self._last_visit is not None:
                logger.info("Idle visit ended: broadcast %s", self._last_visit.broadcast_id)
            if self.visit is not None:
                logger.info("Idle visit started: %s", channel.name if channel else "?")
            self._last_visit = self.visit
        if channel is None:
            self.watched = None
            tw.stop_watching()
        elif channel is not tw.watching_channel.get_with_default(None) or channel is not self.watched:
            if self.watched is not None:
                logger.info("Idle watching switched to %s", channel.name)
            if channel is not self.watched:
                self._claimed.clear()
            self.watched = channel
            tw.watch(channel, update_status=False)
            self._show_status(channel)
            await self.refresh_points(channel)
        await self.share_streaks()

    def _show_status(self, channel: Channel) -> None:
        points = self.balances.get(channel.id)
        if points is None:
            text = _.t["status"]["idle_watching"].format(channel=channel.name)
        else:
            text = _.t["status"]["idle_watching_points"].format(channel=channel.name, points=points)
        self._twitch.gui.status.update(text)

    def _idle_watched(self, channel_id: object) -> Channel | None:
        """The channel being idle watched, if channel_id is it and the loop is running."""
        watched = self._twitch.watching_channel.get_with_default(None)
        if not self.active or watched is None or str(watched.id) != str(channel_id):
            return None
        # a channel drops can use is mined, not idle watched, even before the loop hands it over
        return None if self._twitch.can_watch(watched) else watched

    async def _gql(
        self, op: GQLRequest | list[GQLRequest], name: str | None = None, *, retry: bool = True,
    ) -> JsonType | Sequence[JsonType | None] | None:
        """Send op (or a batch); failures are logged as one warning without the response body."""
        name = name or op["operationName"]  # type: ignore[index,call-overload]
        try:
            response = await self._twitch.gql_request(op, retry=retry)
        except ExitRequest:
            raise
        except GQLException as exc:
            if isinstance(op, list) and len(op) > 1:
                # one unrecognised error in one entry fails the whole batch: send each entry on its own
                singles = [await self._gql([o], name, retry=retry) for o in op]
                return [a[0] if isinstance(a, list) and a else None for a in singles]
            logger.warning("Twitch request %s failed (%s)", name, type(exc).__name__)
            return None
        except (MinerException, aiohttp.ClientError, TimeoutError) as exc:
            logger.warning("Twitch request %s failed (%s)", name, type(exc).__name__)
            return None
        if isinstance(op, list):
            # a batch answers with a list; its entries are checked by the caller
            usable = isinstance(response, list)
        else:
            usable = isinstance(response, dict) and not response.get("errors")
        if usable:
            return response
        logger.warning("Twitch request %s returned errors", name)
        return None

    async def claim(self, channel: Channel, claim_id: str) -> None:
        if (
            not claim_id
            or not self._twitch.settings.claim_channel_points
            or claim_id in self._claimed
            or claim_id in self._claiming
        ):
            return
        self._claiming.add(claim_id)
        try:
            response = await self._gql(GQL_OPERATIONS["ClaimCommunityPoints"].with_variables(
                {"input": {"claimID": claim_id, "channelID": str(channel.id)}},
            ))
        finally:
            self._claiming.discard(claim_id)
        # a failed request stays unclaimed, so the next claim-available or refresh retries it
        if response is None:
            return
        try:
            result = response["data"]["claimCommunityPoints"]
            error = result.get("error") if isinstance(result, dict) else True
        except (KeyError, TypeError):
            result, error = None, True
        if not isinstance(result, dict) or error is not None:
            # the reply carries an error code (for example an already claimed bonus) instead of the claim
            code = error.get("code") if isinstance(error, dict) else error
            logger.warning(
                "Twitch request ClaimCommunityPoints was not accepted%s",
                f" ({code})" if isinstance(code, str) else "",
            )
            return
        self._claimed.add(claim_id)
        logger.info("Claimed channel points bonus on %s", channel.name)

    async def refresh_points(self, channel: Channel) -> None:
        """Read the balance of a newly watched channel and claim a bonus that is already open."""
        try:
            response = await asyncio.wait_for(
                self._gql(GQL_OPERATIONS["ChannelPointsContext"].with_variables({"channelLogin": channel.login})),
                15,
            )
        except TimeoutError:
            logger.warning("Twitch request ChannelPointsContext timed out")
            return
        try:
            points = response["data"]["community"]["channel"]["self"]["communityPoints"]  # type: ignore[index]
            balance, available = points["balance"], points["availableClaim"]
        except (KeyError, TypeError):
            return
        if isinstance(balance, int) and not isinstance(balance, bool):
            self.balances[channel.id] = balance
            self._show_status(channel)
        if isinstance(available, dict) and isinstance(available.get("id"), str):
            await self.claim(channel, available["id"])

    async def share_streaks(self) -> None:
        """At most every 5 minutes, or when a streak was just earned, share watch streaks Twitch allows."""
        now = time.monotonic()
        due = self._share_now or self._share_checked is None or now - self._share_checked >= STREAK_SHARE_CHECK_SECONDS
        if not self.active or not self._twitch.settings.share_watch_streaks or not due:
            return
        live = self.live_channels()
        if not live:
            # an early-check request waits for a check that runs
            return
        self._share_now = False
        self._share_checked = now
        try:
            await asyncio.wait_for(self._share_streaks(live), STREAK_SHARE_TIMEOUT)
        except TimeoutError:
            logger.warning("Watch streak share check timed out")

    async def _share_streaks(self, live: list[Channel]) -> None:
        ops: list[GQLRequest] = [
            GQLRawQuery(MILESTONES_QUERY, {"login": c.login}, MILESTONES_OPERATION) for c in live
        ]
        response = await self._gql(ops, MILESTONES_OPERATION)
        answers = response if isinstance(response, list) else []
        for channel, answer in zip(live, answers, strict=False):
            # at most one share sent per channel per check; the rest wait for the next check
            for milestone in self._shareable(answer, channel):
                if await self._share(channel, milestone):
                    break

    @staticmethod
    def _shareable(answer: object, channel: Channel) -> list[JsonType]:
        """The WATCH_STREAK milestones of channel's answer that are active and can be shared."""
        try:
            answered = answer["data"]["user"]["channel"]  # type: ignore[index,call-overload]
            rows = answered["self"]["viewerMilestones"]
            # answers are paired with channels by position; one for another channel is skipped
            same = answered["id"] == str(channel.id)
        except (KeyError, TypeError):
            return []
        if not same or not isinstance(rows, list) or answer.get("errors"):  # type: ignore[attr-defined]
            return []
        return [
            r for r in rows
            if isinstance(r, dict)
            and r.get("category") == STREAK_REASON
            and r.get("state") == "ACTIVE"
            and r.get("shareStatus") == "CAN_SHARE"
            and isinstance(r.get("id"), str)
            and r["id"]
        ]

    def _may_share(self, channel: Channel) -> bool:
        """The check's guards, again right before a mutation: the read and earlier shares take time."""
        tw = self._twitch
        return (
            self.active
            and tw.settings.share_watch_streaks
            and channel in self.live_channels()
            and not tw.can_watch(channel)
        )

    async def _share(self, channel: Channel, milestone: JsonType) -> bool:
        """Send one share unless it is done, used up or no longer allowed; True when it was sent."""
        milestone_id, raw_value = milestone["id"], milestone.get("value")
        # a streak can keep its id while its count rises: each value is its own share; 9 and "9" are one
        value = str(raw_value) if isinstance(raw_value, (str, int)) and not isinstance(raw_value, bool) else None
        key = (milestone_id, value)
        tries = self._share_tries.get(key, 0)
        if key in self._shared or tries >= STREAK_SHARE_ATTEMPTS or not self._may_share(channel):
            return False
        # Until a reply says Twitch refused it, the outcome is unknown and the share may be posted:
        # all attempts are used before sending, so a failure, timeout or cancellation is never resent.
        self._share_tries[key] = STREAK_SHARE_ATTEMPTS
        # sent once (retry=False): a resend could post a second notice in the streamer's chat
        response = await self._gql(
            GQLRawQuery(
                SHARE_MILESTONE_MUTATION,
                {"input": {"channelID": str(channel.id), "milestoneID": milestone_id}},
                SHARE_MILESTONE_OPERATION,
            ),
            retry=False,
        )
        if response is None:
            return True  # _gql logged it
        try:
            result = response["data"]["shareViewerMilestone"]  # type: ignore[index,call-overload]
        except (KeyError, TypeError):
            result = None
        error = result.get("error") if isinstance(result, dict) else None
        if isinstance(error, dict):
            # an explicit error object: Twitch refused it, so one more try in a later check is safe
            self._share_tries[key] = tries + 1
            code = error.get("code")
            logger.warning(
                "Watch streak share on %s was not accepted%s", channel.name, f" ({code})" if isinstance(code, str) else "",
            )
            return True
        # anything but the shared milestone back is an unknown outcome: not resent, and not reported as shared
        self._shared.add(key)
        shared = result.get("milestone") if isinstance(result, dict) and error is None else None
        if not isinstance(shared, dict) or shared.get("id") != milestone_id:
            logger.warning("Watch streak share on %s returned an unexpected reply", channel.name)
        elif value is None:
            logger.info("Shared watch streak on %s", channel.name)
        else:
            logger.info("Shared watch streak on %s (%s streams)", channel.name, value)
        return True

    @task_wrapper
    async def process_points(self, user_id: int, message: JsonType) -> None:
        """
        Handle community-points websocket messages while idle watching.

        Shapes: {"type": "points-earned", "data": {"channel_id": "1", "point_gain": {"reason_code": "WATCH",
        "total_points": 10}, "balance": {"balance": 1510}}} and
        {"type": "claim-available", "data": {"claim": {"id": "x", "channel_id": "1"}}}
        """
        msg_type = message.get("type") if isinstance(message, dict) else None
        data = message.get("data") if isinstance(message, dict) else None
        if not isinstance(data, dict):
            return
        if msg_type == "points-earned":
            self._log_raw(message)
            await self._points_earned(data)
        elif msg_type == "claim-available":
            claim = data.get("claim")
            if not isinstance(claim, dict) or not isinstance(claim.get("id"), str) or not claim["id"]:
                return
            channel_id = self._channel_id(claim.get("channel_id"))
            if channel_id is not None and (channel := self._idle_watched(channel_id)) is not None:
                await self.claim(channel, claim["id"])

    def _log_raw(self, message: JsonType) -> None:
        if self._raw_logged:
            return
        self._raw_logged = True
        raw = copy.deepcopy(message)
        balance = raw["data"].get("balance")
        if isinstance(balance, dict):
            balance.pop("balance", None)
        logger.debug("Raw points-earned message: %s", raw)

    @staticmethod
    def _channel_id(value: object) -> str | None:
        """A message's channel id as a digit string; Twitch may send it as a number."""
        if isinstance(value, int) and not isinstance(value, bool):
            value = str(value)
        return value if isinstance(value, str) and value.isascii() and value.isdigit() else None

    async def _points_earned(self, data: JsonType) -> None:
        channel_id = self._channel_id(data.get("channel_id"))
        if not self.active or channel_id is None:
            return
        gain, balance = data.get("point_gain"), data.get("balance")
        reason = gain.get("reason_code") if isinstance(gain, dict) else None
        watched = self._idle_watched(channel_id)
        listed = {c.login for c in self._twitch.channels.values() if str(c.id) == channel_id}
        points = balance.get("balance") if isinstance(balance, dict) else None
        if isinstance(points, int) and not isinstance(points, bool) and (
            watched is not None or listed & set(self._twitch.settings.idle_channels)
        ):
            self.balances[int(channel_id)] = points
            if watched is not None:
                self._show_status(watched)
        wake = False
        if reason == STREAK_REASON and listed & set(self._twitch.settings.idle_channels):
            # the next tick runs the share check; no network work in the websocket handler
            self._share_now = wake = True
        visit = self.visit
        if reason == STREAK_REASON and visit is not None and str(visit.channel_id) == channel_id:
            visit.done = wake = True
        if wake:
            self.poke()
        if reason == CLAIM_REASON and watched is not None:
            # claim() already logged the INFO line for this bonus
            logger.debug("Channel points bonus on %s: +%s", watched.name, gain.get("total_points"))  # type: ignore[union-attr]

    @staticmethod
    def _broadcast_id(channel: Channel) -> int:
        stream = channel._stream
        assert stream is not None  # live channels are online
        return stream.broadcast_id

    def pick(self, live: list[Channel], now: float) -> Channel | None:
        ids = {c.id: self._broadcast_id(c) for c in live}
        visit = self.visit
        if visit is not None:
            if (
                ids.get(visit.channel_id) == visit.broadcast_id
                and not visit.done
                and now - visit.started < IDLE_VISIT_SECONDS
            ):
                return next(c for c in live if c.id == visit.channel_id)
            self.visited[visit.broadcast_id] = visit.channel_id
            self.visit = None
        # A visit is forgotten only when its channel is live with another broadcast, or is
        # tracked but no longer listed; a channel missing for a tick (or untracked after a
        # cleanup) keeps its visit, so it is not visited twice in one stream.
        listed = set(self._twitch.settings.idle_channels)
        unlisted = {c.id for c in self._twitch.channels.values() if c.login not in listed}
        kept = [(b, cid) for b, cid in self.visited.items() if cid not in unlisted and ids.get(cid, b) == b]
        self.visited = dict(kept[-IDLE_VISITS_KEPT:])
        for channel in live:
            if ids[channel.id] not in self.visited:
                self.visit = Visit(channel.id, ids[channel.id], now)
                return channel
        return live[0] if live else None

    def live_channels(self) -> list[Channel]:
        by_login = {c.login: c for c in self._twitch.channels.values()}
        channels = (by_login.get(login) for login in self._twitch.settings.idle_channels)
        return [c for c in channels if c is not None and c.online and not c.banned]

    async def _lookup(self, batch: list[str]) -> Sequence[JsonType | None]:
        """Answers for batch, in order; a hard GQL error in a batch is narrowed to the logins that cause it."""
        ops: list[GQLOperation | GQLQuery | GQLRawQuery] = [
            GQL_OPERATIONS["GetStreamInfo"].with_variables({"channel": n}) for n in batch
        ]
        try:
            response = await self._twitch.gql_request(ops)
        except GQLException:
            # gql_request raises for the whole batch when one answer has an unrecognised error
            if len(batch) == 1:
                raise
            answers: list[JsonType | None] = []
            for login in batch:
                try:
                    answers.extend(await self._lookup([login]))
                except GQLException:
                    answers.append(None)  # the caller logs the skip
            return answers
        return response if isinstance(response, list) else [response]

    async def track(self) -> list[Channel]:
        """Look up listed logins not tracked yet and add them; whole-request failures propagate."""
        twitch = self._twitch
        logins = list(self._twitch.settings.idle_channels)
        if logins != self._seen_list:
            self._seen_list = logins
            self._skipped.clear()
            self._ban_ends.clear()
        now = time.time()
        for login in [login for login, end in self._ban_ends.items() if end <= now]:
            del self._ban_ends[login]
            self._skipped.discard(login)
        known = {c.login for c in twitch.channels.values()}
        missing = [login for login in logins if login not in known and login not in self._skipped]
        new: list[Channel] = []
        resolved: dict[str, Channel] = {}
        for batch in chunk(missing, 20):
            answers = await self._lookup(batch)
            # a login a short reply did not answer is skipped like an unknown one
            for login, answer in zip_longest(batch, answers[: len(batch)]):
                data = answer.get("data") if isinstance(answer, dict) else None
                user = data.get("user") if isinstance(data, dict) else None
                user_id = self._channel_id(user.get("id")) if isinstance(user, dict) else None
                if not isinstance(user, dict) or user_id is None or answer.get("errors"):  # type: ignore[union-attr]
                    logger.warning("Idle channel %s skipped: lookup failed or unknown", login)
                    self._skipped.add(login)
                    continue
                channel_id = int(user_id)
                channel = twitch.channels.get(channel_id) or next(
                    (c for c in new if c.id == channel_id), None,
                )
                if channel is None:
                    channel = Channel(twitch, id=channel_id, login=login)
                    if user.get("stream"):
                        channel.external_update(user, [])
                    new.append(channel)
                resolved[login] = channel
        room = max(MAX_CHANNELS - len(twitch.channels), 0)
        if len(new) > room:
            # count only, no logins; they stay skipped for this idle run
            logger.warning("%d idle channels skipped: tracked channel limit reached", len(new) - room)
            new = new[:room]
        banned: set[int] = set()
        if new:
            allowed = await twitch._channel_service.filter_banned_channels(new)
            banned = {c.id for c in new} - {c.id for c in allowed}
            new = allowed
        topics: list[WebsocketTopic] = []
        handlers = twitch._message_handler_service
        for channel in new:
            twitch.channels[channel.id] = channel
            twitch.gui.channels.display(channel, add=True)
            topics.append(WebsocketTopic(
                "Channel", "StreamState", channel.id, handlers.process_stream_state,
            ))
            topics.append(WebsocketTopic(
                "Channel", "StreamUpdate", channel.id, handlers.process_stream_update,
            ))
        if topics:
            twitch.websocket.add_topics(topics)
        by_login = {c.login: c for c in twitch.channels.values()} | resolved
        for login in logins:
            channel = by_login.get(login)
            if channel is not None and (channel.id in banned or channel.banned) and login not in self._skipped:
                logger.warning("Idle channel %s skipped: banned", login)
                self._skipped.add(login)
                if channel.ban_expires_at is not None:
                    self._ban_ends[login] = channel.ban_expires_at
        self._skipped |= {login for login, c in resolved.items() if c.id not in twitch.channels}
        return [c for login in logins if (c := by_login.get(login)) is not None and c.id in twitch.channels]
