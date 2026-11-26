"""
Idle watch service: watch the channels the owner lists, when nothing can be mined (main mode)
or, with watch_slots 2, on a second watch next to drop mining (side mode).

Each live listed channel gets one streak visit per broadcast (and up to RECOVERY_VISITS more
when a full visit got no streak bonus), otherwise the first live listed channel in priority
order is watched for channel points.
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

from src.api.gql_client import private_gql_discovery
from src.config import (
    GQL_OPERATIONS,
    MAX_CHANNELS,
    MAX_TOPICS,
    TOPICS_PER_CHANNEL,
    GQLRawQuery,
    State,
)
from src.config.settings import ChannelOptionSettings
from src.exceptions import ExitRequest, GQLException, MinerException
from src.i18n import _
from src.models.channel import READ_ERRORS, STREAM_CHECK_TIMEOUT, Channel
from src.services.chat_presence import ChatPresence
from src.services.idle_points import PointsReader, WatchPriority
from src.services.points_common import error_code
from src.services.points_events import PointsEvents
from src.services.points_history import PointsHistory
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
# the side watcher's poll, like WatchService.watch_loop: inside Twitch's rolling segment window
SIDE_WATCH_SECONDS = 10
# broadcasts whose AvailableDrops answer side mode keeps (20 listed channels, each with a restart)
SIDE_DROPS_KEPT = 40
# an answer is read again after this long (a campaign may start mid-broadcast); a failed read waits
SIDE_DROPS_REFRESH_SECONDS = 900
SIDE_DROPS_RETRY_SECONDS = 120
# drop mining rebuilds its channel list here: side mode pauses, so no track() adds channels meanwhile
SIDE_PAUSE_STATES = frozenset({State.GAMES_UPDATE, State.CHANNELS_CLEANUP, State.CHANNELS_FETCH})
STREAK_REASON = "WATCH_STREAK"
CLAIM_REASON = "CLAIM"
# tick retries of a failed bonus claim per watched channel; after them claim-available retries it
CLAIM_RETRIES = 2
STREAK_SHARE_CHECK_SECONDS = 300
STREAK_SHARE_ATTEMPTS = 2
STREAK_SHARE_TIMEOUT = 30
# extra visits in the same broadcast after a full visit without the streak bonus, and their spacing
RECOVERY_VISITS = 2
RECOVERY_GAP_SECONDS = 600
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
    recovery: bool = False


class IdleWatchService:
    def __init__(self, twitch: Twitch, visits_path: Path | None = None, history_path: Path | None = None) -> None:
        self._twitch = twitch
        # visits_path keeps the finished visits and the share keys (used up, or refused with their try
        # count) across restarts, with the account they belong to (None: not known yet, taken by the
        # first account that idle watches)
        self._visits_path = visits_path
        self._account: int | None = None
        # balance samples, gains and bet results of the listed channels, saved separately
        self.history = PointsHistory(history_path)
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
        # channel id -> factors of the active points multipliers (subscriptions), from the last points read
        self.multipliers: dict[int, tuple[float, ...]] = {}
        # runtime only, like the drop pause: idle watching and its points actions stop until resumed
        self.points_paused = False
        self._reader = PointsReader(twitch, self)
        # event topics with raids, moments and predictions, and the chat of the watched channel
        self.events = PointsEvents(twitch, self)
        self._chat = ChatPresence(twitch, self)
        # broadcast id -> (channel id, recovery visits started, when the last visit ended) for broadcasts
        # whose full visit got no streak bonus; in memory only
        self._recovery: dict[int, tuple[int, int, float]] = {}
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
        # "main": the loop runs in IDLE and drives the client's watch; "side" (watch_slots 2): it runs
        # next to drop mining and its pick is watched by the side watcher, never by the client
        self.mode = "main"
        # the side watcher: the channel it watches and its task
        self.side_channel: Channel | None = None
        self._side_task: asyncio.Task[None] | None = None
        # broadcast id -> (True when AvailableDrops listed no campaign at all on it, read time), newest
        # SIDE_DROPS_KEPT; and broadcast id -> time of its last failed read
        self._side_drops: dict[int, tuple[bool, float]] = {}
        self._side_failed_at: dict[int, float] = {}
        self._side_drops_failed = False
        # the side pick when side mode paused for a channel reload: the resumed loop continues its watch
        self._side_pick: Channel | None = None

    @property
    def active(self) -> bool:
        return self._task is not None and not self._task.done()

    @property
    def side(self) -> bool:
        return self.mode == "side"

    def allowed(self) -> bool:
        tw = self._twitch
        return (
            tw.is_mining_time()
            and tw.logged_in_user_id() is not None
            and bool(tw.settings.idle_channels)
            and not self.points_paused
        )

    def side_allowed(self) -> bool:
        """Whether side mode may run: allowed(), two watch slots, no authentication change and no channel reload."""
        tw = self._twitch
        return (
            self.allowed() and tw.settings.watch_slots == 2 and not tw._changing_auth
            and tw._state not in SIDE_PAUSE_STATES
        )

    def drop_channel_room(self) -> int:
        """How many drop channels CHANNELS_FETCH may track: MAX_CHANNELS, and with two watch slots fewer, so
        every listed channel, its stream topics and the most event topics still fit next to them."""
        tw = self._twitch
        if tw.settings.watch_slots != 2:
            return MAX_CHANNELS
        # a listed channel that is also a drop channel is counted twice: it only leaves a spare slot
        listed = len(tw.settings.idle_channels)
        return max((MAX_TOPICS - PointsEvents.most_topics(listed)) // TOPICS_PER_CHANNEL - listed, 0)

    def _side_basic(self, channel: Channel) -> bool:
        tw = self._twitch
        drop = tw.watching_channel.get_with_default(None)
        return not channel.drops_enabled and not tw.can_watch(channel) and (drop is None or drop.id != channel.id)

    def side_candidate(self, channel: Channel) -> bool:
        """Whether side mode may watch channel: drops cannot use it, it is not the drop channel and
        AvailableDrops listed no campaign at all on its broadcast (any campaign may take drop progress).

        An unread or failed broadcast is no candidate; _read_side_drops() fills the answers."""
        answer = self._side_drops.get(channel.broadcast_id or 0)
        return self._side_basic(channel) and answer is not None and answer[0]

    def _store_side_drops(self, broadcast: int, no_campaign: bool, now: float) -> None:
        self._side_drops.pop(broadcast, None)  # newest last, so the oldest answer is dropped first
        self._side_drops[broadcast] = (no_campaign, now)
        while len(self._side_drops) > SIDE_DROPS_KEPT:
            del self._side_drops[next(iter(self._side_drops))]

    async def _read_side_drops(self, live: list[Channel]) -> None:
        """Read AvailableDrops per broadcast of the live channels side mode could pick: again after
        SIDE_DROPS_REFRESH_SECONDS, and not before SIDE_DROPS_RETRY_SECONDS after a failed read."""
        now = time.monotonic()
        self._side_failed_at = {b: t for b, t in self._side_failed_at.items() if now - t < SIDE_DROPS_RETRY_SECONDS}
        todo = [(c, self._broadcast_id(c)) for c in live if self._side_basic(c)]
        todo = [(c, b) for c, b in todo if b not in self._side_failed_at
                and (b not in self._side_drops or now - self._side_drops[b][1] >= SIDE_DROPS_REFRESH_SECONDS)]
        if not todo:
            return
        with private_gql_discovery():
            results = await asyncio.gather(
                *(asyncio.wait_for(c.fetch_drop_campaigns(), timeout=STREAM_CHECK_TIMEOUT) for c, _b in todo),
                return_exceptions=True,
            )
        failed: str | None = None
        for (_c, broadcast), result in zip(todo, results, strict=True):
            if isinstance(result, BaseException):
                if not isinstance(result, READ_ERRORS) or isinstance(result, ExitRequest):
                    raise result
                failed = failed or type(result).__name__
                self._side_failed_at[broadcast] = now
                continue
            self._store_side_drops(broadcast, not result, now)
        if failed is not None and not self._side_drops_failed:
            # once per failing streak; the unread broadcasts are retried after SIDE_DROPS_RETRY_SECONDS
            logger.warning("Drops check of second slot channels failed (%s), retrying", failed)
        self._side_drops_failed = failed is not None

    def set_points_paused(self, paused: bool) -> None:
        """Pause or resume idle watching and its points actions until resumed or restarted.

        Drop mining is untouched: the IDLE step re-runs only while nothing is mined, and side mode
        stops or starts again next to drop mining.
        """
        if paused == self.points_paused:
            return
        self.points_paused = paused
        logger.info("Channel points %s", "paused" if paused else "resumed")
        self._twitch.gui.points.paused_changed(paused)
        # the IDLE step stops the loop and its watch (or starts it again); a running loop is poked too
        self._twitch.idle_channels_changed()

    def enter(self, *, side: bool = False) -> None:
        """Start the loop in main mode, or in side mode; a loop in the other mode stops first."""
        mode = "side" if side else "main"
        if self.active and self.mode != mode:
            # like any stop: the visit clock pauses, and the side watcher ends before main mode watches
            self.stop()
        if not self.active:
            self.mode = mode
            # a pick handed to drops stays watched: until the loop picks again it is not idle watched
            self.watched, self._side_pick = (self._side_pick if side else None), None
            if self.watched is None:
                self._claimed.clear()
                self._claim_failed = False
                self._claim_retries = 0
            self._task = asyncio.create_task(self._run())
            # the points tab shows whether the loop runs, also after it ended by itself
            self._task.add_done_callback(lambda _task: self._twitch.gui.points.refresh())
            self._twitch.gui.points.refresh()

    def stop(self) -> asyncio.Task[None] | None:
        """Cancel the loop and return its task, so a caller can await the cancellation."""
        task, self._task = self._task, None
        if task is not None and not task.done():
            self._pause()
            task.cancel()
            # a reload pause keeps the side pick for the resumed loop (see enter)
            self._side_pick = self.watched if self.side and self._twitch._state in SIDE_PAUSE_STATES else None
        self._side_stop()
        # right away, not when the task ends: the next state may need the topic room
        self._end_events()
        return task

    def stop_all(self) -> list[asyncio.Task[None]]:
        """stop(), returning the loop task and the side watcher task, so a caller can await both."""
        side = self._side_task
        return [task for task in (self.stop(), side) if task is not None]

    def _side_start(self, channel: Channel, *, quiet: bool = False) -> None:
        """Watch channel in the second slot; the previous side watch ends first."""
        self._side_stop()
        if not quiet:
            logger.info("Idle watching %s in the second slot", channel.name)
        self.side_channel = channel
        self._side_task = asyncio.create_task(self._side_watch(channel.id))
        self._twitch.gui.points.refresh()

    def _side_stop(self) -> None:
        task, self._side_task = self._side_task, None
        if task is not None:
            task.cancel()
        if self.side_channel is not None:
            self.side_channel = None
            self._twitch.gui.points.refresh()

    async def _side_watch(self, channel_id: int) -> None:
        """The second watch: send_watch() on the side channel at the drop watch's cadence, outside its progress code.

        It follows side_channel while that keeps channel_id (a re-tracked object of the same channel is
        adopted), and ends when the channel goes offline, is banned, may no longer be side watched or
        mining time ends; the loop is poked to pick again.
        """
        tw = self._twitch
        try:
            while (
                (channel := self.side_channel) is not None and channel.id == channel_id
                and tw.is_mining_time() and channel.online and not channel.banned and self.side_candidate(channel)
            ):
                stream = channel._stream
                started = time.monotonic()
                succeeded = await channel.send_watch(side=True)
                await asyncio.sleep(max(0, SIDE_WATCH_SECONDS - (time.monotonic() - started)))
                if not succeeded and stream is not None and channel._watch_current(stream, side=True):
                    # an ended broadcast fails here even if its stream-down event was lost
                    channel.check_online()
        finally:
            if self._side_task is asyncio.current_task():
                self._side_task = None
                self.side_channel = None
                self._twitch.gui.points.refresh()
                self.poke()

    def _end_events(self) -> None:
        """Unsubscribe the event topics, drop open predictions and leave the chat."""
        self.events.clear()
        self._chat.leave()

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
        self._side_pick = None
        self._last_visit = None
        self._paused_at = None
        self.balances.clear()
        self.multipliers.clear()
        self._reader.reset()
        self._side_drops.clear()
        self._side_failed_at.clear()
        self.events.reset()
        self._chat.reset()
        self._twitch.gui.points.clear()
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

    def settings_changed(self) -> None:
        """An idle option was saved; also while drops are mined and the loop does not run."""
        self._chat.settings_changed()

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
            self._recovery.clear()
        self._account = account

    def show_status(self) -> None:
        """Re-show "Idle watching <channel>" while the loop's channel is still watched."""
        if self.active and self.watched is not None and self.watched is self._twitch.watching_channel.get_with_default(None):
            self._show_status(self.watched)

    async def _run(self) -> None:
        tw = self._twitch
        logger.info("Idle watching started%s", " in the second slot" if self.side else "")
        if not self.side:
            # the last drop's progress panel does not belong to an idle channel
            tw.gui.clear_drop()
        self._use_account(tw.logged_in_user_id())
        if self.history.use_account(tw.logged_in_user_id()):
            tw.gui.points.refresh_history(force=True)
        # each idle entry retries the logins the previous run gave up on
        self._skipped.clear()
        self._ban_ends.clear()
        if self._paused_at is not None and self.visit is not None:
            self.visit.started += time.monotonic() - self._paused_at
        self._paused_at = None
        try:
            await self._loop()
        finally:
            # a loop that ends by itself (pause, handover, mining hours) drops its events too; one
            # replaced by a new run after stop() leaves the new run's events alone
            if self._task is None or self._task is asyncio.current_task():
                self._end_events()
                self._side_stop()
                self.save_history()

    def save_history(self) -> None:
        """Write the points history now: idle watching stopped, or shutdown (the tick saves at most once a minute)."""
        self.history.save(self._twitch.settings.idle_channels, force=True)

    async def _loop(self) -> None:
        tw = self._twitch
        while True:
            # cleared before the awaits, so a poke during the tick runs one more tick
            self._wake.clear()
            # side mode runs next to drop mining: it never hands the drop channel over
            if not self.side and self._hand_to_drops(tw.watching_channel.get_with_default(None)):
                return
            if not (self.side_allowed() if self.side else self.allowed()):
                self._pause()
                self._unwatch()
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
        live, now = self.live_channels(), time.monotonic()
        if self.side:
            # the drop channel, channels drops can use and streams with any drops campaign are never picked
            await self._read_side_drops(live)
            live = [c for c in live if self.side_candidate(c)]
        channel = self.pick(live, now)
        self._save()
        self.history.save(tw.settings.idle_channels)
        tw.gui.points.refresh_history()
        # side mode picks only side candidates, which drops cannot use, so this hands over in main mode only
        if self._hand_to_drops(channel):
            # no watch, balance read or claim on a channel drops can use
            return True
        if self.visit is not self._last_visit:
            if self._last_visit is not None:
                logger.info("Idle visit ended: broadcast %s", self._last_visit.broadcast_id)
            if self.visit is not None:
                name = channel.name if channel else "?"
                logger.info("Idle %svisit started: %s", "recovery " if self.visit.recovery else "", name)
                key = "recovery_visit_started" if self.visit.recovery else "visit_started"
                self._twitch.gui.points.log(key, channel=name)
            self._last_visit = self.visit
        if channel is None:
            self.watched = None
            self._unwatch()
        elif self.side and self.watched is not None and self.watched.id == channel.id and (
            channel is not self.watched or not self._watching(channel)
        ):
            # the same channel, re-tracked as a new object or after a reload pause: its side watch goes on
            # without a start line, claim reset or points read
            self.watched = channel
            if self._watching(channel):
                self.side_channel = channel
            else:
                self._side_start(channel, quiet=True)
        elif not self._watching(channel) or channel is not self.watched:
            if self.watched is not None:
                logger.info("Idle watching switched to %s", channel.name)
            # a re-watch of the same pick after a failed claim reads again only as a counted retry
            retry = channel is self.watched and self._claim_failed
            if channel is not self.watched:
                self._claimed.clear()
                self._claim_failed = False
                self._claim_retries = 0
            self.watched = channel
            if self.side:
                self._side_start(channel)
            else:
                tw.watch(channel, update_status=False)
                self._show_status(channel)
            if not retry or self._claim_retries < CLAIM_RETRIES:
                self._claim_retries += retry
                await self.refresh_points(channel)
        elif self._claim_failed and self._claim_retries < CLAIM_RETRIES:
            # the last bonus claim failed: a fresh read finds the bonus again, once per tick
            self._claim_retries += 1
            await self.refresh_points(channel)
        self.events.sync()
        watched = self.watched
        self._chat.follow(watched if watched is not None and self.may_act(watched, "join_chat") else None)
        await self._reader.read_due(live, now)
        await self.share_streaks()
        self._twitch.gui.points.refresh()
        return False

    def _watching(self, channel: Channel) -> bool:
        """Whether the loop's watch is on channel: the client's watch in main mode, the side watcher's in side mode."""
        if self.side:
            return self.side_channel is not None and self.side_channel.id == channel.id
        return channel is self._twitch.watching_channel.get_with_default(None)

    def _unwatch(self) -> None:
        if self.side:
            self._side_stop()
        else:
            self._twitch.stop_watching()

    def _show_status(self, channel: Channel) -> None:
        if self.side:
            # the status bar belongs to drop mining
            return
        points = self.balances.get(channel.id)
        if points is None:
            text = _.t["status"]["idle_watching"].format(channel=channel.name)
        else:
            text = _.t["status"]["idle_watching_points"].format(channel=channel.name, points=points)
        self._twitch.gui.status.update(text)

    def idle_watched(self, channel_id: object) -> Channel | None:
        """The channel being idle watched (in the second slot in side mode), if channel_id is it and the loop is running."""
        watched = self.watched
        # only the loop's own pick, still watched: at idle entry the last drop channel is still watched until the first pick
        if not self.active or watched is None or not self._watching(watched) or str(watched.id) != str(channel_id):
            return None
        # a channel drops can use is mined, not idle watched, even before the loop hands it over
        return None if self._twitch.can_watch(watched) else watched

    async def gql(
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
        """Claim a bonus on a listed channel while the loop runs; only the watched channel's failure is retried."""
        tw = self._twitch
        watched = channel is self.watched
        # the guards of every points action: also no claim on an offline or banned listed channel, like
        # every other action (the points read covers live channels only anyway)
        if (
            not claim_id
            or claim_id in self._claimed
            or claim_id in self._claiming
            or not self.may_act(channel, "claim_channel_points")
        ):
            return
        self._claiming.add(claim_id)
        try:
            response = await self.gql(GQL_OPERATIONS["ClaimCommunityPoints"].with_variables(
                {"input": {"claimID": claim_id, "channelID": str(channel.id)}},
            ))
        finally:
            self._claiming.discard(claim_id)
        # a failed claim stays unclaimed: the next tick reads the watched channel's points again and
        # retries it; another channel's open bonus is found again by the next points read
        self._claim_failed = self._claim_failed or watched
        if response is None:
            return
        code = error_code(response, "claimCommunityPoints")
        # no error: the field is an object
        claim = response["data"]["claimCommunityPoints"].get("claim") if code is None else None  # type: ignore[index,call-overload]
        if not isinstance(claim, dict) or not isinstance(claim.get("id"), str) or not claim["id"]:
            # an error code (for example an already claimed bonus) or no claim with an id instead of the claim
            logger.warning("Twitch request ClaimCommunityPoints was not accepted (%s)", code or "no claim")
            return
        if watched:
            self._claim_failed = False
            self._claim_retries = 0
        self._claimed.add(claim_id)
        logger.info("Claimed channel points bonus on %s", channel.name)
        tw.gui.points.log("bonus_claimed", channel=channel.name)

    async def refresh_points(self, channel: Channel) -> None:
        """Read the balance of a newly watched channel and claim a bonus that is already open."""
        if self._twitch.can_watch(channel):
            return
        try:
            response = await asyncio.wait_for(
                self.gql(GQL_OPERATIONS["ChannelPointsContext"].with_variables({"channelLogin": channel.login})),
                15,
            )
        except TimeoutError:
            logger.warning("Twitch request ChannelPointsContext timed out")
            return
        await self.apply_points(channel, response)

    async def apply_points(self, channel: Channel, response: object) -> None:
        """Take channel's balance, multipliers and open bonus from a ChannelPointsContext answer."""
        try:
            answered = response["data"]["community"]["channel"]  # type: ignore[index]
            points = answered["self"]["communityPoints"]
            balance, available = points["balance"], points["availableClaim"]
        except (KeyError, TypeError):
            return
        # batch answers pair with channels by position: one that names another channel is skipped
        if "id" in answered and answered["id"] != str(channel.id):
            return
        watched = channel is self.watched
        if watched:
            # this read decides again whether a bonus is open
            self._claim_failed = False
        multipliers = points.get("activeMultipliers")
        if isinstance(multipliers, list):
            self.multipliers[channel.id] = tuple(
                m["factor"] for m in multipliers if isinstance(m, dict) and type(m.get("factor")) in (int, float)
            )
        if isinstance(balance, int) and not isinstance(balance, bool):
            self.balances[channel.id] = balance
            self.record_balance(channel, balance)
            if watched:
                self._show_status(channel)
        self._twitch.gui.points.refresh()
        if isinstance(available, dict) and isinstance(available.get("id"), str):
            await self.claim(channel, available["id"])

    async def share_streaks(self) -> None:
        """At most every 5 minutes, or when a streak was just earned, share watch streaks Twitch allows."""
        now = time.monotonic()
        due = self._share_now or self._share_checked is None or now - self._share_checked >= STREAK_SHARE_CHECK_SECONDS
        if not self.active or not self._twitch.settings.share_watch_streaks or not due or self._load_failed:
            return
        live = [c for c in self.live_channels() if not self._streaks_off(c)]
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
        response = await self.gql(ops, MILESTONES_OPERATION)
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
        return self.may_act(channel, "share_watch_streaks") and not self._streaks_off(channel)

    def _streaks_off(self, channel: Channel) -> bool:
        """The channel's own streaks option is off: no shares there. The global idle_streak_first does not
        block a share; without an override share_watch_streaks alone decides."""
        return self._twitch.settings.idle_channel_options.get(channel.login, {}).get("streaks") is False

    def option(self, channel: Channel, setting: str) -> bool:
        """The effective value of a bool setting on channel: its own override, else the global setting."""
        settings = self._twitch.settings
        key = next((k for k, name in ChannelOptionSettings.OVERRIDES.items() if name == setting), None)
        override = settings.idle_channel_options.get(channel.login, {}).get(key) if key else None
        return override if isinstance(override, bool) else getattr(settings, setting)

    def may_act(self, channel: Channel, setting: str) -> bool:
        """Whether a channel points action behind the bool setting may run on channel right now.

        The loop runs, points are not paused, mining time, the option is on, and the channel is
        listed, live, not banned and not one drops can use; in side mode also not the drop channel
        and its stream lists no drops campaign (side_candidate).
        """
        tw = self._twitch
        return (
            self.active
            and not self.points_paused
            and self.option(channel, setting) is True
            and channel in self.live_channels()
            # can_watch is False while paused or outside mining hours too
            and tw.is_mining_time()
            and not tw.can_watch(channel)
            and (not self.side or self.side_candidate(channel))
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
        response = await self.gql(op)
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
        else:
            if value is None:
                logger.info("Shared watch streak on %s", channel.name)
            else:
                logger.info("Shared watch streak on %s (%s streams)", channel.name, value)
            self._twitch.gui.points.log("streak_shared", channel=channel.name)
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
            # any listed channel; claim() refuses it unless may_act() allows the claim there
            if channel_id is not None and (channel := self.listed(channel_id)) is not None:
                await self.claim(channel, claim["id"])

    def record_balance(self, channel: Channel, balance: int) -> None:
        """Add a balance sample for a listed channel."""
        if channel.login in self._twitch.settings.idle_channels:
            self.history.sample(channel.id, channel.login, balance)

    def listed(self, channel_id: str) -> Channel | None:
        """The tracked channel with this id, if its login is in the idle list."""
        listed = set(self._twitch.settings.idle_channels)
        return next(
            (c for c in self._twitch.channels.values() if str(c.id) == channel_id and c.login in listed), None,
        )

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
        watched = self.idle_watched(channel_id)
        listed = self.listed(channel_id)
        points = balance.get("balance") if isinstance(balance, dict) else None
        if isinstance(points, int) and not isinstance(points, bool) and (watched is not None or listed is not None):
            self.balances[int(channel_id)] = points
            if listed is not None:
                self.record_balance(listed, points)
            if watched is not None:
                self._show_status(watched)
        total = gain.get("total_points") if isinstance(gain, dict) else None
        if listed is not None and isinstance(reason, str) and type(total) is int:
            self._twitch.gui.points.gain(listed.login, total, reason)
            self.history.gain(listed.id, listed.login, reason, total)
            if reason != CLAIM_REASON:
                # claim() already logged the bonus line
                # the tab's reason names; an unknown Twitch code stays as sent
                label = _.t["gui"]["points"]["reasons"].get(reason.lower(), reason)  # type: ignore[attr-defined]
                self._twitch.gui.points.log("gain", channel=listed.name, points=total, reason=label)
        else:
            self._twitch.gui.points.refresh()
        wake = False
        if reason == STREAK_REASON and listed is not None:
            # the next tick runs the share check; no network work in the websocket handler
            self._share_now = wake = True
            # the streak counted: no recovery visit for this channel's stream
            self._recovery = {b: r for b, r in self._recovery.items() if str(r[0]) != channel_id}
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
                self._twitch.gui.points.log("visit_skipped", channel=channel.name)
            self._seen[channel.id] = (broadcast_id, now, channel)

    def pick(self, live: list[Channel], now: float) -> Channel | None:
        settings = self._twitch.settings
        ids = {c.id: self._broadcast_id(c) for c in live}
        self._skip_restarts(live, ids, now)
        visiting = next((c for c in live if self.visit is not None and c.id == self.visit.channel_id), None)
        if visiting is not None and not self.option(visiting, "idle_streak_first"):
            # no visits there: one in progress is dropped unrecorded, so it runs again if visits are turned back on
            self.visit = None
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
            full = now - visit.started >= IDLE_VISIT_SECONDS
            if visit.done or full or visit.channel_id in ids:
                self.visited[visit.broadcast_id] = visit.channel_id
            self._note_recovery(visit, full, now)
            self.visit = None
        # A visit is forgotten only when its channel is live with another broadcast, or is
        # tracked but no longer listed; a channel missing for a tick (or untracked after a
        # cleanup) keeps its visit, so it is not visited twice in one stream.
        listed = set(self._twitch.settings.idle_channels)
        unlisted = {c.id for c in self._twitch.channels.values() if c.login not in listed}
        kept = [(b, cid) for b, cid in self.visited.items() if cid not in unlisted and ids.get(cid, b) == b]
        self.visited = dict(kept[-IDLE_VISITS_KEPT:])
        recovery = [(b, r) for b, r in self._recovery.items() if r[0] not in unlisted and ids.get(r[0], b) == b]
        self._recovery = dict(recovery[-IDLE_VISITS_KEPT:])
        options = settings.idle_channel_options
        favorites = [login for login, item in options.items() if item.get("favorite")]
        order = WatchPriority.order(live, settings.idle_priority, self.balances, self.multipliers, favorites)
        visitors = [c for c in order if self.option(c, "idle_streak_first")]
        for channel in visitors:
            if ids[channel.id] not in self.visited:
                self.visit = Visit(channel.id, ids[channel.id], now)
                return channel
        if settings.streak_recovery:
            # only after every first visit
            for channel in visitors:
                entry = self._recovery.get(ids[channel.id])
                if entry is not None and entry[1] < RECOVERY_VISITS and now - entry[2] >= RECOVERY_GAP_SECONDS:
                    self._recovery[ids[channel.id]] = (channel.id, entry[1] + 1, entry[2])
                    self.visit = Visit(channel.id, ids[channel.id], now, recovery=True)
                    return channel
        stay = WatchPriority.stayable(order, options, self.balances)
        return WatchPriority.first(stay, self.watched, settings.idle_priority, self.balances)

    def _note_recovery(self, visit: Visit, full: bool, now: float) -> None:
        """Book an ended visit for streak recovery: a full first visit without the bonus earns recovery visits."""
        if visit.done:
            self._recovery.pop(visit.broadcast_id, None)
        elif visit.recovery:
            # a recovery visit counts when it starts; its end starts the gap to the next one
            if (entry := self._recovery.get(visit.broadcast_id)) is not None:
                self._recovery[visit.broadcast_id] = (entry[0], entry[1], now)
        elif full:
            self._recovery[visit.broadcast_id] = (visit.channel_id, 0, now)

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

    async def _check_drops(self, channels: list[Channel]) -> None:
        """Set the drops flag of new live channels from AvailableDrops (False when the read fails) and
        give side mode the same answer, so a new channel is not read a second time."""
        live = [c for c in channels if c._stream is not None]
        with private_gql_discovery():
            results = await asyncio.gather(
                *(asyncio.wait_for(c.fetch_drop_campaigns(), timeout=STREAM_CHECK_TIMEOUT) for c in live),
                return_exceptions=True,
            )
        failed: list[str] = []
        now = time.monotonic()
        for channel, result in zip(live, results, strict=True):
            enabled = False
            channel.drops_unverified = isinstance(result, BaseException)
            if isinstance(result, BaseException):
                if not isinstance(result, READ_ERRORS) or isinstance(result, ExitRequest):
                    raise result
                failed.append(type(result).__name__)
            else:
                # a false True would hand the channel to drop mining; CHANNELS_FETCH still finds a real one
                enabled = channel._check_drops_enabled(result, game=channel.game)
                if channel.broadcast_id is not None:
                    self._store_side_drops(channel.broadcast_id, not result, now)
            if channel._stream is not None:
                channel._stream.drops_enabled = enabled
        if failed:
            logger.warning("Drops check failed for %d idle channels (%s), treated as without drops",
                           len(failed), failed[0])

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
                    # the stream's default drops flag is True: every later stream check asks AvailableDrops
                    channel = Channel(twitch, id=channel_id, login=login, verify_drops=True)
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
        await self._check_drops(new)
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
