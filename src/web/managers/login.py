"""Sanitized Twitch login status for the integrated browser."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any

from src.i18n import _


if TYPE_CHECKING:
    from src.web.gui_manager import WebGUIManager
    from src.web.managers.broadcaster import WebSocketBroadcaster


class LoginFormManager:
    """Keep login status synchronized without handling browser credentials."""

    def __init__(self, broadcaster: WebSocketBroadcaster, manager: WebGUIManager):
        self._broadcaster = broadcaster
        self._manager = manager
        self._status = _.t["login"]["status"]["logged_out"]
        self._user_id: int | None = None
        self._import_pending = False

    def update(self, status: str, user_id: int | None):
        """Publish the current identity and clear a completed login prompt."""
        self._status = status
        self._user_id = user_id
        if user_id is not None:
            self._import_pending = False
        asyncio.create_task(self._broadcaster.emit("login_status", self.get_status()))

    def get_status(self) -> dict[str, Any]:
        """Return only public identity and waiting state for reconnects."""
        result: dict[str, Any] = {"status": self._status, "user_id": self._user_id}
        if self._import_pending:
            result["import_pending"] = True
        return result

    async def import_pending(self, pending: bool) -> None:
        """Keep only a waiting flag in dashboard broadcasts, never the session."""
        if self._import_pending == pending:
            return
        self._import_pending = pending
        if pending:
            self._status = _.t["login"]["status"]["required"]
            self._user_id = None
            self._manager._twitch.request_login()
        await self._broadcaster.emit("login_status", self.get_status())
