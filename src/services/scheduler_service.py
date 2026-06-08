"""Scheduler service for automatic pause/resume based on time of day."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, time
from typing import TYPE_CHECKING

from src.utils import task_wrapper


if TYPE_CHECKING:
    from src.core.client import Twitch


logger = logging.getLogger("TwitchDrops")


class SchedulerService:
    """
    Service responsible for automatically pausing and resuming mining
    based on a configurable time schedule.

    The scheduler runs as a background task and checks every 60 seconds
    whether the current time falls within the active or inactive mining window.

    A user can override a scheduler-initiated pause by clicking Resume.
    The override persists until the next transition from active to stop window,
    at which point the scheduler pauses mining again automatically.
    """

    def __init__(self, twitch: Twitch) -> None:
        self._twitch = twitch

    def _parse_time(self, time_str: str) -> time:
        """Parse a HH:MM string into a datetime.time object."""
        parts = time_str.strip().split(":")
        return time(int(parts[0]), int(parts[1]))

    def _should_be_paused(self) -> bool:
        """Check if the current time is outside the active mining window."""
        start = self._parse_time(self._twitch.settings.scheduler_start)
        stop = self._parse_time(self._twitch.settings.scheduler_stop)
        now = datetime.now().time()

        if start < stop:
            return not (start <= now < stop)
        else:
            return not (now >= start or now < stop)

    @task_wrapper(critical=False)
    async def run_scheduler(self) -> None:
        """Run the scheduler loop, checking every 60 seconds."""
        logger.info("Scheduler service started")
        was_paused = self._should_be_paused()

        while True:
            if self._twitch.settings.scheduler_enabled:
                should_pause = self._should_be_paused()

                # Detect transition from stop window to active window
                if not should_pause and was_paused:
                    self._twitch._user_override = False
                was_paused = should_pause

                if should_pause and not self._twitch.is_paused():
                    # Only pause if user hasn't overridden the scheduler
                    if not self._twitch._user_override:
                        logger.info("Scheduler: pausing mining (outside active window)")
                        self._twitch.pause(source="scheduler")
                elif not should_pause and self._twitch.is_paused() and self._twitch._pause_source == "scheduler":
                    logger.info("Scheduler: resuming mining (entering active window)")
                    self._twitch.resume()

            await asyncio.sleep(60)
