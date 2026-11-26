"""Optional alerts when the miner needs a Twitch login or stops mining."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from time import monotonic
from typing import TYPE_CHECKING

from src.services.discord_service import DiscordNotifier
from src.services.telegram_service import TelegramNotifier


if TYPE_CHECKING:
    from src.core.client import Twitch


logger = logging.getLogger("TwitchDrops")


@dataclass
class _Problem:
    """One alert condition: when it began, and whether its alert was delivered."""

    started: float | None = None
    alerted: bool = False


class HealthMonitor:
    """Send one alert when a problem lasts too long, and one when it clears.

    Problems are only tracked inside mining hours (and outside a dashboard pause):
    - ``login``: Twitch login has been needed for ``LOGIN_DELAY`` seconds.
    - ``stalled``: a channel is selected but no watch has succeeded for ``STALL_DELAY``.
    """

    CHECK_INTERVAL = 60
    LOGIN_DELAY = 30 * 60
    STALL_DELAY = 15 * 60
    MESSAGES = {
        "login": (
            "TwitchDropsMiner needs a new Twitch login. Mining is stopped until you sign in "
            "on the dashboard.",
            "Twitch login restored. Mining continues.",
        ),
        "stalled": (
            "Mining has stalled: no successful watch for 15 minutes.",
            "Mining resumed: watching succeeds again.",
        ),
    }

    def __init__(self, twitch: Twitch) -> None:
        self._twitch = twitch
        self._problems = {name: _Problem() for name in self.MESSAGES}
        self._watched: object | None = None
        self._watched_since = 0.0

    def _notifiers(self) -> list[TelegramNotifier | DiscordNotifier]:
        settings = self._twitch.settings
        if settings.notification_events.get("mining_problems") is not True:
            return []
        diagnostics = getattr(self._twitch, "diagnostics", None)
        notifiers: list[TelegramNotifier | DiscordNotifier] = [
            TelegramNotifier(
                settings.telegram_bot_token, settings.telegram_chat_id, diagnostics=diagnostics
            ),
            DiscordNotifier(settings.discord_webhook_url, diagnostics=diagnostics),
        ]
        return [notifier for notifier in notifiers if notifier.enabled]

    def _active(self, now: float) -> dict[str, bool]:
        """Return which problems are currently present."""
        twitch = self._twitch
        logged_in = twitch._auth_state.logged_in
        watched = twitch.watching_channel.get_with_default(None)
        if watched is not self._watched:
            self._watched, self._watched_since = watched, now
        last_success = max(self._watched_since, twitch._watch_service.last_watch_success or 0.0)
        return {
            "login": not logged_in,
            "stalled": logged_in and watched is not None and now - last_success >= self.STALL_DELAY,
        }

    async def _send(self, name: str, resolved: bool) -> bool:
        notifiers = self._notifiers()
        if not notifiers:
            return False
        message = self.MESSAGES[name][resolved]
        results = await asyncio.gather(
            *(notifier.notify_health(message, resolved=resolved) for notifier in notifiers),
            return_exceptions=True,
        )
        return any(result is True for result in results)

    async def check(self, now: float | None = None) -> None:
        """Update every problem and send due alerts."""
        now = monotonic() if now is None else now
        if not self._twitch.is_mining_time():
            # Closed hours or a pause: not a problem, and not a recovery either.
            self._watched = None
            for problem in self._problems.values():
                if not problem.alerted:
                    problem.started = None
            return
        delays = {"login": self.LOGIN_DELAY, "stalled": 0}
        for name, present in self._active(now).items():
            problem = self._problems[name]
            if present:
                if problem.started is None:
                    problem.started = now
                if not problem.alerted and now - problem.started >= delays[name]:
                    problem.alerted = await self._send(name, resolved=False)
            else:
                if problem.alerted:
                    # A failed recovery message is not retried; the problem is gone.
                    await self._send(name, resolved=True)
                self._problems[name] = _Problem()

    async def run(self) -> None:
        """Check every minute until cancelled."""
        while True:
            try:
                await self.check()
            except Exception as error:
                logger.warning("Health check failed: %s", type(error).__name__)
            await asyncio.sleep(self.CHECK_INTERVAL)
