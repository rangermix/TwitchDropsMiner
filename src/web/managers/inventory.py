"""Inventory manager for tracking drop campaigns and claiming progress."""

from __future__ import annotations

import asyncio
from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    from src.web.managers.broadcaster import WebSocketBroadcaster
    from src.web.managers.cache import ImageCache
    from src.models import DropsCampaign, TimedDrop


class InventoryManager:
    """Manages drop campaign inventory display in the web interface.

    Tracks all active, upcoming, and expired campaigns with their drops,
    broadcasting real-time updates as drops are mined and claimed.
    """

    def __init__(self, broadcaster: WebSocketBroadcaster, cache: ImageCache):
        self._broadcaster = broadcaster
        self._cache = cache
        self._campaigns: dict[str, dict[str, Any]] = {}

    def clear(self):
        """Clear all campaigns from inventory."""
        self._campaigns.clear()
        asyncio.create_task(
            self._broadcaster.emit("inventory_clear", {})
        )

    async def add_campaign(self, campaign: DropsCampaign):
        """Add a campaign to the inventory display.

        Args:
            campaign: The drop campaign to add
        """
        # Get campaign image from cache
        image_url = str(campaign.image_url)

        drops_data = []
        for drop in campaign.drops:
            drops_data.append({
                "id": drop.id,
                "name": drop.name,
                "current_minutes": drop.current_minutes,
                "required_minutes": drop.required_minutes,
                "progress": drop.progress,
                "is_claimed": drop.is_claimed,
                "can_claim": drop.can_claim,
                "rewards": drop.rewards_text(),
                "starts_at": drop.starts_at.isoformat(),
                "ends_at": drop.ends_at.isoformat()
            })

        campaign_data = {
            "id": campaign.id,
            "name": campaign.name,
            "game_name": campaign.game.name,
            "image_url": image_url,
            "starts_at": campaign.starts_at.isoformat(),
            "ends_at": campaign.ends_at.isoformat(),
            "linked": campaign.linked,
            "active": campaign.active,
            "upcoming": campaign.upcoming,
            "expired": campaign.expired,
            "claimed_drops": campaign.claimed_drops,
            "total_drops": campaign.total_drops,
            "drops": drops_data
        }

        self._campaigns[campaign.id] = campaign_data
        await self._broadcaster.emit("campaign_add", campaign_data)

    def update_drop(self, drop: TimedDrop):
        """Update a specific drop's progress within its campaign.

        Args:
            drop: The drop to update
        """
        campaign_id = drop.campaign.id
        if campaign_id in self._campaigns:
            # Find and update the drop in the campaign
            for drop_data in self._campaigns[campaign_id]["drops"]:
                if drop_data["id"] == drop.id:
                    drop_data.update({
                        "current_minutes": drop.current_minutes,
                        "required_minutes": drop.required_minutes,
                        "progress": drop.progress,
                        "is_claimed": drop.is_claimed,
                        "can_claim": drop.can_claim
                    })
                    asyncio.create_task(
                        self._broadcaster.emit("drop_update", {
                            "campaign_id": campaign_id,
                            "drop": drop_data
                        })
                    )
                    break

    def get_campaigns(self) -> list[dict[str, Any]]:
        """Get all campaigns in inventory.

        Returns:
            List of campaign data dictionaries
        """
        return list(self._campaigns.values())
