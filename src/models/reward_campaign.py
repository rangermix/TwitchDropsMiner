from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING

from dateutil.parser import isoparse

from src.config.constants import MAX_EXTRA_MINUTES
from src.models.campaign import DropsCampaign


if TYPE_CHECKING:
    from src.config.constants import JsonType
    from src.core.client import Twitch
    from src.models.channel import Channel
    from src.models.drop import TimedDrop

logger = logging.getLogger("TwitchDrops")

# A hold on a group Twitch never credits ends after this, so it never locks the group out
# (Twitch's goals are daily).
UNCREDITED_RETRY = timedelta(hours=6)
# Signature of a group with no reading yet, so the same comparison works for it.
NO_READING: tuple[object, ...] = (None, None, None, None)


def _wait_state(
    value: object, goal: int, ends_at: datetime
) -> tuple[int, bool, datetime | None] | None:
    """Turn a window end or next-eligible time into a reward state; None if unparseable."""
    try:
        wait_until = isoparse(value)  # type: ignore[arg-type]
    except (TypeError, ValueError, AttributeError):
        return None
    if wait_until.tzinfo is None:
        wait_until = wait_until.replace(tzinfo=timezone.utc)
    now = datetime.now(timezone.utc)
    if max(wait_until, now) + timedelta(minutes=goal) > ends_at:
        # The next window cannot be finished before the campaign ends: last grant.
        return goal, True, None
    if wait_until > now:
        return 0, False, wait_until
    return 0, False, None


@dataclass
class RewardLedger:
    """Per-group reward bookkeeping, kept by the inventory service across reloads."""

    # Grants already announced (or taken as the starting count), set only from a real reading.
    announced: dict[str, int] = field(default_factory=dict)
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


class RewardCampaign(DropsCampaign):
    """A sitewide Open Reward Campaign mapped onto one drop (see from_reward_campaign)."""

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
        minutes: int, goal: int, ends_at: datetime
    ) -> tuple[int, bool, datetime] | None:
        """Keep a goal that cannot be reached before the campaign ends unmineable.

        It starts at the campaign end instead of counting as claimed, so Inventory does not
        show a reward that was never earned as claimed.
        """
        if datetime.now(timezone.utc) + timedelta(minutes=goal - minutes) > ends_at:
            return minutes, False, ends_at
        return None

    @staticmethod
    def reward_state(
        progress: JsonType | None, goal: int, ends_at: datetime, window_end: str | None = None
    ) -> tuple[int, bool, datetime | None] | None:
        """Map a reward group's progress to (minutes, granted, wait_until); None = do not farm."""
        if progress is None:
            return RewardCampaign._unfinishable(0, goal, ends_at) or (0, False, None)
        status = progress.get("status")
        if status == "IN_PROGRESS":
            minutes = progress.get("currentMinutesWatched")
            minutes = min(max(minutes, 0), goal) if type(minutes) is int else 0
            if minutes < goal:
                return RewardCampaign._unfinishable(minutes, goal, ends_at) or (
                    minutes, False, None
                )
            # Twitch grants the goal and keeps IN_PROGRESS until the window ends; an
            # unknown window end counts as granted until a reload shows a new reading.
            return _wait_state(window_end, goal, ends_at) or (goal, True, None)
        if status == "CLAIMABLE":
            return goal, False, None
        if status not in ("CLAIMED", "FULFILLED"):
            return None
        next_at = progress.get("nextEligibleAt")
        if not next_at:
            return goal, True, None
        return _wait_state(next_at, goal, ends_at)

    @staticmethod
    def _grant_count(progress: JsonType) -> int | None:
        count = progress.get("grantCount")
        return count if type(count) is int and count >= 0 else None

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
        names = (category.get("displayName"), category.get("name"))
        return (
            any(isinstance(n, str) and n for n in names)
            and isinstance(rewards, list)
            and bool(rewards)
            and all(
                isinstance(r, dict)
                and isinstance(r.get("id"), str)
                and isinstance(r.get("name"), str)
                and (r.get("thumbnailImage") is None or isinstance(r["thumbnailImage"], dict))
                for r in rewards
            )
        )

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
        # Subscription goals cannot be earned by watching, so groups that need one are skipped.
        # One malformed group is skipped instead of dropping the whole campaign.
        watch = [
            g
            for g in data["rewardGroups"]
            if isinstance(g, dict)
            and isinstance(g.get("id"), str)
            and isinstance(req := g.get("unlockRequirements"), dict)
            and type(req.get("minuteWatchedGoal")) is int
            and req["minuteWatchedGoal"] > 0
            and not (isinstance(req.get("subsGoal"), int) and req["subsGoal"] > 0)
            and cls._valid_lists(g)
        ]
        ends_at = isoparse(data["endsAt"])
        windows = windows or {}
        ledger = ledger if ledger is not None else RewardLedger()
        states = {
            g["id"]: cls._group_state(
                g, progress_by_group.get(g["id"]), ends_at, windows.get(g["id"]), ledger
            )
            for g in watch
        }
        usable = [(g, state) for g in watch if (state := states[g["id"]]) is not None]

        # shortcut: lane campaigns (several watch tracks, only a started one counts) have no
        # lane choice. Started groups come first: mineable now, waiting for the next window,
        # at the goal inside an open window. Then an unstarted group, then a finished one
        # (CLAIMED/FULFILLED). Add lane choice if mining a chosen lane is wanted.
        def rank(entry: tuple[JsonType, tuple[int, bool, datetime | None]]) -> int:
            group, (_, granted, wait) = entry
            progress = progress_by_group.get(group["id"])
            if progress is None:
                held = ledger.uncredited.get(group["id"])
                # A held lane stays chosen: Twitch credits whichever lane the account can
                # progress, so cycling through every unstarted lane would only retry them all.
                if held is not None and held[2] and wait == held[1]:
                    return 3
                return 3 if not granted and wait is None else 5
            if not granted:
                return 0 if wait is None else 1
            return 4 if progress.get("status") in ("CLAIMED", "FULFILLED") else 2

        pool = [g for g, _ in sorted(usable, key=rank)]
        if not pool:
            return None
        group = pool[0]
        # The claim ID uses the in-progress campaign's ID. GTAVI's equals the event's, so this
        # is defensive; before a group starts only the event's ID is known.
        claim_campaign_id = (campaign_ids or {}).get(group["id"], data["id"])
        goal: int = group["unlockRequirements"]["minuteWatchedGoal"]
        progress: JsonType | None = progress_by_group.get(group["id"])
        state = states[group["id"]]
        assert state is not None
        minutes, granted, wait_until = state
        claimable = progress is not None and progress.get("status") == "CLAIMABLE"
        campaign = cls(
            twitch,
            {
                "id": data["id"],
                "name": data["name"],
                "game": group["categories"][0],
                "self": {"isAccountConnected": True},
                "accountLinkURL": "",
                "startAt": data["startsAt"],
                "endAt": data["endsAt"],
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
                        "startAt": wait_until.isoformat() if wait_until else data["startsAt"],
                        "endAt": data["endsAt"],
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
        campaign.reward_grants = (cls._grant_count(progress) if progress else None) or 0
        campaign.window_end = windows.get(group["id"])
        campaign.built_with_reading = progress is not None
        # The ledger is shared by the service across reloads: a group seen for the first
        # time starts at its current count, so grants that already existed are not news.
        campaign._ledger = ledger
        # A group without a usable reading leaves the ledger alone, so a rebuild that misses it
        # does not rebase and replay old grants later.
        if progress is not None and (count := cls._grant_count(progress)) is not None:
            ledger.announced.setdefault(group["id"], count)
        next(iter(campaign.drops)).quiet_claim = True
        return campaign

    @property
    def skips_drops_flag(self) -> bool:
        """Reward progress does not need the channel's drops-enabled flag."""
        return True

    @classmethod
    def _group_state(
        cls,
        group: JsonType,
        progress: JsonType | None,
        ends_at: datetime,
        window_end: str | None,
        ledger: RewardLedger,
    ) -> tuple[int, bool, datetime | None] | None:
        goal = group["unlockRequirements"]["minuteWatchedGoal"]
        state = cls.reward_state(progress, goal, ends_at, window_end)
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
    def mineable_from_reading(self) -> bool:
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

    async def announce_new_grants(self) -> None:
        """Announce each grant that appeared since the last one announced, once.

        Relies on grantCount rising when the goal is reached (the live run saw 0 -> 1 at 60/60).
        """
        drop = next(iter(self.drops))
        announced = self._ledger.announced.get(drop.id)
        # shortcut: a claim Twitch does not count in grantCount is not announced; claims were
        # never seen live, so every announcement comes from a grantCount rise.
        if announced is None or self.reward_grants <= announced:
            return
        # Set before awaiting so a tick and a reload seeing the same rise announce it once.
        self._ledger.announced[drop.id] = self.reward_grants
        for number in range(announced + 1, self.reward_grants + 1):
            await drop.announce_grant(number)

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
        grants = self._grant_count(progress)
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
        # not a grant to reload for. Observed live: a new window resets 60 -> 1; what Twitch
        # shows after the final grant is assumed to be the same old reading.
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
                # The claim loop already tried this grant: let the extra-minute cutoff
                # switch channels, and the regular reload retry the claim.
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
