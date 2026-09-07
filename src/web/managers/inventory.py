"""Inventory manager for tracking drop campaigns and claiming progress."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Iterable, Mapping
from typing import TYPE_CHECKING, Any


if TYPE_CHECKING:
    from src.models import DropsCampaign, TimedDrop
    from src.web.managers.broadcaster import WebSocketBroadcaster
    from src.web.managers.cache import ImageCache


logger = logging.getLogger("TwitchDrops")


class InventoryManager:
    """Manages drop campaign inventory display in the web interface.

    Tracks active, upcoming, and expired campaigns that contain watch drops,
    broadcasting real-time updates as those drops are mined and claimed.
    """

    def __init__(self, broadcaster: WebSocketBroadcaster, cache: ImageCache):
        self._broadcaster = broadcaster
        self._cache = cache
        self._campaigns: dict[str, dict[str, Any]] = {}
        self._batch_mode: bool = False

    @staticmethod
    def _campaign_progress(
        drops: Iterable[Mapping[str, Any]],
    ) -> dict[str, int | bool]:
        """Return live counts for serialized drops visible in inventory."""
        visible_drops = list(drops)
        return {
            "claimed_drops": sum(bool(drop["is_claimed"]) for drop in visible_drops),
            "total_drops": len(visible_drops),
            "ignored_drops": sum(bool(drop["is_ignored"]) for drop in visible_drops),
            "skipped_drops": sum(bool(drop["is_skipped"]) for drop in visible_drops),
            "finished": all(bool(drop["is_claimed"]) for drop in visible_drops),
            "mining_finished": not any(
                not drop["is_claimed"] and drop["is_mineable"]
                for drop in visible_drops
            ),
        }

    @staticmethod
    def _serialize_drop(drop: TimedDrop) -> dict[str, Any]:
        """Serialize one drop with truthful claim, ignore, and mineability state."""
        reason = drop.ignore_reason
        is_mineable = drop.is_mineable
        is_ignored = reason is not None
        return {
            "id": drop.id,
            "name": drop.name,
            "current_minutes": drop.current_minutes,
            "required_minutes": drop.required_minutes,
            "progress": drop.progress,
            "is_claimed": drop.is_claimed,
            "can_claim": drop.can_claim,
            "is_ignored": is_ignored,
            "is_mineable": is_mineable,
            "is_skipped": not drop.is_claimed and not is_ignored and not is_mineable,
            "ignored_reason": reason.kind if reason is not None else None,
            "ignored_keyword": (
                reason.detail if reason is not None and reason.kind == "keyword" else None
            ),
            "ignored_precondition": (
                reason.detail
                if reason is not None and reason.kind == "precondition"
                else None
            ),
            "benefits": [
                {
                    "name": benefit.name,
                    "type": benefit.type.name,
                    "image_url": str(benefit.image_url),
                }
                for benefit in drop.benefits
                if benefit.image_url
            ],
            "starts_at": drop.starts_at.isoformat(),
            "ends_at": drop.ends_at.isoformat(),
        }

    def _serialize_campaign(self, campaign: DropsCampaign) -> dict[str, Any] | None:
        """Serialize a campaign using the same contract for every update path."""
        watch_drops = [drop for drop in campaign.drops if drop.is_watch_drop]
        if not watch_drops:
            return None
        drops_data = [self._serialize_drop(drop) for drop in watch_drops]
        return {
            "id": campaign.id,
            "name": campaign.name,
            "game_name": campaign.game.name,
            "game_box_art_url": campaign.game.box_art_url,
            "campaign_url": campaign.campaign_url,
            "link_url": campaign.link_url,
            "starts_at": campaign.starts_at.isoformat(),
            "ends_at": campaign.ends_at.isoformat(),
            "linked": campaign.linked,
            "active": campaign.active,
            "upcoming": campaign.upcoming,
            "expired": campaign.expired,
            **self._campaign_progress(drops_data),
            "drops": drops_data,
        }

    def clear(self):
        """Clear all campaigns from inventory."""
        self._campaigns.clear()
        asyncio.create_task(self._broadcaster.emit("inventory_clear", {}))

    async def add_campaign(self, campaign: DropsCampaign):
        """Add a campaign to the inventory display.

        Args:
            campaign: The drop campaign to add
        """
        campaign_data = self._serialize_campaign(campaign)
        if campaign_data is None:
            return

        self._campaigns[campaign.id] = campaign_data

        # Only emit immediately if not in batch mode
        if not self._batch_mode:
            await self._broadcaster.emit("campaign_add", campaign_data)

    def update_drop(self, drop: TimedDrop):
        """Update a specific drop's progress within its campaign.

        Args:
            drop: The drop to update
        """
        campaign_id = drop.campaign.id
        if campaign_id in self._campaigns:
            campaign_data = self._serialize_campaign(drop.campaign)
            if campaign_data is None:
                return
            self._campaigns[campaign_id] = campaign_data
            drop_data = next(
                (item for item in campaign_data["drops"] if item["id"] == drop.id),
                None,
            )
            if drop_data is None:
                # Zero-minute rewards are intentionally omitted from Inventory, but
                # claiming one can still change whether a visible dependent drop is
                # mineable. Re-broadcast the complete inventory using the established
                # batch contract instead of sending an invalid hidden-drop update.
                asyncio.create_task(
                    self._broadcaster.emit(
                        "inventory_batch_update",
                        {"campaigns": list(self._campaigns.values())},
                    )
                )
                return
            campaign_progress = self._campaign_progress(campaign_data["drops"])
            asyncio.create_task(
                self._broadcaster.emit(
                    "drop_update",
                    {
                        "campaign_id": campaign_id,
                        "campaign": campaign_progress,
                        "drop": drop_data,
                        "drops": campaign_data["drops"],
                    },
                )
            )

    def refresh_campaigns(self, campaigns: Iterable[DropsCampaign]) -> None:
        """Re-serialize and broadcast all campaign policy state atomically."""
        refreshed: dict[str, dict[str, Any]] = {}
        for campaign in campaigns:
            if (campaign_data := self._serialize_campaign(campaign)) is not None:
                refreshed[campaign.id] = campaign_data
        self._campaigns = refreshed
        asyncio.create_task(
            self._broadcaster.emit(
                "inventory_batch_update", {"campaigns": list(refreshed.values())}
            )
        )

    def start_batch(self):
        """Start batch mode - prevents individual campaign_add emissions.

        Call this before adding multiple campaigns, then call finalize_batch()
        when done to emit all campaigns at once.
        """
        self._batch_mode = True
        self._campaigns.clear()

    async def finalize_batch(self):
        """Finalize batch mode and emit all campaigns atomically.

        This sends a single inventory_batch_update event with all campaigns,
        preventing UI flicker from individual adds.
        """
        self._batch_mode = False
        campaigns_data = list(self._campaigns.values())
        await self._broadcaster.emit("inventory_batch_update", {"campaigns": campaigns_data})

    def get_campaigns(self) -> list[dict[str, Any]]:
        """Get all campaigns in inventory.

        Returns:
            List of campaign data dictionaries
        """
        return list(self._campaigns.values())
