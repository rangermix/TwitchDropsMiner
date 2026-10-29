"""Settings manager for application configuration."""

from __future__ import annotations

import asyncio
import copy
import logging
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from src.config.reward_priority import RewardPriority
from src.config.settings import InventoryFilterSettings, MiningHoursSettings, default_settings
from src.i18n.translator import _
from src.models.game import Game
from src.services.discord_service import DiscordNotifier
from src.utils import PASSWORD_MASK, DropIgnorePolicy, mask_url_password


logger = logging.getLogger("TwitchDrops")

# Value returned to web clients in place of the stored Telegram bot token.
# Never echo the real credential through the unauthenticated web API/socket.
TELEGRAM_TOKEN_MASK = "••••••••"
DISCORD_WEBHOOK_MASK = "••••••••"


def _format_mining_hours(value: Any) -> str:
    """Return a console-friendly summary that hides times outside range mode."""
    if isinstance(value, dict) and value.get("mode") == "range":
        return f"{value.get('start')}-{value.get('end')}"
    return "always"


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
        on_games_change: Callable[[], None] | None = None,
    ):
        self._broadcaster = broadcaster
        self._settings = settings
        self._console = console
        self._on_change = on_change
        self._on_games_change = on_games_change
        self._available_games: list[str] = []
        # Console lines and side effects of applied fields, run only after a successful save.
        self._pending_effects: list[Callable[[], None]] = []

    def get_settings(self, legacy_show_not_linked: bool | None = None) -> dict[str, Any]:
        """Get current settings for display.

        Args:
            legacy_show_not_linked: Request-scoped value echoed only in the
                immediate settings POST response for a legacy frontend. It is
                never persisted, mapped to ``link_status``, or returned
                by a later GET/page reload.

        Returns:
            Dictionary containing all user-configurable settings
        """
        settings = vars(self._settings).copy()
        settings["games_available"] = self._available_games
        configured_webhook = bool(settings.get("discord_webhook_url"))
        settings["discord_configured"] = configured_webhook
        settings["discord_webhook_url"] = DISCORD_WEBHOOK_MASK if configured_webhook else ""
        # Never expose the real Telegram bot token to web clients. The token
        # stays server-side; clients only see a configured flag and a mask.
        configured_token = bool(settings.get("telegram_bot_token"))
        settings["telegram_configured"] = configured_token
        settings["telegram_bot_token"] = TELEGRAM_TOKEN_MASK if configured_token else ""
        settings["proxy"] = mask_url_password(settings.get("proxy") or "")
        # TODO(remove in 1.3.x): Retain this POST-only echo long enough for stale
        # pre-versioned frontends to age out; it never survives a page reload.
        if legacy_show_not_linked is not None:
            inventory_filters = copy.deepcopy(dict(self._settings.inventory_filters))
            inventory_filters["show_not_linked"] = legacy_show_not_linked
            settings["inventory_filters"] = inventory_filters
        return settings

    def resolve_proxy(self, submitted: str) -> str:
        """Return the stored proxy when a client submits its masked form unchanged.

        An edited URL that still carries the mask must not reuse the stored password,
        which could then reach another host, nor save the mask itself as a password.
        """
        stored = getattr(self._settings, "proxy", "") or ""
        if stored and submitted == mask_url_password(stored):
            return stored
        if PASSWORD_MASK in submitted:
            raise ValueError("proxy_password_required")
        return submitted

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

        Every field is validated before any is written, so a ValueError leaves
        settings, console output and persistence untouched. A failed save restores
        every field, and change lines and side effects run only after saving.

        Args:
            settings_data: Dictionary of settings to update
        """
        previous_reward_priority = getattr(self._settings, "reward_priority", [])
        requested_reward_priority = settings_data.get("reward_priority")
        if requested_reward_priority is not None:
            requested_reward_priority = RewardPriority.normalize(requested_reward_priority)
        previous_priority_list_only = getattr(self._settings, "priority_list_only", True)
        requested_priority_list_only = settings_data.get("priority_list_only")
        priority_mode_changed = (
            isinstance(requested_priority_list_only, bool)
            and requested_priority_list_only != previous_priority_list_only
        )
        requested_hours = settings_data.get("mining_hours")
        if requested_hours is not None:
            requested_hours = MiningHoursSettings.normalize(requested_hours)
        previous_webhook = getattr(self._settings, "discord_webhook_url", "")
        new_webhook = previous_webhook
        if settings_data.get("discord_remove_webhook") is True:
            new_webhook = ""
        elif "discord_webhook_url" in settings_data:
            requested_webhook = settings_data.get("discord_webhook_url")
            if requested_webhook is not None and not isinstance(requested_webhook, str):
                raise ValueError("invalid_discord_webhook")
            requested_webhook = (requested_webhook or "").strip()
            if requested_webhook and requested_webhook != DISCORD_WEBHOOK_MASK:
                if not DiscordNotifier.is_valid_webhook(requested_webhook):
                    raise ValueError("invalid_discord_webhook")
                new_webhook = requested_webhook
        new_proxy = None
        if "proxy" in settings_data:
            proxy_value = settings_data["proxy"]
            new_proxy = self.resolve_proxy(str(proxy_value).strip()) if proxy_value else ""
        inventory_filters = settings_data.get("inventory_filters")
        legacy_show_not_linked = None
        if inventory_filters is not None:
            InventoryFilterSettings.validate_update(inventory_filters)
            legacy_value = inventory_filters.get("show_not_linked")
            if isinstance(legacy_value, bool):
                legacy_show_not_linked = legacy_value
            inventory_filters = self._normalize_inventory_filters(inventory_filters)
        language = settings_data.get("language")
        if language is not None and language not in _.get_languages():
            raise ValueError("invalid_language")
        drop_name_blacklist = settings_data.get("drop_name_blacklist")
        if drop_name_blacklist is not None:
            drop_name_blacklist = DropIgnorePolicy.normalize_keywords(
                drop_name_blacklist
            )

        snapshot = {
            key: copy.deepcopy(value)
            for key, value in vars(self._settings).items()
            if key in default_settings
        }
        self._pending_effects = []
        should_trigger_update = False
        previous_allow_unlinked = getattr(self._settings, "allow_unlinked_campaigns", False)
        requested_allow_unlinked = settings_data.get("allow_unlinked_campaigns")
        override_changed = (
            isinstance(requested_allow_unlinked, bool)
            and requested_allow_unlinked != previous_allow_unlinked
        )
        should_trigger_update |= self.check_and_update_setting(
            "games_to_watch", settings_data.get("games_to_watch"), True,
            lambda _games: self._on_games_change() if self._on_games_change else None,
        )
        should_trigger_update |= self.check_and_update_setting(
            "drop_name_blacklist", drop_name_blacklist, True
        )
        should_trigger_update |= self.check_and_update_setting(
            "dark_mode", settings_data.get("dark_mode")
        )
        should_trigger_update |= self.check_and_update_setting(
            "language", language, False, self._set_language
        )
        should_trigger_update |= self.check_and_update_setting(
            "connection_quality", settings_data.get("connection_quality")
        )
        if new_proxy is not None:
            should_trigger_update |= self.check_and_update_setting(
                "proxy",
                new_proxy,
                True,
                lambda proxy: self._log_change("Proxy cleared") if proxy == "" else None,
                mask_url_password,
            )
        should_trigger_update |= self.check_and_update_setting(
            "minimum_refresh_interval_minutes",
            settings_data.get("minimum_refresh_interval_minutes"),
        )
        if "telegram_bot_token" in settings_data:
            new_token = str(settings_data.get("telegram_bot_token") or "").strip()
            if new_token and new_token != TELEGRAM_TOKEN_MASK:
                self.check_and_update_setting(
                    "telegram_bot_token", new_token
                )
        if "telegram_chat_id" in settings_data:
            self.check_and_update_setting(
                "telegram_chat_id", settings_data.get("telegram_chat_id") or ""
            )
        should_trigger_update |= self.check_and_update_setting("inventory_filters", inventory_filters)
        should_trigger_update |= self.check_and_update_setting(
            "inventory_list_view", settings_data.get("inventory_list_view")
        )
        should_trigger_update |= self.check_and_update_setting(
            "mining_benefits", settings_data.get("mining_benefits"), True
        )
        should_trigger_update |= self.check_and_update_setting(
            "mining_hours",
            requested_hours,
            True,
            log_formatter=_format_mining_hours,
        )

        if override_changed and isinstance(requested_allow_unlinked, bool):
            self._settings.allow_unlinked_campaigns = requested_allow_unlinked
        if priority_mode_changed and isinstance(requested_priority_list_only, bool):
            self._settings.priority_list_only = requested_priority_list_only
        if requested_reward_priority is not None:
            self._settings.reward_priority = requested_reward_priority
        self._settings.discord_webhook_url = new_webhook
        try:
            self._settings.save()
        except Exception:
            # A rejected save must leave no in-memory change for a later save to persist.
            for key, value in snapshot.items():
                setattr(self._settings, key, value)
            self._pending_effects = []
            raise
        effects, self._pending_effects = self._pending_effects, []
        for effect in effects:
            effect()
        if new_webhook != previous_webhook:
            self._log_change(f"Setting changed: discord_webhook_url = {DISCORD_WEBHOOK_MASK}")
        if override_changed:
            self._log_change(
                f"Setting changed: allow_unlinked_campaigns = {requested_allow_unlinked}"
            )
            should_trigger_update = True
        if priority_mode_changed:
            self._log_change(
                f"Setting changed: priority_list_only = {requested_priority_list_only}"
            )
            should_trigger_update = True
        if requested_reward_priority is not None and requested_reward_priority != previous_reward_priority:
            should_trigger_update = True
        response_settings = self.get_settings(legacy_show_not_linked)
        asyncio.create_task(self._broadcaster.emit("settings_updated", response_settings))

        if should_trigger_update and self._on_change:
            self._on_change()

        return response_settings

    def _normalize_inventory_filters(self, updates: dict[str, Any]) -> dict[str, Any]:
        """Merge partial filter updates and discard legacy or unknown keys."""
        return dict(InventoryFilterSettings.normalize(updates, self._settings.inventory_filters))

    def check_and_update_setting(
        self,
        key: str,
        new_value: Any,
        should_trigger_update: bool = False,
        action: Callable[[Any], None] = lambda x: None,
        log_formatter: Callable[[Any], str] | None = None,
    ):
        if new_value is None or getattr(self._settings, key, None) == new_value:
            return False
        setattr(self._settings, key, new_value)
        log_value = TELEGRAM_TOKEN_MASK if key in {"telegram_bot_token", "discord_webhook_url"} else (
            log_formatter(new_value) if log_formatter else new_value
        )

        def effect() -> None:
            self._log_change(f"Setting changed: {key} = {log_value}")
            action(new_value)

        self._pending_effects.append(effect)
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
