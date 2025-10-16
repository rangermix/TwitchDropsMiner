"""WebSocket broadcaster for real-time updates to web clients."""

from __future__ import annotations

from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    from socketio import AsyncServer


class WebSocketBroadcaster:
    """Manages broadcasting messages to all connected web clients via Socket.IO.

    This class acts as a central hub for sending real-time updates from the application
    to all connected browser clients through Socket.IO events.
    """

    def __init__(self):
        self._sio: AsyncServer | None = None  # Will be set by webapp

    def set_socketio(self, sio: AsyncServer):
        """Set the Socket.IO server instance for broadcasting."""
        self._sio = sio

    async def emit(self, event: str, data: Any):
        """Emit an event to all connected clients.

        Args:
            event: The event name to emit
            data: The data payload to send with the event
        """
        if self._sio:
            await self._sio.emit(event, data)
