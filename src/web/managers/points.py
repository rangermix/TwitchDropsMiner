"""Channel points tab: the listed channels, the points log and the separate pause."""

from __future__ import annotations

import asyncio
from datetime import datetime
from typing import TYPE_CHECKING, Any, cast

from src.i18n.translator import _


if TYPE_CHECKING:
    from src.core.client import Twitch
    from src.web.managers.broadcaster import WebSocketBroadcaster
    from src.web.managers.console import ConsoleOutputManager


class PointsManager:
    """State of the channel points tab and its log, broadcast to web clients when it changes.

    The idle watch service owns the balances and the pause flag; this manager keeps the last gain
    of each listed channel and turns both into the `points_update` state.
    """

    def __init__(self, broadcaster: WebSocketBroadcaster, twitch: Twitch, log: ConsoleOutputManager):
        self._broadcaster = broadcaster
        self._twitch = twitch
        self._log = log
        # login -> {"points", "reason", "at"} of the last gain
        self._gains: dict[str, dict[str, Any]] = {}
        # login -> channel id, so a known balance stays shown after a cleanup untracks the channel
        self._ids: dict[str, int] = {}
        self._sent: dict[str, Any] | None = None

    def log(self, key: str, **values: object) -> None:
        """Add a translated `points.log.<key>` line to the points log."""
        lines = cast("dict[str, str]", _.t["points"]["log"])
        self._log.print(lines[key].format(**values))

    def gain(self, login: str, points: int, reason: str) -> None:
        self._gains[login] = {"points": points, "reason": reason, "at": datetime.now().astimezone().isoformat()}
        self.refresh()

    def clear(self) -> None:
        """Forget the last gains on an account change; balances are cleared by the service."""
        self._gains.clear()
        self._ids.clear()
        self.refresh()

    def get_state(self) -> dict[str, Any]:
        tw = self._twitch
        idle = tw.idle_watch
        by_login = {c.login: c for c in tw.channels.values()}
        channels = []
        for login in tw.settings.idle_channels:
            channel = by_login.get(login)
            if channel is not None:
                self._ids[login] = channel.id
            channel_id = self._ids.get(login)
            channels.append({
                "login": login,
                "name": channel.name if channel is not None else login,
                "live": channel is not None and channel.online,
                "balance": idle.balances.get(channel_id) if channel_id is not None else None,
                "last_gain": self._gains.get(login),
            })
        return {"paused": idle.points_paused, "active": idle.active, "channels": channels}

    def get_initial(self) -> dict[str, Any]:
        return {**self.get_state(), "log": self._log.get_history()}

    def refresh(self) -> None:
        """Broadcast `points_update` when the state differs from the last one sent."""
        state = self.get_state()
        if state != self._sent:
            self._sent = state
            asyncio.create_task(self._broadcaster.emit("points_update", state))

    def paused_changed(self, paused: bool) -> None:
        self.log("paused" if paused else "resumed")
        asyncio.create_task(self._broadcaster.emit("points_paused_update", {"paused": paused}))
        self.refresh()
