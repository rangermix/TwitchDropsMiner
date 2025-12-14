"""Console output manager for logging to web interface."""

from __future__ import annotations

import asyncio
from collections import deque
from datetime import datetime
from typing import TYPE_CHECKING


if TYPE_CHECKING:
    from src.web.managers.broadcaster import WebSocketBroadcaster



import logging

logger = logging.getLogger("TwitchDrops")

class ConsoleOutputManager:
    """Manages console output display in the web interface.

    Buffers log messages and broadcasts them to connected clients in real-time,
    maintaining a rolling history of recent messages.
    """

    def __init__(self, broadcaster: WebSocketBroadcaster, max_lines: int = 1000):
        self._broadcaster = broadcaster
        self._buffer: deque[str] = deque(maxlen=max_lines)

    def print(self, message: str):
        """Print a message to the console output with timestamp.

        Args:
            message: The message to display
        """
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        line = f"[{timestamp}] | {message}"
        self._buffer.append(line)
        asyncio.create_task(self._broadcaster.emit("console_output", {"message": line}))
        logger.info(message)

    def get_history(self) -> list[str]:
        """Get the current console history buffer.

        Returns:
            List of timestamped console messages
        """
        return list(self._buffer)
