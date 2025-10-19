"""Settings manager for application configuration."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any

from src.models.game import Game


if TYPE_CHECKING:
    from src.config.settings import Settings
    from src.web.managers.broadcaster import WebSocketBroadcaster


class SettingsManager:
    """Manages application settings in the web interface.

    Provides access to and modification of user preferences including
    game priorities, proxy configuration, and UI preferences.
    """

    def __init__(self, broadcaster: WebSocketBroadcaster, settings: Settings):
        self._broadcaster = broadcaster
        self._settings = settings
        self._available_games: list[str] = []

    def get_settings(self) -> dict[str, Any]:
        """Get current settings for display.

        Returns:
            Dictionary containing all user-configurable settings
        """
        return {
            "language": self._settings.language,
            "dark_mode": self._settings.dark_mode,
            "games_to_watch": list(self._settings.games_to_watch),
            "games_available": self._available_games,
            "proxy": str(self._settings.proxy),
            "connection_quality": self._settings.connection_quality,
            "minimum_refresh_interval_minutes": self._settings.minimum_refresh_interval_minutes,
        }

    def update_settings(self, settings_data: dict[str, Any]):
        """Update settings from user input.

        Args:
            settings_data: Dictionary of settings to update
        """
        if "games_to_watch" in settings_data:
            self._settings.games_to_watch = settings_data["games_to_watch"]
        if "dark_mode" in settings_data:
            self._settings.dark_mode = settings_data["dark_mode"]
        if "connection_quality" in settings_data:
            self._settings.connection_quality = settings_data["connection_quality"]
        if "proxy" in settings_data:
            self._settings.proxy = settings_data["proxy"]
        if "minimum_refresh_interval_minutes" in settings_data:
            self._settings.minimum_refresh_interval_minutes = settings_data[
                "minimum_refresh_interval_minutes"
            ]
        self._settings.alter()
        asyncio.create_task(self._broadcaster.emit("settings_updated", self.get_settings()))

    def set_games(self, games: set[Game]):
        """Update the list of available games for settings panel.

        Args:
            games: Set of Game objects discovered from campaigns
        """
        # Store and broadcast available games for settings panel
        game_names = sorted([g.name for g in games])
        self._available_games = game_names
        asyncio.create_task(self._broadcaster.emit("games_available", {"games": game_names}))
