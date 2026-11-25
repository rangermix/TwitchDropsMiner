"""
Idle watch service: when nothing can be mined, watch the channels the owner lists.

Each live listed channel gets one streak visit per broadcast, otherwise the highest
live listed channel is watched for channel points.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import time
from dataclasses import dataclass
from itertools import zip_longest
from typing import TYPE_CHECKING

import aiohttp

from src.config import GQL_OPERATIONS, MAX_CHANNELS, GQLRawQuery, State
from src.exceptions import ExitRequest, GQLException, MinerException
from src.i18n import _
from src.models.channel import Channel
from src.utils import chunk, json_save, task_wrapper


if TYPE_CHECKING:
    from collections.abc import Sequence
    from pathlib import Path

    from src.config import GQLRequest, JsonType
    from src.core.client import Twitch


logger = logging.getLogger("TwitchDrops")

IDLE_VISIT_SECONDS = 420
# Twitch counts a stream for the watch streak only when it starts at least 30 minutes after the
# last one ended: a restart within that gap is the same stream
RESTART_SECONDS = 1800
IDLE_TICK_SECONDS = 30
STREAK_REASON = "WATCH_STREAK"
CLAIM_REASON = "CLAIM"
# tick retries of a failed bonus claim per watched channel; after them claim-available retries it
CLAIM_RETRIES = 2
STREAK_SHARE_CHECK_SECONDS = 300
STREAK_SHARE_ATTEMPTS = 2
STREAK_SHARE_TIMEOUT = 30
# visits kept (and saved); twice the idle list's 20-login cap, oldest dropped first
IDLE_VISITS_KEPT = 40
# share keys saved (used up, or refused with their try count), oldest dropped first; a streak's
# count rises once per stream
IDLE_SHARES_KEPT = 100
# raw queries with an operation name, which must match the name inside the query
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
        # visits_path keeps the finished visits and the share keys (used up, or refused with their try
        # count) across restarts, with the account they belong to (None: not known yet, taken by the
        # first account that idle watches)
        self._visits_path = visits_path
        self._account: int | None = None
        # broadcast id -> channel id of finished visits
        self.visited: dict[int, int] = {}
        # in memory only, for restarts: channel id -> (broadcast id, time, Channel) when pick() last saw it
        # live, Channel None once it was seen offline; channel id -> the broadcast whose visit got the
        # watch streak bonus.
        # shortcut: monotonic time stops while the host suspends, which shortens the gap; use wall time
        # if TDM must run on a host that sleeps
        self._seen: dict[int, tuple[int, float, Channel | None]] = {}
        self._finished: dict[int, int] = {}
        # watch streak sharing: attempts per (milestone id, count); a key with all attempts used
        # is done (shared, or sent with an unknown outcome)
        self._share_tries: dict[tuple[str, int | None], int] = {}
        # the file exists but could not be read or has a damaged share entry: it is never written
        # and no share is sent
        self._load_failed = False
        self._load()
        # what the file holds; only a successful write updates it, so a failed one is retried
        self._saved = self._payload()
        self._save_failed = False
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
        # the last claim on the watched channel failed; the tick reads its points again, at most
        # CLAIM_RETRIES times until a claim succeeds or the channel changes
        self._claim_failed = False
        self._claim_retries = 0
        # watch streak share check: last read, early-check request
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
            and tw.logged_in_user_id() is not None
            and bool(tw.settings.idle_channels)
        )

    def enter(self) -> None:
        if not self.active:
            # a pick handed to drops stays watched: until the loop picks again it is not idle watched
            self.watched = None
            self._claimed.clear()
            self._claim_failed = False
            self._claim_retries = 0
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
        """Forget the session's balances, claims and lookups on an auth change.

        Visits and share keys stay: they belong to an account, and the next idle entry drops them
        when another account logged in (see _use_account).
        """
        self.visit = None
        self._last_visit = None
        self._paused_at = None
        self.balances.clear()
        self._claimed.clear()
        self._claim_failed = False
        self._claim_retries = 0
        self._share_checked = None
        self._share_now = False
        self._skipped.clear()
        self._ban_ends.clear()
        self._seen_list = None

    def poke(self) -> None:
        self._wake.set()

    def _load(self) -> None:
        """Read the saved account, visits and share keys; a missing file is empty, bad visit entries are skipped.

        A file that cannot be read or parsed as a whole, or that has a damaged share entry, may hold used
        share keys: it is left as it is, its readable visits stay in memory and no share is sent for the
        rest of this run.
        """
        if self._visits_path is None:
            return
        account: int | None = None
        visits: dict[int, int] = {}
        tries: dict[tuple[str, int | None], int] = {}
        skipped, damaged = 0, None
        try:
            raw = json.loads(self._visits_path.read_text(encoding="utf8"))
            # newer fields without "visits" are not the old flat format: the file is damaged
            if not isinstance(raw, dict) or ("visits" not in raw and ("user_id" in raw or "shares" in raw)):
                raise ValueError
            # a file from before the account and shares were kept holds the visits alone
            account, saved, keys = (
                (raw.get("user_id"), raw["visits"], raw.get("shares", [])) if "visits" in raw else (None, raw, [])
            )
            if account is not None and (type(account) is not int or account <= 0):
                raise ValueError
            if not isinstance(saved, dict) or not isinstance(keys, list):
                raise ValueError
            for broadcast, channel_id in saved.items():
                if broadcast.isascii() and broadcast.isdigit() and type(channel_id) is int:
                    visits[int(broadcast)] = channel_id
                else:
                    skipped += 1
            for key in keys:
                # [id, count] is used up; [id, count, tries] was refused and has tries left
                if not (
                    type(key) is list and len(key) in (2, 3) and type(key[0]) is str and key[0]
                    and (key[1] is None or type(key[1]) is int)
                    and (len(key) == 2 or (type(key[2]) is int and 0 < key[2] < STREAK_SHARE_ATTEMPTS))
                ):
                    damaged = "damaged share entry"
                    break
                # a repeated key (a hand-edited or merged file) keeps its highest count
                k = (key[0], key[1])
                tries[k] = max(tries.get(k, 0), key[2] if len(key) == 3 else STREAK_SHARE_ATTEMPTS)
        except FileNotFoundError:
            return
        except (OSError, ValueError, RecursionError) as exc:
            # exception type only: the file is not echoed into the log
            account, visits, tries, skipped, damaged = None, {}, {}, 0, type(exc).__name__
        if skipped:
            logger.warning("Idle visits file: %d invalid entries skipped", skipped)
        if damaged is not None:
            self._load_failed = True
            logger.warning(
                "Idle visits file unreadable (%s): fix or delete it and restart; until then visits are not"
                " saved and no watch streak is shared", damaged,
            )
        self._account, self.visited, self._share_tries = account, visits, tries

    def _payload(self) -> dict[str, object]:
        # a refused key keeps its try count, so a restart gives it only the tries it has left
        keys = [
            [mid, value] if tries >= STREAK_SHARE_ATTEMPTS else [mid, value, tries]
            for (mid, value), tries in self._share_tries.items() if tries
        ]
        return {"user_id": self._account, "visits": dict(self.visited), "shares": keys[-IDLE_SHARES_KEPT:]}

    def _save(self) -> bool:
        """Write the account, visits and share keys when they differ from the last successful write.

        True when the file holds them (or there is no file).
        """
        if self._load_failed:
            return False
        payload = self._payload()
        if self._visits_path is None or payload == self._saved:
            return True
        try:
            json_save(self._visits_path, payload)
        except OSError as exc:
            # once per failing streak: the next tick tries again
            if not self._save_failed:
                logger.warning("Idle visits could not be saved (%s)", type(exc).__name__)
            self._save_failed = True
            return False
        self._saved, self._save_failed = payload, False
        return True

    def _use_account(self, account: int | None) -> None:
        """Drop the visits and share keys of another account than the one logged in."""
        if account is None or account == self._account:
            return
        if self._account is not None:
            self.visited.clear()
            self._share_tries.clear()
            self._seen.clear()
            self._finished.clear()
        self._account = account

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
        self._use_account(tw.logged_in_user_id())
        # each idle entry retries the logins the previous run gave up on
        self._skipped.clear()
        self._ban_ends.clear()
        if self._paused_at is not None and self.visit is not None:
            self.visit.started += time.monotonic() - self._paused_at
        self._paused_at = None
        while True:
            # cleared before the awaits, so a poke during the tick runs one more tick
            self._wake.clear()
            if self._hand_to_drops(tw.watching_channel.get_with_default(None)):
                return
            if not self.allowed():
                self._pause()
                tw.stop_watching()
                logger.info("Idle watching stopped")
                return
            try:
                if await self._tick():
                    return
            except ExitRequest:
                # shutting down: end the loop quietly, like task_wrapper does for other tasks
                return
            except Exception:
                # one bad tick must not end idle watching; CancelledError still propagates
                logger.exception("Idle watching step failed, retrying")
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(self._wake.wait(), IDLE_TICK_SECONDS)

    def _hand_to_drops(self, channel: Channel | None) -> bool:
        """Hand a channel drops can use to CHANNEL_SWITCH, which ends idle watching; True if handed over."""
        if channel is None or not self._twitch.can_watch(channel):
            return False
        # normal selection takes the channel over; a watched one is not stopped
        logger.info("Idle watching stopped: a drop channel can be watched")
        # the loop ends by itself, so stop() finds it done: the visit clock stops here
        self._pause()
        self._twitch.change_state(State.CHANNEL_SWITCH)
        return True

    async def _tick(self) -> bool:
        """One idle step; True when it handed the pick to drops and the loop must end."""
        tw = self._twitch
        try:
            await self.track()
        except ExitRequest:
            raise
        except (MinerException, aiohttp.ClientError, TimeoutError) as exc:
            # no response body: the exception type is enough to diagnose
            logger.warning("Idle channel lookup failed (%s), retrying", type(exc).__name__)
        channel = self.pick(self.live_channels(), time.monotonic())
        self._save()
        if self._hand_to_drops(channel):
            # no watch, balance read or claim on a channel drops can use
            return True
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
            # a re-watch of the same pick after a failed claim reads again only as a counted retry
            retry = channel is self.watched and self._claim_failed
            if channel is not self.watched:
                self._claimed.clear()
                self._claim_failed = False
                self._claim_retries = 0
            self.watched = channel
            tw.watch(channel, update_status=False)
            self._show_status(channel)
            if not retry or self._claim_retries < CLAIM_RETRIES:
                self._claim_retries += retry
                await self.refresh_points(channel)
        elif self._claim_failed and self._claim_retries < CLAIM_RETRIES:
            # the last bonus claim failed: a fresh read finds the bonus again, once per tick
            self._claim_retries += 1
            await self.refresh_points(channel)
        await self.share_streaks()
        return False

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
        # only the loop's own pick: at idle entry the last drop channel is still watched until the first pick
        if not self.active or watched is None or watched is not self.watched or str(watched.id) != str(channel_id):
            return None
        # a channel drops can use is mined, not idle watched, even before the loop hands it over
        return None if self._twitch.can_watch(watched) else watched

    async def _gql(
        self, op: GQLRequest | list[GQLRequest], name: str | None = None,
    ) -> JsonType | Sequence[JsonType | None] | None:
        """Send op (or a batch); failures are logged as one warning without the response body."""
        name = name or op["operationName"]  # type: ignore[index,call-overload]
        try:
            response = await (self._send_split(op) if isinstance(op, list) else self._twitch.gql_request(op))
        except ExitRequest:
            raise
        except (MinerException, aiohttp.ClientError, TimeoutError) as exc:
            logger.warning("Twitch request %s failed (%s)", name, type(exc).__name__)
            return None
        if isinstance(op, list):
            # a batch answers with a list; its entries are checked by the caller
            usable = isinstance(response, list)
            for answer in response:
                if answer is None:
                    # _send_split's mark for an entry its own GQL error failed
                    logger.warning("Twitch request %s failed (GQLException)", name)
        else:
            usable = isinstance(response, dict) and not response.get("errors")
        if usable:
            return response
        logger.warning("Twitch request %s returned errors", name)
        return None

    async def claim(self, channel: Channel, claim_id: str) -> None:
        tw = self._twitch
        if (
            not claim_id
            or not tw.settings.claim_channel_points
            # paused, outside mining hours, or a channel drops can use: no claim
            or not tw.is_mining_time()
            or tw.can_watch(channel)
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
        # a failed claim stays unclaimed: the next tick reads the points again and retries it
        self._claim_failed = True
        if response is None:
            return
        try:
            result = response["data"]["claimCommunityPoints"]
            error = result.get("error") if isinstance(result, dict) else True
        except (KeyError, TypeError):
            result, error = None, True
        claim = result.get("claim") if isinstance(result, dict) else None
        if error is not None or not isinstance(claim, dict) or not isinstance(claim.get("id"), str) or not claim["id"]:
            # an error code (for example an already claimed bonus) or no claim with an id instead of the claim
            code = error.get("code") if isinstance(error, dict) else error
            logger.warning(
                "Twitch request ClaimCommunityPoints was not accepted%s",
                f" ({code})" if isinstance(code, str) else "",
            )
            return
        self._claim_failed = False
        self._claim_retries = 0
        self._claimed.add(claim_id)
        logger.info("Claimed channel points bonus on %s", channel.name)

    async def refresh_points(self, channel: Channel) -> None:
        """Read the balance of a newly watched channel and claim a bonus that is already open."""
        if self._twitch.can_watch(channel):
            return
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
        # this read decides again whether a bonus is open
        self._claim_failed = False
        if isinstance(balance, int) and not isinstance(balance, bool):
            self.balances[channel.id] = balance
            self._show_status(channel)
        if isinstance(available, dict) and isinstance(available.get("id"), str):
            await self.claim(channel, available["id"])

    async def share_streaks(self) -> None:
        """At most every 5 minutes, or when a streak was just earned, share watch streaks Twitch allows."""
        now = time.monotonic()
        due = self._share_now or self._share_checked is None or now - self._share_checked >= STREAK_SHARE_CHECK_SECONDS
        if not self.active or not self._twitch.settings.share_watch_streaks or not due or self._load_failed:
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
            # can_watch is False while paused or outside mining hours too
            and tw.is_mining_time()
            and not tw.can_watch(channel)
        )

    async def _share(self, channel: Channel, milestone: JsonType) -> bool:
        """Send one share unless it is done, used up or no longer allowed; True when it was sent."""
        milestone_id = milestone["id"]
        # a streak can keep its id while its count rises: each count is its own share
        value = self._streak_value(milestone.get("value"))
        key = (milestone_id, value)
        tries = self._share_tries.get(key, 0)
        if self._share_done(milestone_id, value) or not self._may_share(channel):
            return False
        # Until a reply says Twitch refused it, the outcome is unknown and the share may be posted:
        # all attempts are used and saved before sending, so a failure, timeout, cancellation or
        # restart never resends it.
        self._share_tries[key] = STREAK_SHARE_ATTEMPTS
        if not self._save():
            # unrecorded, a restart could send it again: one lost share beats a double notice.
            # Nothing was sent, so a later check may try once the file can be written.
            if tries:
                self._share_tries[key] = tries
            else:
                del self._share_tries[key]
            logger.warning("Watch streak share on %s skipped: it could not be recorded", channel.name)
            return True
        op = GQLRawQuery(
            SHARE_MILESTONE_MUTATION,
            {"input": {"channelID": str(channel.id), "milestoneID": milestone_id}},
            SHARE_MILESTONE_OPERATION,
        )
        # Sent once: a resend could post a second notice in the streamer's chat. The imported
        # session does not replay it after an auth renewal either, so that share is lost, not
        # resent: one lost share beats a double notice.
        op.single_attempt = True
        response = await self._gql(op)
        if response is None:
            return True  # _gql logged it
        try:
            result = response["data"]["shareViewerMilestone"]  # type: ignore[index,call-overload]
        except (KeyError, TypeError):
            result = None
        error = result.get("error") if isinstance(result, dict) else None
        code = error.get("code") if isinstance(error, dict) else None
        if isinstance(code, str) and code and "milestone" in result and result["milestone"] is None:
            # an error code and no milestone: Twitch refused it, so one more try in a later check is safe
            self._share_tries[key] = tries + 1
            self._save()
            logger.warning("Watch streak share on %s was not accepted (%s)", channel.name, code)
            return True
        # anything but the shared milestone back is an unknown outcome: not resent, and not reported as shared
        shared = result.get("milestone") if isinstance(result, dict) and error is None else None
        if not isinstance(shared, dict) or shared.get("id") != milestone_id:
            logger.warning("Watch streak share on %s returned an unexpected reply", channel.name)
        elif value is None:
            logger.info("Shared watch streak on %s", channel.name)
        else:
            logger.info("Shared watch streak on %s (%s streams)", channel.name, value)
        return True

    @staticmethod
    def _streak_value(raw: object) -> int | None:
        """A streak count as one int (9, "9", " 9", "09" and 9.0 are one count); None when unusable."""
        if isinstance(raw, bool):
            return None
        if isinstance(raw, int):
            return raw
        if isinstance(raw, float):
            return int(raw) if raw.is_integer() else None
        if isinstance(raw, str) and (text := raw.strip()).isascii() and text.isdigit():
            return int(text)
        return None

    def _share_done(self, milestone_id: str, value: int | None) -> bool:
        """Whether this milestone count has used all its attempts.

        A count without a usable value cannot be told apart from the counts of the same id, so it
        matches them both ways: one lost share beats a second notice in the streamer's chat.
        """
        used = {v for (mid, v), tries in self._share_tries.items() if mid == milestone_id and tries >= STREAK_SHARE_ATTEMPTS}
        return value in used or (bool(used) and (value is None or None in used))

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
            await self._points_earned(data)
        elif msg_type == "claim-available":
            claim = data.get("claim")
            if not isinstance(claim, dict) or not isinstance(claim.get("id"), str) or not claim["id"]:
                return
            channel_id = self._channel_id(claim.get("channel_id"))
            if channel_id is not None and (channel := self._idle_watched(channel_id)) is not None:
                await self.claim(channel, claim["id"])

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
        broadcast_id = channel.broadcast_id
        assert broadcast_id is not None  # live channels are online
        return broadcast_id

    def _skip_restarts(self, live: list[Channel], ids: dict[int, int], now: float) -> None:
        """Record as visited a new broadcast that continues a stream whose visit got the streak bonus.

        Only the bonus proves the stream was counted: a full visit without it gives the restart a visit.
        The old broadcast must have been seen offline while its Channel was still tracked; one dropped by
        a cleanup while it still looked live (maybe after a lost stream-down) has an unknown end, and a
        new broadcast of a channel that never looked offline may be a new stream: both get a visit. The
        gap is counted from the last pick() that saw the old broadcast live.
        """
        visit = self.visit
        if visit is not None and visit.done:
            self._finished[visit.channel_id] = visit.broadcast_id
        tracked = self._twitch.channels
        for channel_id, (broadcast_id, seen_at, channel) in list(self._seen.items()):
            if channel is None or channel_id in ids:
                continue
            if tracked.get(channel_id) is channel:
                # shortcut: a stale "live" corrected on the same object (check_online() after a failed
                # watch or a title change) counts as seen offline; track the stream's end time if that matters
                self._seen[channel_id] = (broadcast_id, seen_at, None)  # seen offline
            else:
                del self._seen[channel_id]
        for channel in live:
            broadcast_id, seen = ids[channel.id], self._seen.get(channel.id)
            if (
                seen is not None
                and seen[2] is None
                and seen[0] != broadcast_id
                and self._finished.get(channel.id) == seen[0]
                and now - seen[1] < RESTART_SECONDS
            ):
                self.visited[broadcast_id] = channel.id
                self._finished[channel.id] = broadcast_id
                logger.info("Idle visit skipped: %s restarted its stream", channel.name)
            self._seen[channel.id] = (broadcast_id, now, channel)

    def pick(self, live: list[Channel], now: float) -> Channel | None:
        ids = {c.id: self._broadcast_id(c) for c in live}
        self._skip_restarts(live, ids, now)
        visit = self.visit
        if visit is not None:
            if (
                ids.get(visit.channel_id) == visit.broadcast_id
                and not visit.done
                and now - visit.started < IDLE_VISIT_SECONDS
            ):
                return next(c for c in live if c.id == visit.channel_id)
            # a channel missing from live (offline, or a failed lookup) cut the visit short: not recorded,
            # so the same stream gets its full visit once it is tracked again
            if visit.done or now - visit.started >= IDLE_VISIT_SECONDS or visit.channel_id in ids:
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

    async def _send_split(self, ops: list[GQLRequest]) -> Sequence[JsonType | None]:
        """Answers for a batch, in order; a GQL error in a batch is narrowed to the entries that cause it (None)."""
        try:
            response = await self._twitch.gql_request(ops)
        except GQLException:
            # gql_request raises for the whole batch when one answer has an unrecognised error
            if len(ops) == 1:
                raise
            answers: list[JsonType | None] = []
            for op in ops:
                try:
                    answers.extend(await self._send_split([op]))
                except GQLException:
                    answers.append(None)  # the caller logs the skip
            return answers
        return response if isinstance(response, list) else [response]

    async def _lookup(self, batch: list[str]) -> Sequence[JsonType | None]:
        ops: list[GQLRequest] = [GQL_OPERATIONS["GetStreamInfo"].with_variables({"channel": n}) for n in batch]
        return await self._send_split(ops)

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
        for channel in new:
            twitch.channels[channel.id] = channel
            twitch.gui.channels.display(channel, add=True)
        twitch._add_channel_topics(new)
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
