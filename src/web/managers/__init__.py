"""Web GUI manager modules for the Twitch Drops Miner web interface.

This package contains all component managers for the web-based GUI:
- WebSocketBroadcaster: Real-time message broadcasting to clients
- StatusManager: Main application status display
- WebsocketStatusManager: WebSocket connection pool status
- ConsoleOutputManager: Console log output buffering and display
- CampaignProgressManager: Active drop mining progress and countdown
- ChannelListManager: Available channels tracking and display
- InventoryManager: Drop campaigns and inventory management
- LoginFormManager: Authentication and OAuth flow handling
- SettingsManager: Application settings configuration
- ImageCache: Minimal image caching for campaign artwork
"""

from src.web.managers.broadcaster import WebSocketBroadcaster
from src.web.managers.cache import ImageCache
from src.web.managers.campaigns import CampaignProgressManager
from src.web.managers.channels import ChannelListManager
from src.web.managers.console import ConsoleOutputManager
from src.web.managers.inventory import InventoryManager
from src.web.managers.login import LoginData, LoginFormManager
from src.web.managers.settings import SettingsManager
from src.web.managers.status import StatusManager, WebsocketStatusManager


__all__ = [
    "WebSocketBroadcaster",
    "StatusManager",
    "WebsocketStatusManager",
    "ConsoleOutputManager",
    "CampaignProgressManager",
    "ChannelListManager",
    "InventoryManager",
    "LoginFormManager",
    "LoginData",
    "SettingsManager",
    "ImageCache",
]
