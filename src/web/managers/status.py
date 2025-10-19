"""Status managers for application and WebSocket connection status."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any


if TYPE_CHECKING:
    from src.web.managers.broadcaster import WebSocketBroadcaster


class StatusManager:
    """Manages main application status display in the web interface.

    Tracks and broadcasts the current status message shown to users
    (e.g., "Mining drops...", "Fetching inventory...", etc.).
    """

    def __init__(self, broadcaster: WebSocketBroadcaster):
        self._broadcaster = broadcaster
        self._current_status = "Initializing..."

    def update(self, status: str):
        """Update the current status and broadcast to all clients."""
        self._current_status = status
        asyncio.create_task(self._broadcaster.emit("status_update", {"status": status}))

    def get(self) -> str:
        """Get the current status message."""
        return self._current_status


class WebsocketStatusManager:
    """Manages WebSocket connection status tracking and display.

    Tracks the status and topic count of each WebSocket connection in the pool,
    providing real-time updates about connection health to the web interface.
    """

    def __init__(self, broadcaster: WebSocketBroadcaster):
        self._broadcaster = broadcaster
        self._websockets: dict[int, dict[str, Any]] = {}

    def update(self, idx: int, status: str | None = None, topics: int | None = None):
        """Update a specific websocket's status and/or topic count.

        Args:
            idx: WebSocket index/ID
            status: Optional status string (e.g., "Connected", "Reconnecting")
            topics: Optional number of topics this WebSocket is subscribed to
        """
        if status is None and topics is None:
            return  # Nothing to update

        if idx not in self._websockets:
            self._websockets[idx] = {"status": "Unknown", "topics": 0}

        if status is not None:
            self._websockets[idx]["status"] = status
        if topics is not None:
            self._websockets[idx]["topics"] = topics

        # Broadcast the update
        asyncio.create_task(
            self._broadcaster.emit(
                "websocket_status",
                {
                    "idx": idx,
                    "status": self._websockets[idx]["status"],
                    "topics": self._websockets[idx]["topics"],
                    "total_websockets": len(self._websockets),
                    "total_topics": sum(ws["topics"] for ws in self._websockets.values()),
                },
            )
        )
