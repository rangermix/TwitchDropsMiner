from __future__ import annotations

import asyncio
import logging
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING

from dateutil.parser import isoparse

from src.config.constants import MAX_EXTRA_MINUTES
from src.exceptions import ExitRequest, LoginException, RequestException
from src.models.campaign import DropsCampaign
from src.models.drop import TimedDrop


if TYPE_CHECKING:
    from src.config.constants import JsonType
    from src.core.client import Twitch
    from src.models.channel import Channel

logger = logging.getLogger("TwitchDrops")

# A hold on a group Twitch never credits ends after this, so it never locks the group out
# (Twitch's goals are daily).
UNCREDITED_RETRY = timedelta(hours=6)
# Signature of a group with no reading yet, so the same comparison works for it.
NO_READING: tuple[object, ...] = (None, None, None, None)
# GraphQL Int fields (watch goals and grant counts) are signed 32-bit values.
MAX_GQL_INT = 2**31 - 1


def _reward_timestamp(value: object, *, assume_utc: bool = False) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = isoparse(value)
        if parsed.tzinfo is None:
            if not assume_utc:
                return None
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    except (ValueError, OverflowError):
        return None


def _wait_state(
    value: object, goal: int, ends_at: datetime, earliest: datetime
) -> tuple[int, bool, datetime | None] | None:
    """Turn a window end or next-eligible time into a reward state; None if unparseable."""
    wait_until = _reward_timestamp(value, assume_utc=True)
    if wait_until is None:
        return None
    if ends_at - max(wait_until, earliest) < timedelta(minutes=goal):
        # The next window cannot be finished before the campaign ends: last grant.
        return goal, True, None
    if wait_until > earliest:
        return 0, False, wait_until
    return 0, False, None


@dataclass
class RewardClaimAttempt:
    window_end: datetime | None
    confirmed: bool = False
    in_flight: bool = True


@dataclass
class PendingGrantRange:
    drop: TimedDrop
    first: int
    last: int


# Limit disk writes and external alerts per reconciliation, even for a long-lived campaign.
GRANTS_PER_UPDATE = 16


@dataclass
class RewardLedger:
    """Per-group reward bookkeeping, kept by the inventory service across reloads."""

    # Highest confirmed count queued for delivery (or taken as the starting count).
    announced: dict[str, int] = field(default_factory=dict)
    pending_history: dict[str, PendingGrantRange] = field(default_factory=dict)
    pending_alerts: deque[tuple[TimedDrop, int]] = field(default_factory=deque)
    _delivery_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    claim_attempts: dict[str, RewardClaimAttempt] = field(default_factory=dict)
    # Groups whose stale at-goal reading ran into the extra-minute cutoff: reading signature.
    stale: dict[str, tuple[object, ...]] = field(default_factory=dict)
    # Groups Twitch did not credit through the extra-minute cutoff: (reading signature, until,
    # held). The first cutoff stores a pending entry (held False, until = now + the retry
    # period), so the next reload still mines the group. A second cutoff with the same reading
    # makes it a hold (held True, until = the retry time), and a rebuild holds the group until
    # then. An entry past its until is dropped.
    uncredited: dict[str, tuple[tuple[object, ...], datetime, bool]] = field(
        default_factory=dict
    )

    async def announce_pending(self) -> None:
        """Persist a bounded batch of confirmed grants, then attempt each queued alert once."""
        async with self._delivery_lock:
            budget = GRANTS_PER_UPDATE - len(self.pending_alerts)
            for group_id, pending in tuple(self.pending_history.items()):
                while budget and pending.first <= pending.last:
                    budget -= 1
                    try:
                        if not pending.drop.record_grant(pending.first):
                            break
                    except Exception:
                        logger.warning("Reward grant announcement history could not be recorded")
                        break
                    self.pending_alerts.append((pending.drop, pending.first))
                    pending.first += 1
                if pending.first > pending.last:
                    del self.pending_history[group_id]
                if not budget:
                    break

            while self.pending_alerts:
                # Once an external send starts, its outcome can be ambiguous on cancellation.
                # Leave subsequent unsent grants in the queue for the next tick or reload.
                drop, number = self.pending_alerts.popleft()
                try:
                    await drop.announce_grant(number)
                except ExitRequest:
                    raise
                except Exception:
                    logger.warning("Reward grant announcement could not be delivered")


class RewardDrop(TimedDrop):
    """Send one claim per observed reward opportunity, even if the reply is lost."""

    campaign: RewardCampaign
    quiet_claim = True
    single_attempt_claim = True

    async def _claim(self) -> bool:
        if self.is_claimed or not self.can_claim:
            return self.is_claimed
        attempts = self.campaign._ledger.claim_attempts
        previous = attempts.get(self.id)
        if previous is not None:
            return previous.confirmed
        attempt = RewardClaimAttempt(_reward_timestamp(self.campaign.window_end, assume_utc=True))
        attempts[self.id] = attempt
        try:
            result = await super()._claim()
        except (LoginException, RequestException):
            # The request may already have reached Twitch. Preserve the attempt for reconciliation.
            return False
        finally:
            attempt.in_flight = False
        if result and attempts.get(self.id) is attempt:
            attempt.confirmed = True
            self._twitch.request_inventory_refresh()
        return result


class RewardCampaign(DropsCampaign):
    """A sitewide Open Reward Campaign mapped onto one drop (see from_reward_campaign)."""

    drop_type = RewardDrop
    # Times the goal was earned (Twitch reports no total); shown in Inventory.
    reward_grants: int = 0
    # End of the current window, so a tick can tell a stale at-goal reading from a grant.
    window_end: str | None = None
    # Whether the campaign was built from a real reading (a later missing one is not news).
    built_with_reading: bool = False
    # Twitch's own grant has no claim to announce it, so grants are tracked here.
    _ledger: RewardLedger

    @staticmethod
    def _unfinishable(
        minutes: int, goal: int, ends_at: datetime, earliest: datetime
    ) -> tuple[int, bool, datetime] | None:
        """Keep a goal that cannot be reached before the campaign ends unmineable.

        It starts at the campaign end instead of counting as claimed, so Inventory does not
        show a reward that was never earned as claimed.
        """
        if ends_at - earliest < timedelta(minutes=goal - minutes):
            return minutes, False, ends_at
        return None

    @staticmethod
    def reward_state(
        progress: JsonType | None, goal: int, ends_at: datetime, window_end: str | None = None,
        *, starts_at: datetime | None = None,
    ) -> tuple[int, bool, datetime | None] | None:
        """Map a reward group's progress to (minutes, granted, wait_until); None = do not farm."""
        now = datetime.now(timezone.utc)
        earliest = max(now, starts_at) if starts_at is not None else now
        if progress is None:
            return RewardCampaign._unfinishable(0, goal, ends_at, earliest) or (0, False, None)
        status = progress.get("status")
        if status == "IN_PROGRESS":
            minutes = progress.get("currentMinutesWatched")
            minutes = min(max(minutes, 0), goal) if type(minutes) is int else 0
            if minutes < goal:
                return RewardCampaign._unfinishable(minutes, goal, ends_at, earliest) or (
                    minutes, False, None
                )
            # Twitch grants the goal and keeps IN_PROGRESS until the window ends; an
            # unknown window end counts as granted until a reload shows a new reading.
            return _wait_state(window_end, goal, ends_at, earliest) or (goal, True, None)
        if status == "CLAIMABLE":
            return goal, False, None
        if status not in ("CLAIMED", "FULFILLED"):
            return None
        next_at = progress.get("nextEligibleAt")
        if not next_at:
            return goal, True, None
        return _wait_state(next_at, goal, ends_at, earliest)

    @staticmethod
    def _grant_count(progress: JsonType | None, goal: int, duration: timedelta) -> int | None:
        count = progress.get("grantCount") if progress else None
        if type(count) is not int or not 0 <= count <= MAX_GQL_INT:
            return None
        # Allow a partial interval at a reporting boundary; a larger count cannot come
        # from this watch goal even if it progressed for the campaign's entire lifetime.
        intervals, remainder = divmod(duration, timedelta(minutes=goal))
        return count if count <= intervals + bool(remainder) else None

    @staticmethod
    def _valid_lists(group: JsonType) -> bool:
        """Whether the group has what Game and the drop build read from it.

        Game needs an int-like category ID and a displayName or name; each reward needs a
        string ID and name, and a thumbnailImage that is a dict when present.
        """
        categories, rewards = group.get("categories"), group.get("rewards")
        if not (isinstance(categories, list) and categories and isinstance(categories[0], dict)):
            return False
        category = categories[0]
        cid = category.get("id")
        if isinstance(cid, bool) or not isinstance(cid, int | str):
            return False
        try:
            int(cid)
        except ValueError:
            return False
        # Match the exact value Game uses; a truthy malformed displayName masks a valid name.
        name = category.get("displayName") or category.get("name")
        if not isinstance(name, str) or not name.strip():
            return False
        if "slug" in category and not isinstance(category["slug"], str):
            return False
        if not isinstance(category.get("boxArtURL"), str | None):
            return False
        if not isinstance(rewards, list) or not rewards:
            return False
        for reward in rewards:
            if not isinstance(reward, dict) or not isinstance(reward.get("name"), str):
                return False
            if not isinstance(reward.get("id"), str) or not reward["id"].strip():
                return False
            image = reward.get("thumbnailImage")
            if image is not None and (
                not isinstance(image, dict) or not isinstance(image.get("image1xURL"), str | None)
            ):
                return False
        return True

    @classmethod
    def from_reward_campaign(
        cls,
        twitch: Twitch,
        data: JsonType,
        progress_by_group: JsonType,
        user_id: str | int,
        windows: dict[str, str] | None = None,
        campaign_ids: dict[str, str] | None = None,
        badge_rewards: set[str] | None = None,
        ledger: RewardLedger | None = None,
    ) -> RewardCampaign | None:
        """Build a campaign from a sitewide Open Reward Campaign's current watch group."""
        if data.get("isSitewide") is not True:
            return None
        if not isinstance(data.get("rewardGroups"), list) or not all(
            isinstance(data.get(key), str) and data[key].strip() for key in ("id", "name", "status")
        ):
            raise ValueError("Invalid reward campaign metadata")
        starts_at = _reward_timestamp(data.get("startsAt"))
        ends_at = _reward_timestamp(data.get("endsAt"))
        # BaseDrop.can_claim uses a 24-hour grace period after the campaign ends.
        if (
            starts_at is None or ends_at is None or starts_at >= ends_at
            or ends_at > datetime.max.replace(tzinfo=timezone.utc) - timedelta(days=1)
        ):
            raise ValueError("Invalid reward campaign dates")
        # Subscription goals cannot be earned by watching, so groups that need one are skipped.
        # One malformed group is skipped instead of dropping the whole campaign.
        watch = [
            g
            for g in data["rewardGroups"]
            if isinstance(g, dict)
            and isinstance(g.get("id"), str) and g["id"].strip()
            and isinstance(req := g.get("unlockRequirements"), dict)
            and type(req.get("minuteWatchedGoal")) is int
            and 0 < req["minuteWatchedGoal"] <= MAX_GQL_INT
            and (req.get("subsGoal") is None or (type(req["subsGoal"]) is int and req["subsGoal"] == 0))
            and cls._valid_lists(g)
        ]
        windows = windows or {}
        ledger = ledger if ledger is not None else RewardLedger()
        states = {
            g["id"]: cls._group_state(
                g, progress_by_group.get(g["id"]), ends_at, windows.get(g["id"]), ledger, starts_at
            )
            for g in watch
        }
        usable = [(g, state) for g in watch if (state := states[g["id"]]) is not None]

        # shortcut: lane campaigns (several watch tracks, only a started one counts) have no
        # lane choice. Claimable groups come first, then started groups that can earn
        # now, are waiting for their next window, or reached the goal in an open window.
        def rank(entry: tuple[JsonType, tuple[int, bool, datetime | None]]) -> int:
            group, (_, granted, wait) = entry
            progress = progress_by_group.get(group["id"])
            if wait == ends_at:
                # This unfinished goal cannot fit, even if Twitch already started it.
                return 7
            if progress is None:
                held = ledger.uncredited.get(group["id"])
                # A held lane stays chosen: Twitch credits whichever lane the account can
                # progress, so cycling through every unstarted lane would only retry them all.
                if held is not None and held[2] and wait == held[1]:
                    return 4
                return 5 if not granted and wait is None else 7
            if not granted:
                if wait is None and progress.get("status") == "CLAIMABLE":
                    return 0
                return 1 if wait is None else 2
            return 6 if progress.get("status") in ("CLAIMABLE", "CLAIMED", "FULFILLED") else 3

        if not usable:
            return None
        group, state = min(usable, key=rank)
        # The claim ID uses the in-progress campaign's ID. GTAVI's equals the event's, so this
        # is defensive; before a group starts only the event's ID is known.
        claim_campaign_id = (campaign_ids or {}).get(group["id"], data["id"])
        goal: int = group["unlockRequirements"]["minuteWatchedGoal"]
        progress: JsonType | None = progress_by_group.get(group["id"])
        minutes, granted, wait_until = state
        claimable = (
            progress is not None and progress.get("status") == "CLAIMABLE"
            and not granted and wait_until is None
        )
        campaign = cls(
            twitch,
            {
                "id": data["id"],
                "name": data["name"],
                "game": group["categories"][0],
                "self": {"isAccountConnected": True},
                "accountLinkURL": "",
                "startAt": starts_at.isoformat(),
                "endAt": ends_at.isoformat(),
                "status": data["status"],
                "allow": {"channels": None},
                "timeBasedDrops": [
                    {
                        "id": group["id"],
                        "name": data["name"],
                        "benefitEdges": [
                            {
                                "benefit": {
                                    "id": reward["id"],
                                    "name": reward["name"],
                                    # Twitch reports the type only through the hints query.
                                    "distributionType": (
                                        "BADGE" if reward["id"] in (badge_rewards or ()) else None
                                    ),
                                    "imageAssetURL": (reward.get("thumbnailImage") or {}).get(
                                        "image1xURL"
                                    ),
                                }
                            }
                            for reward in group["rewards"]
                        ],
                        "startAt": (wait_until or starts_at).isoformat(),
                        "endAt": ends_at.isoformat(),
                        "preconditionDrops": None,
                        "requiredMinutesWatched": goal,
                        "self": {
                            "dropInstanceID": (
                                f"{user_id}#{claim_campaign_id}#{group['id']}" if claimable else None
                            ),
                            "isClaimed": granted,
                            "currentMinutesWatched": minutes,
                        },
                    }
                ],
            },
            {},
        )
        # Watchable siblings can already have grants before they become the selected lane.
        # Seed each first observation without announcing it or replacing an earlier baseline.
        counts = {}
        for watch_group, _state in usable:
            gid = watch_group["id"]
            count = cls._grant_count(
                progress_by_group.get(gid), watch_group["unlockRequirements"]["minuteWatchedGoal"],
                ends_at - starts_at,
            )
            if count is not None:
                counts[gid] = count
                ledger.announced.setdefault(gid, count)
        campaign.reward_grants = counts.get(group["id"], ledger.announced.get(group["id"], 0))
        campaign.window_end = windows.get(group["id"])
        campaign.built_with_reading = progress is not None
        campaign._ledger = ledger
        drop = next(iter(campaign.drops))
        # The group has no drop title of its own. Match each reward as well as the event.
        drop.ignore_name_aliases = tuple(benefit.name for benefit in drop.benefits if benefit.name)
        return campaign

    @property
    def skips_drops_flag(self) -> bool:
        """Reward progress does not need the channel's drops-enabled flag."""
        return True

    def _base_can_earn(self, channel: Channel | None = None, ignore_channel_status: bool = False) -> bool:
        return (
            super()._base_can_earn(channel, ignore_channel_status)
            and self.has_wanted_unclaimed_benefits(self._twitch.settings.mining_benefits)
        )

    def can_earn_within(self, stamp: datetime) -> bool:
        return super().can_earn_within(stamp) and self.has_wanted_unclaimed_benefits(
            self._twitch.settings.mining_benefits
        )

    @classmethod
    def _group_state(
        cls,
        group: JsonType,
        progress: JsonType | None,
        ends_at: datetime,
        window_end: str | None,
        ledger: RewardLedger,
        starts_at: datetime,
    ) -> tuple[int, bool, datetime | None] | None:
        goal = group["unlockRequirements"]["minuteWatchedGoal"]
        state = cls.reward_state(progress, goal, ends_at, window_end, starts_at=starts_at)
        attempt = ledger.claim_attempts.get(group["id"])
        if attempt is not None:
            window = _reward_timestamp(window_end, assume_utc=True)
            reading = progress or {}
            minutes = reading.get("currentMinutesWatched")
            new_cycle = (
                reading.get("status") == "IN_PROGRESS" and type(minutes) is int and 0 <= minutes < goal
            ) or (
                attempt.window_end is not None and window is not None
                and attempt.window_end <= datetime.now(timezone.utc) and window > attempt.window_end
            )
            if new_cycle and not attempt.in_flight:
                del ledger.claim_attempts[group["id"]]
            elif progress is None or reading.get("status") == "CLAIMABLE":
                # A missing reading or a repeated mutation response cannot establish a new grant.
                if attempt.window_end is None:
                    attempt.window_end = window
                return goal, attempt.confirmed, None if attempt.confirmed else ends_at
        remembered = ledger.stale.get(group["id"])
        if remembered is not None:
            if progress is not None and cls._same_reading(
                remembered, cls._reading_signature(progress, window_end)
            ):
                # The same stale at-goal reading already ran into the extra-minute cutoff.
                return goal, True, None
            del ledger.stale[group["id"]]
        entry = ledger.uncredited.get(group["id"])
        if entry is not None and state is not None:
            signature, until, held = entry
            current = NO_READING if progress is None else cls._reading_signature(progress, window_end)
            # A missing reading is no news about one that was really seen.
            keeps = (progress is None and signature != NO_READING) or cls._matches_hold(
                signature, current
            )
            if until <= datetime.now(timezone.utc) or not keeps:
                del ledger.uncredited[group["id"]]
            elif held and not state[1] and state[2] is None:
                # Mineable now but not credited: the drop starts when the retry is due.
                return state[0], False, until
        return state

    @staticmethod
    def _same_reading(remembered: tuple[object, ...], current: tuple[object, ...]) -> bool:
        # A missing window (the hints query failed) must not forget a stale reading.
        return remembered == current or (current[-1] is None and remembered[:-1] == current[:-1])

    @classmethod
    def _matches_hold(cls, held: tuple[object, ...], current: tuple[object, ...]) -> bool:
        # The hold may have been recorded while the hints query failed (window None).
        return cls._same_reading(held, current) or (held[-1] is None and held[:-1] == current[:-1])

    @staticmethod
    def _reading_signature(progress: JsonType, window_end: str | None) -> tuple[object, ...]:
        return (progress.get("status"), progress.get("grantCount"),
                progress.get("currentMinutesWatched"), window_end)

    @property
    def earnable_from_reading(self) -> bool:
        """Whether the picked group has a real reading that can be mined (or claimed) now.

        Started groups that can be mined now are picked first, so this covers every group.
        Granted, waiting, held and unfinishable groups start later or count as claimed.
        """
        drop = next(iter(self.timed_drops.values()))
        return (
            self.built_with_reading
            and not drop.is_claimed
            and drop.starts_at <= datetime.now(timezone.utc)
        )

    @property
    def mineable_drop_ids(self) -> frozenset[str]:
        # A goal that cannot finish before the campaign ends starts at its end (_unfinishable).
        return frozenset(
            i for i in super().mineable_drop_ids if self.timed_drops[i].starts_at < self.ends_at
        )

    def queue_new_grants(self) -> None:
        """Keep each newly observed grant even if the next inventory omits this group."""
        drop = next(iter(self.drops))
        announced = self._ledger.announced.get(drop.id)
        # shortcut: a claim Twitch does not count in grantCount is not announced; claims were
        # never seen live, so every announcement comes from a grantCount rise.
        if announced is None or self.reward_grants <= announced:
            return
        self._ledger.announced[drop.id] = self.reward_grants
        pending = self._ledger.pending_history.get(drop.id)
        if pending is None:
            self._ledger.pending_history[drop.id] = PendingGrantRange(
                drop, announced + 1, self.reward_grants
            )
        else:
            pending.last = self.reward_grants

    async def announce_new_grants(self) -> None:
        self.queue_new_grants()
        await self._ledger.announce_pending()

    def _track_uncredited(
        self, drop: TimedDrop, signature: tuple[object, ...], crossed: bool, stale: bool
    ) -> None:
        """Forget a hold once the reading changed; hold on a second cutoff with the same reading."""
        ledger = self._ledger.uncredited
        now = datetime.now(timezone.utc)
        entry = ledger.get(drop.id)
        if entry is not None and (entry[1] <= now or not self._matches_hold(entry[0], signature)):
            del ledger[drop.id]
            entry = None
        # A stale at-goal reading has its own memo (ledger.stale).
        if not crossed or stale:
            return
        if entry is None:
            ledger[drop.id] = (signature, now + UNCREDITED_RETRY, False)
            return
        if entry[2]:
            return
        # Never skip the last attempt that can still finish before the campaign ends (one
        # cutoff of slack, since a retry may spend that long without credit).
        retry_at = min(
            now + UNCREDITED_RETRY,
            self.ends_at - timedelta(
                minutes=drop.required_minutes - drop.real_current_minutes + MAX_EXTRA_MINUTES
            ),
        )
        if retry_at <= now:
            return
        for gid in [g for g, (_, until, _) in ledger.items() if until <= now]:
            del ledger[gid]
        ledger[drop.id] = (signature, retry_at, True)
        logger.info(
            'Twitch did not credit "%s" in two attempts of %d minutes; retrying after %s',
            self.name, MAX_EXTRA_MINUTES, retry_at.astimezone().strftime("%Y-%m-%d %H:%M"),
        )

    def apply_reward_progress(self, progress: JsonType | None, channel: Channel) -> None:
        """Apply a fresh reward group reading to this campaign's single drop."""
        drop = next(iter(self.drops))
        if progress is None:
            # No observation (only building a campaign reads a missing group as 0).
            before = drop.extra_current_minutes
            self.bump_minutes(channel)
            if not self.built_with_reading:
                self._track_uncredited(
                    drop, NO_READING, before < MAX_EXTRA_MINUTES <= drop.extra_current_minutes, False
                )
            return
        gid = drop.id
        grants = self._grant_count(progress, drop.required_minutes, self.ends_at - self.starts_at)
        minutes = progress.get("currentMinutesWatched")
        if (
            gid not in self._ledger.announced
            and grants is not None
            and type(minutes) is int
            and minutes < drop.required_minutes
        ):
            # A group that was not in progress at load has no entry. Seeding from a reading
            # below the goal makes its first grant (grantCount rising at the goal) news.
            self._ledger.announced[gid] = grants
        # Compared with what was announced too: a reload can rebuild with a lower reading.
        rose = grants is not None and grants > max(
            self.reward_grants, self._ledger.announced.get(gid, 0)
        )
        if grants is not None:
            self.reward_grants = grants
        at_goal = self.reward_state(progress, drop.required_minutes, self.ends_at)
        if rose and at_goal is not None and at_goal[1]:
            # A new grant, even if the stored window looks over (Twitch reset it late).
            self._twitch.request_inventory_refresh()
            return
        # A reading at the goal after the window passed is Twitch's old one: no new minutes,
        # not a grant to reload for. Observed live: a new window resets 60 -> 1. After the final
        # grant (seen once on a sitewide campaign) Twitch dropped the campaign from the in-progress
        # list, so ticks got no reading; the next reload skips it through the completed list.
        state = self.reward_state(progress, drop.required_minutes, self.ends_at, self.window_end)
        stale = (
            progress.get("status") == "IN_PROGRESS"
            and at_goal is not None and at_goal[1] and state is not None and not state[1]
        )
        signature = self._reading_signature(progress, self.window_end)
        remembered = self._ledger.stale.get(gid)
        if remembered is not None and (not stale or not self._same_reading(remembered, signature)):
            # The reading changed (minutes, grant count, new window): mine it again.
            del self._ledger.stale[gid]
        crossed = False
        if state is None or state[1] or state[2]:
            # Finished window or unknown status: rebuild from Twitch.
            self._twitch.request_inventory_refresh()
        elif progress.get("status") == "CLAIMABLE":
            if drop.claim_id is None:
                self._twitch.request_inventory_refresh()
            else:
                # The claim loop handles this grant. Let the extra-minute cutoff switch
                # channels while waiting for a fresh reading.
                self.bump_minutes(channel)
        elif state[0] != drop.real_current_minutes:
            drop.extra_current_minutes = 0
            drop.update_minutes(state[0])
        else:
            # Twitch may not credit this watch: estimate until the extra-minute cutoff
            # switches channels.
            before = drop.extra_current_minutes
            self.bump_minutes(channel)
            # shortcut: a cutoff reached by a fallback bump (failed progress query) is not
            # counted, so a flaky poll keeps the old reload loop; count it there if that shows up.
            crossed = before < MAX_EXTRA_MINUTES <= drop.extra_current_minutes
        self._track_uncredited(drop, signature, crossed, stale)
        if stale and drop.extra_current_minutes >= MAX_EXTRA_MINUTES:
            # No minute change all the way to the cutoff: a rebuild with this same reading
            # treats the group as done instead of mining it again.
            self._ledger.stale[gid] = signature
