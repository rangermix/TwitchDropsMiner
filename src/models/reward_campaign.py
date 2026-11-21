from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING

from dateutil.parser import isoparse

from src.models.campaign import DropsCampaign


if TYPE_CHECKING:
    from src.config.constants import JsonType
    from src.core.client import Twitch
    from src.models.channel import Channel


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


class RewardCampaign(DropsCampaign):
    """A sitewide Open Reward Campaign mapped onto one drop (see from_reward_campaign)."""

    # Times the goal was earned (Twitch reports no total); shown in Inventory.
    reward_grants: int = 0
    # End of the current window, so a tick can tell a stale at-goal reading from a grant.
    window_end: str | None = None
    # A CLAIMABLE reading is announced by the claim itself, not as a grant.
    claimable: bool = False
    # Grants already announced per group (Twitch's own grant has no claim to announce it).
    _announced: dict[str, int]

    @staticmethod
    def _unfinishable(minutes: int, goal: int, ends_at: datetime) -> tuple[int, bool, None] | None:
        """Treat a goal that cannot be reached before the campaign ends as finished."""
        if datetime.now(timezone.utc) + timedelta(minutes=goal - minutes) > ends_at:
            return goal, True, None
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
        grants_announced: dict[str, int] | None = None,
    ) -> RewardCampaign | None:
        """Build a campaign from a sitewide Open Reward Campaign's current watch group."""
        # Subscription goals cannot be earned by watching, so groups that need one are skipped.
        watch = [
            g
            for g in data["rewardGroups"]
            if g["unlockRequirements"]["minuteWatchedGoal"] > 0
            and not (g["unlockRequirements"].get("subsGoal") or 0) > 0
            and g["categories"]
            and g["rewards"]
        ]
        # shortcut: lane campaigns (several watch tracks, one counts once started) have no
        # lane choice: the started track that can be mined now wins, else the first one
        # that can be, else the first waiting for its next window, else the first granted
        # one; add lane choice if mining a chosen lane is wanted.
        ends_at = isoparse(data["endsAt"])
        windows = windows or {}
        states = {
            g["id"]: cls.reward_state(
                progress_by_group.get(g["id"]),
                g["unlockRequirements"]["minuteWatchedGoal"],
                ends_at,
                windows.get(g["id"]),
            )
            for g in watch
        }
        usable = [(g, state) for g in watch if (state := states[g["id"]]) is not None]
        # Mineable now: not granted and not waiting for the next window.
        mineable = [g for g, (_, granted, wait) in usable if not granted and wait is None]
        waiting = [g for g, (_, granted, wait) in usable if not granted and wait is not None]
        granted_groups = [g for g, (_, granted, _w) in usable if granted]
        pool = (
            [g for g in mineable if g["id"] in progress_by_group]
            or mineable
            or waiting
            or granted_groups
        )
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
        campaign.claimable = claimable
        # Shared by the service across reloads, keyed by group: a group seen for the first
        # time starts at its current count, so grants that already existed are not news.
        campaign._announced = grants_announced if grants_announced is not None else {}
        campaign._announced.setdefault(group["id"], campaign.reward_grants)
        next(iter(campaign.drops)).claim_entry_id = campaign._claim_entry_id
        return campaign

    @property
    def skips_drops_flag(self) -> bool:
        """Reward progress does not need the channel's drops-enabled flag."""
        return True

    def _claim_entry_id(self) -> str:
        """History entry for a real claim, which counts as announced.

        Twitch may or may not already count the CLAIMABLE window in grantCount, so the
        grant is numbered by whichever is higher and later counts do not repeat it.
        """
        drop = next(iter(self.drops))
        number = max(self._announced[drop.id] + 1, self.reward_grants)
        self._announced[drop.id] = number
        return f"{drop.id}#{number}"

    async def announce_new_grants(self) -> None:
        """Announce each grant that appeared since the last one announced, once.

        Relies on grantCount rising when the goal is reached (the live run saw 0 -> 1 at 60/60).
        """
        drop = next(iter(self.drops))
        first = self._announced[drop.id] + 1
        if self.claimable or self.reward_grants < first:
            return
        # Set before awaiting so a tick and a reload seeing the same rise announce it once.
        self._announced[drop.id] = self.reward_grants
        for number in range(first, self.reward_grants + 1):
            await drop.announce_grant(number)

    def apply_reward_progress(self, progress: JsonType | None, channel: Channel) -> None:
        """Apply a fresh reward group reading to this campaign's single drop."""
        drop = next(iter(self.drops))
        if progress is None:
            # No observation (only building a campaign reads a missing group as 0).
            self.bump_minutes(channel)
            return
        self.claimable = progress.get("status") == "CLAIMABLE"
        grants = self._grant_count(progress)
        rose = grants is not None and grants > self.reward_grants
        if grants is not None:
            self.reward_grants = grants
        at_goal = self.reward_state(progress, drop.required_minutes, self.ends_at)
        if rose and at_goal is not None and at_goal[1]:
            # A new grant, even if the stored window looks over (Twitch reset it late).
            self._twitch.request_inventory_refresh()
            return
        # A reading at the goal after the window passed is Twitch's old one: no new minutes,
        # not a grant to reload for.
        state = self.reward_state(progress, drop.required_minutes, self.ends_at, self.window_end)
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
            self.bump_minutes(channel)
