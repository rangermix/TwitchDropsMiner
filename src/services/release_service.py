"""GitHub release lookup and optional new-version notifications."""

from __future__ import annotations

import asyncio
import json
import logging
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import aiohttp

from src.diagnostics import Diagnostics
from src.services.discord_service import DiscordNotifier
from src.services.telegram_service import TelegramNotifier
from src.version import __version__


if TYPE_CHECKING:
    from src.core.client import Twitch


logger = logging.getLogger("TwitchDrops")


@dataclass(frozen=True)
class Release:
    version: str
    url: str


class ReleaseService:
    """Look up the latest published release and announce each newer version once."""

    API_URL = "https://api.github.com/repos/rangermix/TwitchDropsMiner/releases/latest"
    RELEASES_URL = "https://github.com/rangermix/TwitchDropsMiner/releases"
    CHECK_INTERVAL = 6 * 3600  # seconds

    def __init__(self, twitch: Twitch) -> None:
        self._twitch = twitch

    @staticmethod
    def parse_version(value: Any) -> tuple[int, int, int] | None:
        """Parse ``1.2.3`` or ``v1.2.3``; anything else is not a release version."""
        if not isinstance(value, str):
            return None
        match = re.fullmatch(r"v?(\d{1,6})\.(\d{1,6})\.(\d{1,6})", value)
        return (int(match[1]), int(match[2]), int(match[3])) if match else None

    @classmethod
    def is_newer(cls, latest: str, current: str = __version__) -> bool:
        """Compare numerically, so 2.10.0 is newer than 2.9.0."""
        latest_parts, current_parts = cls.parse_version(latest), cls.parse_version(current)
        return latest_parts is not None and current_parts is not None and latest_parts > current_parts

    @classmethod
    async def fetch_latest(cls, diagnostics: Diagnostics | None = None) -> Release | None:
        """Return the latest published release, or None when GitHub cannot be read."""
        try:
            async with aiohttp.ClientSession() as session, session.get(
                cls.API_URL, timeout=aiohttp.ClientTimeout(total=10)
            ) as response:
                body = await Diagnostics.read_bounded(response)
                if diagnostics is not None:
                    diagnostics.record_http("GET", cls.API_URL, response.status, body=body)
                if response.status != 200 or len(body) > Diagnostics.MAX_BODY_BYTES:
                    return None
        except Exception as e:
            if diagnostics is not None:
                diagnostics.record(
                    "github_version", outcome="timeout" if isinstance(e, TimeoutError) else "connection"
                )
            logger.warning("Failed to check for updates: %s", type(e).__name__)
            return None
        try:
            data = json.loads(body)
        except (ValueError, RecursionError):
            return None
        if not isinstance(data, dict) or cls.parse_version(data.get("tag_name")) is None:
            return None
        version = data["tag_name"].removeprefix("v")
        # Only link to this repository's release page; the value ends up in messages.
        url = data.get("html_url")
        if not isinstance(url, str) or not re.fullmatch(
            re.escape(cls.RELEASES_URL) + r"/tag/v?\d{1,6}\.\d{1,6}\.\d{1,6}", url
        ):
            url = cls.RELEASES_URL
        return Release(version, url)

    async def run(self) -> None:
        """Check at startup, then periodically, until cancelled."""
        while True:
            try:
                await self.check()
            except Exception as e:
                logger.warning("Release notification check failed: %s", type(e).__name__)
            await asyncio.sleep(self.CHECK_INTERVAL)

    async def check(self) -> bool:
        """Notify about a newer release once; return whether any channel accepted it."""
        settings = self._twitch.settings
        if not settings.notification_events.get("new_release"):
            return False
        diagnostics = self._twitch.diagnostics
        notifiers = [
            notifier
            for notifier in (
                TelegramNotifier(
                    settings.telegram_bot_token, settings.telegram_chat_id, diagnostics=diagnostics
                ),
                DiscordNotifier(settings.discord_webhook_url, diagnostics=diagnostics),
            )
            if notifier.enabled
        ]
        if not notifiers:
            return False
        release = await self.fetch_latest(diagnostics)
        if (
            release is None
            or not self.is_newer(release.version)
            or release.version == settings.last_notified_release
        ):
            return False
        results = await asyncio.gather(
            *(notifier.notify_new_release(release.version, release.url) for notifier in notifiers)
        )
        # ponytail: one delivery marks the version as announced; a channel that failed
        # at the same time is not retried. Track per channel if that ever matters.
        if not any(results):
            return False
        settings.last_notified_release = release.version
        settings.save()
        return True
