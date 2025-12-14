from __future__ import annotations

import logging
from datetime import datetime, timezone
from functools import cached_property
from itertools import chain
from typing import TYPE_CHECKING

from dateutil.parser import isoparse

from src.config.constants import State, URLType
from src.models.channel import Channel
from src.models.drop import TimedDrop, remove_dimensions
from src.models.game import Game


if TYPE_CHECKING:
    from collections import abc

    from src.config.constants import JsonType
    from src.core.client import Twitch


logger = logging.getLogger("TwitchDrops")


class DropsCampaign:
    def __init__(self, twitch: Twitch, data: JsonType, claimed_benefits: dict[str, datetime]):
        self._twitch: Twitch = twitch
        self.id: str = data["id"]
        self.campaign_url: str = f"https://www.twitch.tv/drops/campaigns?dropID={self.id}"
        self.name: str = data["name"]
        self.game: Game = Game(data["game"])
        self.linked: bool = data["self"]["isAccountConnected"]
        self.link_url: str = data["accountLinkURL"]
        # campaign's image actually comes from the game object
        # we use regex to get rid of the dimensions part (ex. ".../game_id-285x380.jpg")
        self.starts_at: datetime = isoparse(data["startAt"])
        self.ends_at: datetime = isoparse(data["endAt"])
        self._valid: bool = data["status"] != "EXPIRED"
        allowed: JsonType = data["allow"]
        self.allowed_channels: list[Channel] = (
            [Channel.from_acl(twitch, channel_data) for channel_data in allowed["channels"]]
            if allowed["channels"] and allowed.get("isEnabled", True)
            else []
        )
        self.timed_drops: dict[str, TimedDrop] = {
            drop_data["id"]: TimedDrop(self, drop_data, claimed_benefits)
            for drop_data in data["timeBasedDrops"]
        }

    def __repr__(self) -> str:
        return f"Campaign({self.game!s}, {self.name}, {self.claimed_drops}/{self.total_drops})"

    @property
    def drops(self) -> abc.Iterable[TimedDrop]:
        return self.timed_drops.values()

    @property
    def time_triggers(self) -> set[datetime]:
        return set(
            chain(
                (self.starts_at, self.ends_at),
                *((d.starts_at, d.ends_at) for d in self.timed_drops.values()),
            )
        )

    @property
    def active(self) -> bool:
        return self._valid and self.starts_at <= datetime.now(timezone.utc) < self.ends_at

    @property
    def upcoming(self) -> bool:
        return self._valid and datetime.now(timezone.utc) < self.starts_at

    @property
    def expired(self) -> bool:
        return not self._valid or self.ends_at <= datetime.now(timezone.utc)

    @property
    def total_drops(self) -> int:
        return len(self.timed_drops)

    @property
    def eligible(self) -> bool:
        return self.linked or self.has_badge_or_emote

    @cached_property
    def has_badge_or_emote(self) -> bool:
        return any(
            benefit.type.is_badge_or_emote() for drop in self.drops for benefit in drop.benefits
        )

    @property
    def finished(self) -> bool:
        return all(d.is_claimed or d.required_minutes <= 0 for d in self.drops)

    @property
    def claimed_drops(self) -> int:
        return sum(d.is_claimed for d in self.drops)

    @property
    def remaining_drops(self) -> int:
        return sum(not d.is_claimed for d in self.drops)

    @property
    def required_minutes(self) -> int:
        return max(d.total_required_minutes for d in self.drops)

    @property
    def remaining_minutes(self) -> int:
        return max(d.total_remaining_minutes for d in self.drops)

    @property
    def progress(self) -> float:
        return sum(d.progress for d in self.drops) / self.total_drops

    @property
    def availability(self) -> float:
        return min(d.availability for d in self.drops)

    @property
    def first_drop(self) -> TimedDrop | None:
        drops: list[TimedDrop] = sorted(
            (drop for drop in self.drops if drop.can_earn()),
            key=lambda d: d.remaining_minutes,
        )
        return drops[0] if drops else None

    def _update_real_minutes(self, delta: int) -> None:
        for drop in self.drops:
            drop._update_real_minutes(delta)
        if (first_drop := self.first_drop) is not None:
            first_drop.display()

    def _base_can_earn(
        self, channel: Channel | None = None, ignore_channel_status: bool = False
    ) -> bool:
        return (
            self.eligible  # account is eligible
            and self.active  # campaign is active (and valid)
            and (
                channel is None
                or (  # channel isn't specified,
                    # or there's no ACL, or the channel is in the ACL
                    (not self.allowed_channels or channel in self.allowed_channels)
                    # and the channel is live and playing the campaign's game
                    and (
                        ignore_channel_status
                        or channel.game is not None
                        and channel.game == self.game
                    )
                )
            )
        )

    def get_drop(self, drop_id: str) -> TimedDrop | None:
        """Get a specific drop by ID from this campaign."""
        return self.timed_drops.get(drop_id)

    def preconditions_chain(self) -> set[str]:
        """Return all drop IDs that are preconditions for unclaimed drops."""
        return set(
            chain.from_iterable(
                drop.precondition_drops for drop in self.drops if not drop.is_claimed
            )
        )

    def can_earn(self, channel: Channel | None = None, ignore_channel_status: bool = False) -> bool:
        # True if any of the containing drops can be earned
        return self._base_can_earn(channel, ignore_channel_status) and any(
            drop._base_can_earn() for drop in self.drops
        )

    def can_earn_within(self, stamp: datetime) -> bool:
        # Same as can_earn, but doesn't check the channel
        # and uses a future timestamp to see if we can earn this campaign later
        return (
            self.eligible
            and self._valid
            and self.ends_at > datetime.now(timezone.utc)
            and self.starts_at < stamp
            and any(drop._can_earn_within(stamp) for drop in self.drops)
        )

    def bump_minutes(self, channel: Channel) -> None:
        """
        Bump the minute counter for all earnable drops in this campaign.
        Used when websocket updates aren't available.
        """
        # NOTE: Use a temporary list to ensure all drops are bumped before checking
        if any(drop._bump_minutes(channel) for drop in self.drops):
            # Executes if any drop's extra_current_minutes reach MAX_ESTIMATED_MINUTES
            # TODO: Figure out a better way to handle this case
            logger.warning(
                f'At least one of the drops in campaign "{self.name}({self.game.name})" '
                "has reached the maximum extra minutes limit!"
            )
            self._twitch.change_state(State.CHANNEL_SWITCH)
        if (first_drop := self.first_drop) is not None:
            first_drop.display()

    def has_wanted_unclaimed_benefits(self, allowed_benefits: dict[str, bool]) -> bool:
        return any(
            drop.has_wanted_unclaimed_benefits(allowed_benefits) for drop in self.drops
        )
