"""Optional reward-type ordering shared by mining selection and queue display."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any


if TYPE_CHECKING:
    from src.models.campaign import DropsCampaign
    from src.models.drop import TimedDrop


class RewardPriority:
    TYPES = ("BADGE", "EMOTE", "DIRECT_ENTITLEMENT", "UNKNOWN")

    @classmethod
    def normalize(cls, value: Any) -> list[str]:
        if type(value) is not list or (value and (
            len(value) != len(cls.TYPES) or any(type(item) is not str for item in value)
            or set(value) != set(cls.TYPES)
        )):
            raise ValueError("invalid_reward_priority")
        return list(value)

    def __init__(self, settings: Any):
        value = getattr(settings, "reward_priority", [])
        self.order = self.normalize(value if type(value) is list else [])
        self.allowed = getattr(settings, "mining_benefits", {})

    @property
    def enabled(self) -> bool:
        return bool(self.order)

    def drop_key(self, drop: TimedDrop) -> tuple[int, float]:
        ranks = [self.order.index(benefit.type.name) for benefit in drop.benefits
                 if benefit.is_wanted(self.allowed)]
        return (min(ranks, default=len(self.TYPES)), drop.ends_at.timestamp())

    def eligible_drop_key(self, drop: TimedDrop) -> tuple[int, float]:
        return self.drop_key(drop) if drop.can_earn() else (len(self.TYPES), drop.ends_at.timestamp())

    def campaign_key(self, campaign: DropsCampaign) -> tuple[int, float]:
        now = datetime.now(timezone.utc)
        return min((self.drop_key(drop) for drop in campaign.mineable_watch_drops
                    if not drop.is_claimed and drop.starts_at <= now < drop.ends_at and drop.can_earn()),
                   default=(len(self.TYPES), float("inf")))
