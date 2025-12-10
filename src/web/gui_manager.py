"""Main web GUI manager coordinating all UI components."""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

from src.models.game import Game
from src.web.managers.broadcaster import WebSocketBroadcaster
from src.web.managers.cache import ImageCache
from src.web.managers.campaigns import CampaignProgressManager
from src.web.managers.channels import ChannelListManager
from src.web.managers.console import ConsoleOutputManager
from src.web.managers.inventory import InventoryManager
from src.web.managers.login import LoginFormManager
from src.web.managers.settings import SettingsManager
from src.web.managers.status import StatusManager, WebsocketStatusManager


if TYPE_CHECKING:
    from socketio import AsyncServer

    from src.core.client import Twitch
    from src.models import TimedDrop


logger = logging.getLogger("TwitchDrops")


class WebGUIManager:
    """Web-based GUI manager coordinating all UI components.

    This class serves as the main coordinator for the web-based interface,
    managing all component managers and providing the same interface as the
    desktop GUIManager for compatibility with the core application logic.

    The WebGUIManager uses Socket.IO for real-time bidirectional communication
    with browser clients, enabling live updates of drop progress, channel lists,
    and other dynamic content.
    """

    def __init__(self, twitch: Twitch):
        self._twitch: Twitch = twitch
        self._broadcaster = WebSocketBroadcaster()

        # Create component managers
        self.status = StatusManager(self._broadcaster)
        self.websockets = WebsocketStatusManager(self._broadcaster)
        self.output = ConsoleOutputManager(self._broadcaster)
        self.progress = CampaignProgressManager(self._broadcaster)
        self.channels = ChannelListManager(self._broadcaster, self)
        self.inv = InventoryManager(self._broadcaster, ImageCache(self))
        self.login = LoginFormManager(self._broadcaster, self)
        self.settings = SettingsManager(self._broadcaster, twitch.settings, self.output)

        # Selected channel tracking (set by web client)
        self._selected_channel_id: int | None = None

        # Start message
        logger.info("Web GUI Manager initialized")

    def set_socketio(self, sio: AsyncServer):
        """Set the Socket.IO instance for real-time communication.

        Called by webapp during initialization to connect the broadcaster
        to the Socket.IO server.

        Args:
            sio: The Socket.IO AsyncServer instance
        """
        self._broadcaster.set_socketio(sio)

    def save(self, *, force: bool = False):
        """Save GUI state and settings.

        Args:
            force: Force save even if no changes detected
        """
        self._twitch.settings.save(force=force)

    def print(self, message: str):
        """Print message to console output.

        Args:
            message: Message to display in console
        """
        self.output.print(message)

    def set_games(self, games: set[Game]):
        """Set available games for settings panel.

        Args:
            games: Set of Game objects from discovered campaigns
        """
        self.settings.set_games(games)

    def display_drop(self, drop: TimedDrop, *, countdown: bool = True, subone: bool = False):
        """Display drop mining progress with countdown.

        Args:
            drop: The drop currently being mined
            countdown: Whether to show countdown timer
            subone: Subtract one minute from remaining time
        """
        remaining = drop.remaining_minutes * 60  # Convert to seconds
        if subone:
            remaining -= 60
        self.progress.update(drop, remaining)

    def clear_drop(self):
        """Clear the drop progress display."""
        self.progress.stop_timer()

    def grab_attention(self, *, sound: bool = True):
        """Get user's attention via notification.

        Args:
            sound: Whether to play notification sound
        """
        asyncio.create_task(self._broadcaster.emit("attention_required", {"sound": sound}))

    def select_channel(self, channel_id: int):
        """Select a channel (called by webapp when user clicks channel).

        Args:
            channel_id: Twitch channel ID to select
        """
        self._selected_channel_id = channel_id

    def get_selected_channel_id(self) -> int | None:
        """Get the currently selected channel ID and clear the selection.

        Returns:
            Channel ID if one was selected, None otherwise
        """
        result = self._selected_channel_id
        self._selected_channel_id = None  # Clear after reading
        return result

    def apply_theme(self, dark_mode: bool):
        """Apply UI theme (handled client-side in web mode).

        Args:
            dark_mode: Whether to use dark theme
        """
        asyncio.create_task(self._broadcaster.emit("theme_change", {"dark_mode": dark_mode}))

    def broadcast_manual_mode_change(self, manual_mode_info: dict):
        """Broadcast manual mode status change to connected clients.

        Args:
            manual_mode_info: Manual mode status from get_manual_mode_info()
        """
        asyncio.create_task(self._broadcaster.emit("manual_mode_update", manual_mode_info))


# Type aliases for backwards compatibility with code that imports from gui
LoginForm = LoginFormManager
ChannelList = ChannelListManager
WebsocketStatus = WebsocketStatusManager
GUIManager = WebGUIManager
