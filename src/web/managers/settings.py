"""Settings manager for application configuration."""

from __future__ import annotations

import asyncio
import copy
import logging
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from src.config.settings import default_settings
from src.i18n.translator import _
from src.models.game import Game
from src.utils import merge_json


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

    def get_settings(self, legacy_show_not_linked: bool | None = None) -> dict[str, Any]:
        """Get current settings for display.

        Args:
            legacy_show_not_linked: Request-scoped value to echo to a legacy
                frontend. It is never persisted or mapped to the current
                ``show_only_not_linked`` restriction.

        Returns:
            Dictionary containing all user-configurable settings
        """
        settings = vars(self._settings).copy()
        if legacy_show_not_linked is not None:
            inventory_filters = copy.deepcopy(dict(self._settings.inventory_filters))
            inventory_filters["show_not_linked"] = legacy_show_not_linked
            settings["inventory_filters"] = inventory_filters
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

    def update_settings(self, settings_data: dict[str, Any]) -> dict[str, Any]:
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
            proxy_value = settings_data["proxy"]
            should_trigger_update |= self.check_and_update_setting(
                "proxy",
                str(proxy_value).strip() if proxy_value else "",
                True,
                lambda proxy: self._log_change("Proxy cleared") if proxy == "" else None,
            )
        should_trigger_update |= self.check_and_update_setting(
            "minimum_refresh_interval_minutes",
            settings_data.get("minimum_refresh_interval_minutes"),
        )
        inventory_filters = settings_data.get("inventory_filters")
        legacy_show_not_linked = None
        if inventory_filters is not None:
            legacy_value = inventory_filters.get("show_not_linked")
            if isinstance(legacy_value, bool):
                legacy_show_not_linked = legacy_value
            inventory_filters = self._normalize_inventory_filters(inventory_filters)
        should_trigger_update |= self.check_and_update_setting("inventory_filters", inventory_filters)
        should_trigger_update |= self.check_and_update_setting(
            "inventory_list_view", settings_data.get("inventory_list_view")
        )
        should_trigger_update |= self.check_and_update_setting(
            "mining_benefits", settings_data.get("mining_benefits"), True
        )

        self._settings.save()
        response_settings = self.get_settings(legacy_show_not_linked)
        asyncio.create_task(self._broadcaster.emit("settings_updated", response_settings))

        if should_trigger_update and self._on_change:
            self._on_change()

        return response_settings

    def _normalize_inventory_filters(self, updates: dict[str, Any]) -> dict[str, Any]:
        """Merge partial filter updates and discard legacy or unknown keys."""
        current: dict[str, Any] = copy.deepcopy(dict(self._settings.inventory_filters))
        current.pop("show_not_linked", None)
        current.update(updates)
        current.pop("show_not_linked", None)
        template = default_settings["inventory_filters"]
        assert isinstance(template, dict)
        merge_json(current, template)
        return current

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
