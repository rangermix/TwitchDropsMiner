"""Campaign progress manager for tracking active drop mining progress."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING


if TYPE_CHECKING:
    from src.models import TimedDrop
    from src.web.managers.broadcaster import WebSocketBroadcaster


class CampaignProgressManager:
    """Manages active drop mining progress display and countdown timer.

    Tracks the currently mined drop and broadcasts real-time progress updates
    including remaining time and completion percentage to the web interface.
    """

    def __init__(self, broadcaster: WebSocketBroadcaster):
        self._broadcaster = broadcaster
        self._current_drop: TimedDrop | None = None
        self._remaining_seconds: int = 0

    def update(self, drop: TimedDrop | None, remaining_seconds: int):
        """Update the current drop progress and remaining time.

        Args:
            drop: The drop currently being mined, or None if no active drop
            remaining_seconds: Seconds remaining until the next progress minute
        """
        self._current_drop = drop
        self._remaining_seconds = remaining_seconds
        if drop:
            asyncio.create_task(
                self._broadcaster.emit(
                    "drop_progress",
                    {
                        "drop_id": drop.id,
                        "drop_name": drop.name,
                        "campaign_name": drop.campaign.name,
                        "campaign_id": drop.campaign.id,
                        "game_name": drop.campaign.game.name,
                        "current_minutes": drop.current_minutes,
                        "required_minutes": drop.required_minutes,
                        "progress": drop.progress,
                        "remaining_seconds": remaining_seconds,
                    },
                )
            )

    def stop_timer(self):
        """Stop the progress timer and clear the current drop."""
        self._current_drop = None
        asyncio.create_task(self._broadcaster.emit("drop_progress_stop", {}))

    def minute_almost_done(self) -> bool:
        """Check if the current progress minute is almost complete.

        Returns:
            True if remaining seconds is at or below zero
        """
        return self._remaining_seconds <= 0

    def get_current_drop(self) -> dict | None:
        """Get the current drop progress data for sending to newly connected clients.

        Returns:
            Dictionary with drop progress data, or None if no active drop
        """
        if self._current_drop is None:
            return None

        drop = self._current_drop
        return {
            "drop_id": drop.id,
            "drop_name": drop.name,
            "campaign_name": drop.campaign.name,
            "campaign_id": drop.campaign.id,
            "game_name": drop.campaign.game.name,
            "current_minutes": drop.current_minutes,
            "required_minutes": drop.required_minutes,
            "progress": drop.progress,
            "remaining_seconds": self._remaining_seconds,
        }
