"""Optional alerts when the miner needs a Twitch login or stops mining."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from time import monotonic
from typing import TYPE_CHECKING

from src.services.notification_targets import NotificationTargets


if TYPE_CHECKING:
    from src.core.client import Twitch
    from src.services.discord_service import DiscordNotifier
    from src.services.telegram_service import TelegramNotifier


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
            "TwitchDropsMiner is not logged in to Twitch. Mining is stopped; check the "
            "dashboard, you may need to sign in again.",
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
        # Tracked by ID: a channel reload replaces the object for the same channel.
        self._watched_id: int | None = None
        self._watched_since = 0.0

    def _notifiers(self) -> list[TelegramNotifier | DiscordNotifier]:
        return NotificationTargets.enabled(self._twitch, "mining_problems")

    def _active(self, now: float) -> dict[str, bool]:
        """Return which problems are currently present."""
        twitch = self._twitch
        logged_in = twitch._auth_state.logged_in
        watched = twitch.watching_channel.get_with_default(None)
        watched_id = watched.id if watched is not None else None
        if watched_id != self._watched_id:
            self._watched_id, self._watched_since = watched_id, now
        last_success = max(self._watched_since, twitch._watch_service.last_watch_success or 0.0)
        return {
            "login": not logged_in,
            "stalled": logged_in and watched is not None and now - last_success >= self.STALL_DELAY,
        }

    def _recovered(self, name: str, problem: _Problem) -> bool:
        """Whether a cleared problem really recovered (and was not just replaced)."""
        if name == "login":
            return True  # Only clears when logged in again.
        last_success = self._twitch._watch_service.last_watch_success
        return (
            last_success is not None and problem.started is not None
            and last_success > problem.started
        )

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
            self._watched_id = None
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
                if problem.alerted and self._recovered(name, problem):
                    # A failed recovery message is not retried; the problem is gone.
                    await self._send(name, resolved=True)
                # Other clears (channel gone, logged out, hours reopened) reset silently.
                self._problems[name] = _Problem()

    async def run(self) -> None:
        """Check every minute until cancelled."""
        while True:
            try:
                await self.check()
            except Exception as error:
                logger.warning("Health check failed: %s", type(error).__name__)
            await asyncio.sleep(self.CHECK_INTERVAL)
