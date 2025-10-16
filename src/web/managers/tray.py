"""Tray icon stub for web-based GUI (no system tray in browser)."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from src.web.managers.broadcaster import WebSocketBroadcaster


class TrayIconStub:
    """Stub implementation for system tray icon functionality.

    In web mode, traditional system tray operations are not applicable.
    This class provides a compatible interface that translates tray
    operations into browser notifications and UI indicators.
    """

    def __init__(self, broadcaster: WebSocketBroadcaster):
        self._broadcaster = broadcaster

    def change_icon(self, icon: str):
        """Change tray icon (translated to UI indicator in web mode).

        Args:
            icon: Icon name/identifier to change to
        """
        # Broadcast icon change for potential UI indicators
        asyncio.create_task(
            self._broadcaster.emit("tray_icon_change", {"icon": icon})
        )

    def notify(self, message: str, title: str):
        """Send a system notification (translated to browser notification).

        Args:
            message: Notification message body
            title: Notification title
        """
        # Send browser notification
        asyncio.create_task(
            self._broadcaster.emit("notification", {
                "title": title,
                "message": message
            })
        )

    def minimize(self):
        """Minimize to tray (no-op in web mode)."""
        pass

    def restore(self):
        """Restore from tray (no-op in web mode)."""
        pass
