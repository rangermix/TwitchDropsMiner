"""Optional alert when a wanted campaign with unclaimed drops is about to end."""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import TYPE_CHECKING

from dateutil.parser import isoparse

from src.services.discord_service import DiscordNotifier
from src.services.telegram_service import TelegramNotifier
from src.utils import json_save


if TYPE_CHECKING:
    from src.core.client import Twitch
    from src.models.campaign import DropsCampaign


logger = logging.getLogger("TwitchDrops")


class CampaignEndingAlerts:
    """Send one Discord/Telegram alert per campaign that ends within ``WINDOW``.

    Only campaigns of wanted games that still have drops TDM would mine are reported.
    Alerted campaign IDs are kept in ``campaign_alerts.json`` (with their end time, so
    entries expire) so a restart does not repeat an alert.
    """

    WINDOW = timedelta(hours=24)
    FILENAME = "campaign_alerts.json"

    def __init__(self, twitch: Twitch, data_dir: Path) -> None:
        self._twitch = twitch
        self._path = data_dir / self.FILENAME
        self._alerted: dict[str, datetime] = self._load()

    def _load(self) -> dict[str, datetime]:
        try:
            with self._path.open(encoding="utf-8") as file:
                data = json.load(file)
            return {
                str(campaign_id): isoparse(ends_at) for campaign_id, ends_at in data.items()
            }
        except FileNotFoundError:
            return {}
        except (OSError, ValueError, TypeError, AttributeError) as error:
            # Losing this list can only repeat an alert, never lose user data.
            logger.warning("Campaign alert history is unreadable: %s", type(error).__name__)
            return {}

    def _save(self) -> None:
        try:
            json_save(self._path, {cid: ends.isoformat() for cid, ends in self._alerted.items()})
        except OSError as error:
            logger.warning("Failed to save campaign alert history: %s", type(error).__name__)

    def _notifiers(self) -> list[TelegramNotifier | DiscordNotifier]:
        settings = self._twitch.settings
        if settings.notification_events.get("campaign_ending") is not True:
            return []
        diagnostics = getattr(self._twitch, "diagnostics", None)
        notifiers: list[TelegramNotifier | DiscordNotifier] = [
            TelegramNotifier(
                settings.telegram_bot_token, settings.telegram_chat_id, diagnostics=diagnostics
            ),
            DiscordNotifier(settings.discord_webhook_url, diagnostics=diagnostics),
        ]
        return [notifier for notifier in notifiers if notifier.enabled]

    def due(self, now: datetime | None = None) -> list[DropsCampaign]:
        """Return wanted, still-mineable campaigns ending within the window and not yet alerted."""
        now = now or datetime.now(timezone.utc)
        wanted = set(self._twitch.wanted_games)
        return [
            campaign
            for campaign in self._twitch.inventory
            if campaign.game in wanted
            and campaign.eligible
            and campaign.active
            and not campaign.mining_finished
            and campaign.ends_at <= now + self.WINDOW
            and campaign.id not in self._alerted
        ]

    async def check(self) -> None:
        """Alert about due campaigns; delivery failures are retried on the next check."""
        notifiers = self._notifiers()
        if not notifiers:
            return
        now = datetime.now(timezone.utc)
        expired = [cid for cid, ends_at in self._alerted.items() if ends_at <= now]
        for campaign_id in expired:
            del self._alerted[campaign_id]
        changed = bool(expired)
        for campaign in self.due(now):
            results = await asyncio.gather(
                *(notifier.notify_campaign_ending(campaign) for notifier in notifiers),
                return_exceptions=True,
            )
            if any(result is True for result in results):
                self._alerted[campaign.id] = campaign.ends_at
                changed = True
        if changed:
            self._save()
