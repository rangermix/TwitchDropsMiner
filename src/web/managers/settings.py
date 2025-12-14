"""Settings manager for application configuration."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any, Callable

from src.i18n.translator import _
from src.models.game import Game
import logging

logger = logging.getLogger("TwitchDrops")



if TYPE_CHECKING:
    from src.config.settings import Settings
    from src.web.managers.broadcaster import WebSocketBroadcaster
    from src.web.managers.console import ConsoleOutputManager


class SettingsManager:
    """Manages application settings in the web interface.

    Provides access to and modification of user preferences including
    game priorities, proxy configuration, and UI preferences.
    """

    def __init__(
        self,
        broadcaster: WebSocketBroadcaster,
        settings: Settings,
        console: ConsoleOutputManager,
        on_change: Callable[[], None] | None = None,
    ):
        self._broadcaster = broadcaster
        self._settings = settings
        self._console = console
        self._on_change = on_change
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
            "telegram_bot_token": self._settings.telegram_bot_token,
            "telegram_chat_id": self._settings.telegram_chat_id,
            "inventory_filters": self._settings.inventory_filters,
            "mining_benefits": self._settings.mining_benefits,
        }

    def get_languages(self) -> dict[str, Any]:
        """Get available languages and current selection.

        Returns:
            Dictionary with available languages and current language
        """
        return {
            "available": _.get_languages(),
            "current": _.current_language,
        }

    def _log_change(self, message: str):
        """Log setting change to both console and system logger."""
        self._console.print(message)

    def update_settings(self, settings_data: dict[str, Any]):
        """Update settings from user input.

        Args:
            settings_data: Dictionary of settings to update
        """
        should_trigger_update = False

        if "games_to_watch" in settings_data:
            self._settings.games_to_watch = settings_data["games_to_watch"]
            self._log_change(f"Setting changed: games_to_watch = {len(self._settings.games_to_watch)} games")
            should_trigger_update = True
            
        if "dark_mode" in settings_data:
            self._settings.dark_mode = settings_data["dark_mode"]
            self._log_change(f"Setting changed: dark_mode = {self._settings.dark_mode}")

        if "language" in settings_data:
            language = settings_data["language"]
            try:
                _.set_language(language)
                self._settings.language = language
                self._log_change(f"Setting changed: language = {language}")
                # Notify clients that translations need to be reloaded
                asyncio.create_task(
                    self._broadcaster.emit("language_changed", {"language": language})
                )
            except ValueError as e:
                # Invalid language, log warning
                logger.warning(f"Invalid language '{language}': {e}")
                
        if "connection_quality" in settings_data:
            self._settings.connection_quality = settings_data["connection_quality"]
            self._log_change(f"Setting changed: connection_quality = {self._settings.connection_quality}")

        if "proxy" in settings_data:
            from yarl import URL

            proxy_str = settings_data["proxy"].strip()
            if proxy_str:
                if self._settings.proxy != URL(proxy_str):
                    self._settings.proxy = URL(proxy_str)
                    self._log_change(f"Proxy set to: {proxy_str}")
            else:
                if self._settings.proxy != URL():
                    self._settings.proxy = URL()
                    self._log_change("Proxy cleared")

        if "minimum_refresh_interval_minutes" in settings_data:
            self._settings.minimum_refresh_interval_minutes = settings_data[
                "minimum_refresh_interval_minutes"
            ]
        if "telegram_bot_token" in settings_data:
            self._settings.telegram_bot_token = settings_data["telegram_bot_token"] or ""
        if "telegram_chat_id" in settings_data:
            self._settings.telegram_chat_id = settings_data["telegram_chat_id"] or ""
            self._log_change(f"Setting changed: minimum_refresh_interval_minutes = {self._settings.minimum_refresh_interval_minutes}")
            
        if "inventory_filters" in settings_data:
            self._settings.inventory_filters = settings_data["inventory_filters"]
            self._log_change("Setting changed: inventory_filters updated")
            
        if "mining_benefits" in settings_data:
            self._settings.mining_benefits = settings_data["mining_benefits"]
            self._log_change(f"Setting changed: mining_benefits = {self._settings.mining_benefits}")
            should_trigger_update = True

        self._settings.alter()
        # Persist settings to disk immediately
        self._settings.save()
        asyncio.create_task(self._broadcaster.emit("settings_updated", self.get_settings()))

        if should_trigger_update and self._on_change:
            self._on_change()

    def set_games(self, games: set[Game]):
        """Update the list of available games for settings panel.

        Args:
            games: Set of Game objects discovered from campaigns
        """
        # Store and broadcast available games for settings panel
        game_names = sorted([g.name for g in games])
        self._available_games = game_names
        asyncio.create_task(self._broadcaster.emit("games_available", {"games": game_names}))
