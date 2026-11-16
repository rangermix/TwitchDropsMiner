"""GitHub release lookup and optional new-version notifications."""

from __future__ import annotations

import asyncio
import json
import logging
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any
from urllib.parse import quote

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
    VERSION_CORE = r"v?(0|[1-9][0-9]{0,5})\.(0|[1-9][0-9]{0,5})\.(0|[1-9][0-9]{0,5})"
    VERSION_IDENTIFIERS = r"[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*"
    VERSION_PRERELEASE = rf"(?P<prerelease>-{VERSION_IDENTIFIERS})?"
    VERSION_BUILD = rf"(?:\+{VERSION_IDENTIFIERS})?"

    def __init__(self, twitch: Twitch) -> None:
        self._twitch = twitch
        self._pending_save = False

    @classmethod
    def parse_version(cls, value: Any) -> tuple[int, int, int] | None:
        """Get a stable release's precedence, ignoring the build metadata."""
        if not isinstance(value, str):
            return None
        match = re.fullmatch(cls.VERSION_CORE + cls.VERSION_BUILD, value)
        return (int(match[1]), int(match[2]), int(match[3])) if match else None

    @classmethod
    def is_newer(cls, latest: str, current: str = __version__) -> bool:
        """Compare numerically, so 2.10.0 is newer than 2.9.0."""
        latest_parts = cls.parse_version(latest)
        installed = re.fullmatch(cls.VERSION_CORE + cls.VERSION_PRERELEASE + cls.VERSION_BUILD, current)
        if latest_parts is None or installed is None:
            return False
        current_parts = (int(installed[1]), int(installed[2]), int(installed[3]))
        return latest_parts > current_parts or (
            latest_parts == current_parts and installed["prerelease"] is not None
        )

    @classmethod
    async def fetch_latest(cls, diagnostics: Diagnostics | None = None) -> Release | None:
        """Return the latest published release, or None when GitHub cannot be read."""
        try:
            async with aiohttp.ClientSession() as session, session.get(
                cls.API_URL, timeout=aiohttp.ClientTimeout(total=5), allow_redirects=False
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
        tag = data["tag_name"]
        return Release(tag.removeprefix("v"), f"{cls.RELEASES_URL}/tag/{quote(tag, safe='')}")

    async def run(self) -> None:
        """Check at startup, then periodically, until cancelled."""
        while True:
            try:
                await self.check()
            except Exception as e:
                logger.warning("Release notification check failed: %s", type(e).__name__)
            await asyncio.sleep(self.CHECK_INTERVAL)

    def _notifiers(self) -> dict[str, TelegramNotifier | DiscordNotifier]:
        settings = self._twitch.settings
        if settings.notification_events.get("new_release") is not True:
            return {}
        diagnostics = self._twitch.diagnostics
        notifiers: dict[str, TelegramNotifier | DiscordNotifier] = {
            "telegram": TelegramNotifier(
                settings.telegram_bot_token, settings.telegram_chat_id, diagnostics=diagnostics
            ),
            "discord": DiscordNotifier(settings.discord_webhook_url, diagnostics=diagnostics),
        }
        return {provider: notifier for provider, notifier in notifiers.items() if notifier.enabled}

    def _save_deliveries(self) -> None:
        if self._pending_save:
            try:
                self._twitch.settings.save()
            except Exception as error:
                logger.warning("Failed to save release notifications: %s", type(error).__name__)
            else:
                self._pending_save = False

    async def _notify(self, provider: str, release: Release) -> bool:
        # A user can disable alerts or change the destination during the GitHub lookup.
        notifier = self._notifiers().get(provider)
        settings = self._twitch.settings
        previous = settings.last_notified_releases[provider]
        if notifier is None or (
            self.parse_version(previous) is not None and not self.is_newer(release.version, previous)
        ):
            return False
        try:
            accepted = await notifier.notify_new_release(release.version, release.url)
        except Exception as error:
            logger.warning("Release notification failed for %s: %s", provider, type(error).__name__)
            return False
        if not accepted:
            return False
        # Persist each success before waiting for the other provider. Keep the in-memory
        # value even if the disk write fails so the next check can save without resending.
        settings.last_notified_releases = {
            **settings.last_notified_releases,
            provider: release.version,
        }
        self._pending_save = True
        self._save_deliveries()
        return True

    async def check(self) -> bool:
        """Notify about a newer release once per provider."""
        self._save_deliveries()
        if not self._notifiers():
            return False
        release = await self.fetch_latest(self._twitch.diagnostics)
        if release is None or not self.is_newer(release.version):
            return False
        results = await asyncio.gather(
            self._notify("telegram", release), self._notify("discord", release)
        )
        return any(results)
