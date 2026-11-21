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
    if wait_until + timedelta(minutes=goal) > ends_at:
        # The next window cannot be finished before the campaign ends: last grant.
        return goal, True, None
    if wait_until > datetime.now(timezone.utc):
        return 0, False, wait_until
    return 0, False, None


class RewardCampaign(DropsCampaign):
    """A sitewide Open Reward Campaign mapped onto one drop (see from_reward_campaign)."""

    # Times the goal was earned (Twitch reports no total); shown in Inventory.
    reward_grants: int = 0

    @staticmethod
    def reward_state(
        progress: JsonType | None, goal: int, ends_at: datetime, window_end: str | None = None
    ) -> tuple[int, bool, datetime | None] | None:
        """Map a reward group's progress to (minutes, granted, wait_until); None = do not farm."""
        if progress is None:
            return 0, False, None
        status = progress.get("status")
        if status == "IN_PROGRESS":
            minutes = progress.get("currentMinutesWatched")
            minutes = min(max(minutes, 0), goal) if type(minutes) is int else 0
            if minutes < goal:
                return minutes, False, None
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
        # lane choice: the track in progress wins, else the first goal; add lane choice if
        # mining a chosen lane is wanted.
        ends_at = isoparse(data["endsAt"])
        windows = windows or {}
        active = [
            g
            for g in watch
            if g["id"] in progress_by_group
            and (
                state := cls.reward_state(
                    progress_by_group[g["id"]],
                    g["unlockRequirements"]["minuteWatchedGoal"],
                    ends_at,
                    windows.get(g["id"]),
                )
            )
            is not None
            and not state[1]
        ]
        pool = active or watch
        if not pool:
            return None
        group = pool[0]
        goal: int = group["unlockRequirements"]["minuteWatchedGoal"]
        progress: JsonType | None = progress_by_group.get(group["id"])
        state = cls.reward_state(progress, goal, ends_at, windows.get(group["id"]))
        if state is None:
            return None
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
                                    "distributionType": None,
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
                                f"{user_id}#{data['id']}#{group['id']}" if claimable else None
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
        return campaign

    @property
    def skips_drops_flag(self) -> bool:
        """Reward progress does not need the channel's drops-enabled flag."""
        return True

    def apply_reward_progress(self, progress: JsonType | None, channel: Channel) -> None:
        """Apply a fresh reward group reading to this campaign's single drop."""
        drop = next(iter(self.drops))
        if progress is None:
            # No observation (only building a campaign reads a missing group as 0).
            self.bump_minutes(channel)
            return
        if (grants := self._grant_count(progress)) is not None:
            self.reward_grants = grants
        state = self.reward_state(progress, drop.required_minutes, self.ends_at)
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
