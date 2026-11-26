"""Notifiers enabled for one optional notification event."""

from __future__ import annotations

from typing import TYPE_CHECKING

from src.services.discord_service import DiscordNotifier
from src.services.telegram_service import TelegramNotifier


if TYPE_CHECKING:
    from src.core.client import Twitch


class NotificationTargets:
    """Build the configured Telegram/Discord notifiers for an event the user turned on."""

    @staticmethod
    def enabled(twitch: Twitch, event: str) -> list[TelegramNotifier | DiscordNotifier]:
        settings = twitch.settings
        if settings.notification_events.get(event) is not True:
            return []
        diagnostics = getattr(twitch, "diagnostics", None)
        notifiers: list[TelegramNotifier | DiscordNotifier] = [
            TelegramNotifier(
                settings.telegram_bot_token, settings.telegram_chat_id, diagnostics=diagnostics
            ),
            DiscordNotifier(settings.discord_webhook_url, diagnostics=diagnostics),
        ]
        return [notifier for notifier in notifiers if notifier.enabled]
