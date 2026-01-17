"""Settings manager for application configuration."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from pprint import pprint
from typing import TYPE_CHECKING, Any

from yarl import URL

from src.i18n.translator import _
from src.models.game import Game


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
        settings = self._settings.__dict__.copy()
        return settings

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
        should_trigger_update |= self.check_and_update_setting(
            "games_to_watch", settings_data.get("games_to_watch"), True
        )
        should_trigger_update |= self.check_and_update_setting(
            "dark_mode", settings_data.get("dark_mode")
        )
        should_trigger_update |= self.check_and_update_setting(
            "language", settings_data.get("language"), False, self._set_language
        )
        should_trigger_update |= self.check_and_update_setting(
            "connection_quality", settings_data.get("connection_quality")
        )
        if "proxy" in settings_data:
            should_trigger_update |= self.check_and_update_setting(
                "proxy",
                URL(settings_data.get("proxy").strip()),
                True,
                lambda proxy: self._log_change("Proxy cleared") if proxy == URL() else None,
            )
        should_trigger_update |= self.check_and_update_setting(
            "minimum_refresh_interval_minutes",
            settings_data.get("minimum_refresh_interval_minutes"),
        )
        should_trigger_update |= self.check_and_update_setting(
            "inventory_filters", settings_data.get("inventory_filters")
        )
        should_trigger_update |= self.check_and_update_setting(
            "mining_benefits", settings_data.get("mining_benefits"), True
        )

        self._settings.save()
        asyncio.create_task(self._broadcaster.emit("settings_updated", self.get_settings()))

        if should_trigger_update and self._on_change:
            self._on_change()

    def check_and_update_setting(
        self,
        key: str,
        new_value: Any,
        should_trigger_update: bool = False,
        action: Callable[[Any], None] = lambda x: None,
    ):
        if new_value is None or getattr(self._settings, key, None) == new_value:
            return False
        setattr(self._settings, key, new_value)
        self._log_change(f"Setting changed: {key} = {new_value}")
        action(new_value)
        return should_trigger_update

    def _set_language(self, language: str):
        _.set_language(language)
        # Notify clients that translations need to be reloaded
        asyncio.create_task(self._broadcaster.emit("language_changed", {"language": language}))

    def set_games(self, games: set[Game]):
        """Update the list of available games for settings panel.

        Args:
            games: Set of Game objects discovered from campaigns
        """
        # Store and broadcast available games for settings panel
        game_names = sorted([g.name for g in games])
        self._available_games = game_names
        asyncio.create_task(self._broadcaster.emit("games_available", {"games": game_names}))
