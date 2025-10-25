from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING

from dateutil.parser import isoparse

from src.config.constants import MAX_EXTRA_MINUTES
from src.config.operations import GQL_OPERATIONS
from src.exceptions import GQLException
from src.i18n import _
from src.models.benefit import Benefit


if TYPE_CHECKING:
    from src.config.constants import JsonType
    from src.core.client import Twitch
    from src.models.campaign import DropsCampaign
    from src.models.channel import Channel


logger = logging.getLogger("TwitchDrops")
DIMS_PATTERN = re.compile(r"-\d+x\d+(?=\.(?:jpg|png|gif)$)", re.I)


def remove_dimensions(url: str) -> str:
    """Remove dimension suffix from Twitch image URLs (e.g., -285x380.jpg)."""
    return DIMS_PATTERN.sub("", url)


class BaseDrop:
    def __init__(
        self, campaign: DropsCampaign, data: JsonType, claimed_benefits: dict[str, datetime]
    ):
        self._twitch: Twitch = campaign._twitch
        self.id: str = data["id"]
        self.name: str = data["name"]
        self.campaign: DropsCampaign = campaign
        self.benefits: list[Benefit] = [Benefit(b) for b in (data["benefitEdges"] or [])]
        self.starts_at: datetime = isoparse(data["startAt"])
        self.ends_at: datetime = isoparse(data["endAt"])
        self.claim_id: str | None = None
        self.is_claimed: bool = False
        if "self" in data:
            self.claim_id = data["self"]["dropInstanceID"]
            self.is_claimed = data["self"]["isClaimed"]
        elif (
            # If there's no self edge available, we can use claimed_benefits to determine
            # (with pretty good certainty) if this drop has been claimed or not.
            # To do this, we check if the benefitEdges appear in claimed_benefits, and then
            # deref their "lastAwardedAt" timestamps into a list to check against.
            # If the benefits were claimed while the drop was active,
            # the drop has been claimed too.
            (
                dts := [
                    claimed_benefits[bid]
                    for benefit in self.benefits
                    if (bid := benefit.id) in claimed_benefits
                ]
            )
            and all(self.starts_at <= dt < self.ends_at for dt in dts)
        ):
            self.is_claimed = True
        self.precondition_drops: list[str] = [d["id"] for d in (data["preconditionDrops"] or [])]

    def __repr__(self) -> str:
        if self.is_claimed:
            additional = ", claimed=True"
        elif self.can_earn():
            additional = ", can_earn=True"
        else:
            additional = ""
        return f"Drop({self.rewards_text()}{additional})"

    @property
    def preconditions_met(self) -> bool:
        campaign = self.campaign
        return all(campaign.timed_drops[pid].is_claimed for pid in self.precondition_drops)

    def _on_state_changed(self) -> None:
        raise NotImplementedError

    def _base_earn_conditions(self) -> bool:
        # define when a drop can be earned or not
        return (
            self.preconditions_met  # preconditions are met
            and not self.is_claimed  # isn't already claimed
            # has at least one benefit, or participates in a preconditions chain
            and (bool(self.benefits) or self.id in self.campaign.preconditions_chain())
        )

    def _base_can_earn(self) -> bool:
        # cross-participates in can_earn and can_earn_within handling, where a timeframe is added
        return (
            self._base_earn_conditions()
            # is within the timeframe
            and self.starts_at <= datetime.now(timezone.utc) < self.ends_at
        )

    def _can_earn_within(self, stamp: datetime) -> bool:
        # NOTE: This does not check the campaign's eligibility or active status
        return (
            self._base_earn_conditions()
            and self.ends_at > datetime.now(timezone.utc)
            and self.starts_at < stamp
        )

    def can_earn(self, channel: Channel | None = None, ignore_channel_status: bool = False) -> bool:
        return self._base_can_earn() and self.campaign._base_can_earn(
            channel, ignore_channel_status
        )

    @property
    def can_claim(self) -> bool:
        # https://help.twitch.tv/s/article/mission-based-drops?language=en_US#claiming
        # "If you are unable to claim the Drop in time, you will be able to claim it
        # from the Drops Inventory page until 24 hours after the Drops campaign has ended."
        return (
            self.claim_id is not None
            and not self.is_claimed
            and datetime.now(timezone.utc) < self.campaign.ends_at + timedelta(hours=24)
        )

    def update_claim(self, claim_id: str) -> None:
        """Update the claim ID for this drop."""
        self.claim_id = claim_id

    async def generate_claim(self) -> None:
        # claim IDs now appear to be constructed from other IDs we have access to
        # Format: UserID#CampaignID#DropID
        # NOTE: This marks a drop as a ready-to-claim, so we may want to later ensure
        # its mining progress is finished first
        auth_state = await self.campaign._twitch.get_auth()
        self.claim_id = f"{auth_state.user_id}#{self.campaign.id}#{self.id}"

    def rewards_text(self, delim: str = ", ") -> str:
        return delim.join(benefit.name for benefit in self.benefits)

    async def claim(self) -> bool:
        result = await self._claim()
        if result:
            self.is_claimed = result
            claim_text = (
                f"{self.campaign.game.name}\n"
                f"{self.rewards_text()} "
                f"({self.campaign.claimed_drops}/{self.campaign.total_drops})"
            )
            # two different claim texts, becase a new line after the game name
            # looks ugly in the output window - replace it with a space
            self._twitch.print(
                _.t["status"]["claimed_drop"].format(drop=claim_text.replace("\n", " "))
            )
        else:
            logger.error(f"Drop claim has potentially failed! Drop ID: {self.id}")
        return result

    async def _claim(self) -> bool:
        """
        Returns True if the claim succeeded, False otherwise.
        """
        if self.is_claimed:
            return True
        if not self.can_claim:
            return False
        try:
            response = await self._twitch.gql_request(
                GQL_OPERATIONS["ClaimDrop"].with_variables(
                    {"input": {"dropInstanceID": self.claim_id}}
                )
            )
        except GQLException:
            # regardless of the error, we have to assume
            # the claiming operation has potentially failed
            return False
        data = response["data"]
        if "errors" in data and data["errors"]:
            return False
        elif "claimDropRewards" in data:
            if not data["claimDropRewards"]:
                return False
            elif data["claimDropRewards"]["status"] in (
                "ELIGIBLE_FOR_ALL",
                "DROP_INSTANCE_ALREADY_CLAIMED",
            ):
                return True
        return False


class TimedDrop(BaseDrop):
    def __init__(
        self, campaign: DropsCampaign, data: JsonType, claimed_benefits: dict[str, datetime]
    ):
        super().__init__(campaign, data, claimed_benefits)
        self.real_current_minutes: int = (
            "self" in data and data["self"]["currentMinutesWatched"] or 0
        )
        self.required_minutes: int = data["requiredMinutesWatched"]
        self.extra_current_minutes: int = 0
        if self.is_claimed:
            # claimed drops may report inconsistent current minutes, so we need to overwrite them
            self.real_current_minutes = self.required_minutes

    def __repr__(self) -> str:
        if self.is_claimed:
            additional = ", claimed=True"
        elif self.can_earn():
            additional = ", can_earn=True"
        else:
            additional = ""
        if 0 < self.current_minutes < self.required_minutes:
            minutes = f", {self.current_minutes}/{self.required_minutes}"
        else:
            minutes = ""
        return f"Drop({self.rewards_text()}{minutes}{additional})"

    @property
    def current_minutes(self) -> int:
        return self.real_current_minutes + self.extra_current_minutes

    @property
    def remaining_minutes(self) -> int:
        return self.required_minutes - self.current_minutes

    @property
    def total_required_minutes(self) -> int:
        return self.required_minutes + max(
            (
                self.campaign.timed_drops[pid].total_required_minutes
                for pid in self.precondition_drops
            ),
            default=0,
        )

    @property
    def total_remaining_minutes(self) -> int:
        return self.remaining_minutes + max(
            (
                self.campaign.timed_drops[pid].total_remaining_minutes
                for pid in self.precondition_drops
            ),
            default=0,
        )

    @property
    def progress(self) -> float:
        if self.current_minutes <= 0 or self.required_minutes <= 0:
            return 0.0
        elif self.current_minutes >= self.required_minutes:
            return 1.0
        return self.current_minutes / self.required_minutes

    @property
    def availability(self) -> float:
        import math

        now = datetime.now(timezone.utc)
        if self.required_minutes > 0 and self.total_remaining_minutes > 0 and now < self.ends_at:
            return ((self.ends_at - now).total_seconds() / 60) / self.total_remaining_minutes
        return math.inf

    def _base_earn_conditions(self) -> bool:
        return (
            super()._base_earn_conditions()
            and self.required_minutes > 0
            # NOTE: This may be a bad idea, as it invalidates the can_earn status
            # and provides no way to recover from this state until the next reload.
            and self.extra_current_minutes < MAX_EXTRA_MINUTES
        )

    def _on_state_changed(self) -> None:
        self._twitch.gui.inv.update_drop(self)

    def _update_real_minutes(self, delta: int) -> None:
        if delta == 0 or self.real_current_minutes + delta < 0 or not self.can_earn():
            return
        if self.real_current_minutes + delta < self.required_minutes:
            self.real_current_minutes += delta
        else:
            self.real_current_minutes = self.required_minutes
        self.extra_current_minutes = 0
        self._on_state_changed()

    def _bump_minutes(self, channel: Channel | None) -> bool:
        if self.can_earn(channel):
            self.extra_current_minutes += 1
            self._on_state_changed()
            if self.extra_current_minutes >= MAX_EXTRA_MINUTES:
                return True
        return False

    async def claim(self) -> bool:
        result = await super().claim()
        if result:
            self.real_current_minutes = self.required_minutes
            self.extra_current_minutes = 0
        self._on_state_changed()
        return result

    def display(self, *, countdown: bool = True, subone: bool = False) -> None:
        """Display this drop in the GUI with progress information."""
        self._twitch.gui.display_drop(self, countdown=countdown, subone=subone)

    def update_minutes(self, new_minutes: int) -> None:
        """Update the current watched minutes for this drop."""
        delta: int = new_minutes - self.real_current_minutes
        if delta == 0:
            return
        elif self.real_current_minutes + delta < 0:
            delta = -self.real_current_minutes
        elif self.real_current_minutes + delta > self.required_minutes:
            delta = self.required_minutes - self.real_current_minutes
        self.campaign._update_real_minutes(delta)
