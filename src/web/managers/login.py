"""Sanitized Twitch login status for the integrated browser."""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Any

from src.config import GQLRawQuery
from src.i18n import _


if TYPE_CHECKING:
    from src.web.gui_manager import WebGUIManager
    from src.web.managers.broadcaster import WebSocketBroadcaster


logger = logging.getLogger("TwitchDrops")

CURRENT_USER_QUERY = "query CurrentUser { currentUser { id login displayName profileImageURL(width: 300) } }"


class LoginFormManager:
    """Keep login status synchronized without handling browser credentials."""

    def __init__(self, broadcaster: WebSocketBroadcaster, manager: WebGUIManager):
        self._broadcaster = broadcaster
        self._manager = manager
        self._status = _.t["login"]["status"]["logged_out"]
        self._user_id: int | None = None
        self._avatar_url: str | None = None
        self._avatar_attempted = False
        self._avatar_generation = 0
        self._avatar_tasks: set[asyncio.Task[None]] = set()
        self._import_pending = False

    def update(self, status: str, user_id: int | None):
        """Publish the current identity and clear a completed login prompt."""
        self._status = status
        if self._user_id != user_id:
            self._reset_avatar()
        self._user_id = user_id
        if user_id is not None:
            self._import_pending = False
            if not self._avatar_attempted:
                self._avatar_attempted = True
                task = asyncio.create_task(self._refresh_avatar(user_id, self._avatar_generation))
                self._avatar_tasks.add(task)
                task.add_done_callback(self._avatar_tasks.discard)
        asyncio.create_task(self._broadcaster.emit("login_status", self.get_status()))

    def get_status(self) -> dict[str, Any]:
        """Return only public identity and waiting state for reconnects."""
        result: dict[str, Any] = {"status": self._status, "user_id": self._user_id}
        if self._avatar_url is not None:
            result["avatar_url"] = self._avatar_url
        if self._import_pending:
            result["import_pending"] = True
        return result

    def _reset_avatar(self) -> None:
        self._avatar_generation += 1
        self._avatar_url = None
        self._avatar_attempted = False
        for task in self._avatar_tasks:
            task.cancel()

    async def stop_avatar(self) -> None:
        """Drain account-specific requests before identity replacement or teardown."""
        tasks = tuple(self._avatar_tasks)
        self._reset_avatar()
        await asyncio.gather(*tasks, return_exceptions=True)

    async def _refresh_avatar(self, user_id: int, generation: int) -> None:
        """Fetch the account profile image and republish the login status."""
        avatar_url: str | None = None
        try:
            twitch = getattr(self._manager, "_twitch", None)
            gql = getattr(twitch, "_gql_client", None)
            if gql is not None:
                response = await asyncio.wait_for(gql.request(GQLRawQuery(CURRENT_USER_QUERY)), 10)
                data = response.get("data") if isinstance(response, dict) else None
                if isinstance(data, dict):
                    current_user = data.get("currentUser")
                    candidate = current_user.get("profileImageURL") if isinstance(current_user, dict) else None
                    if (isinstance(current_user, dict) and str(current_user.get("id")) == str(user_id)
                            and isinstance(candidate, str) and candidate.startswith("https://")):
                        avatar_url = candidate
        except Exception:
            logger.debug("Failed to refresh account avatar")
        if self._user_id != user_id or self._avatar_generation != generation:
            return
        if avatar_url is not None:
            self._avatar_url = avatar_url
        await self._broadcaster.emit("login_status", self.get_status())

    async def import_pending(self, pending: bool) -> None:
        """Keep only a waiting flag in dashboard broadcasts, never the session."""
        if self._import_pending == pending:
            return
        self._import_pending = pending
        if pending:
            self._status = _.t["login"]["status"]["required"]
            self._user_id = None
            self._reset_avatar()
            self._manager._twitch.request_login()
        await self._broadcaster.emit("login_status", self.get_status())
