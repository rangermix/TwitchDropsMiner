"""Login form manager for handling Twitch authentication."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from src.i18n import _


if TYPE_CHECKING:
    from src.web.gui_manager import WebGUIManager
    from src.web.managers.broadcaster import WebSocketBroadcaster


@dataclass
class LoginData:
    """Container for login credentials submitted by the user."""
    username: str
    password: str
    token: str


class LoginFormManager:
    """Manages login form and OAuth authentication flow in the web interface.

    Handles both traditional username/password login and OAuth device code flow,
    coordinating between the web client and the Twitch authentication system.
    """

    def __init__(self, broadcaster: WebSocketBroadcaster, manager: WebGUIManager):
        self._broadcaster = broadcaster
        self._manager = manager
        self._login_event = asyncio.Event()
        self._login_data: LoginData | None = None
        self._status = "Logged out"
        self._user_id: int | None = None
        self._oauth_pending: dict[str, str] | None = None  # Store OAuth code for late-connecting clients

    def clear(self, login: bool = False, password: bool = False, token: bool = False):
        """Clear login form fields on the client side.

        Args:
            login: Clear the login/username field
            password: Clear the password field
            token: Clear the 2FA token field
        """
        asyncio.create_task(
            self._broadcaster.emit("login_clear", {
                "login": login,
                "password": password,
                "token": token
            })
        )

    def update(self, status: str, user_id: int | None):
        """Update login status display.

        Args:
            status: Status message to display (e.g., "Logged in as...", "Login required")
            user_id: Twitch user ID if logged in, None otherwise
        """
        self._status = status
        self._user_id = user_id
        asyncio.create_task(
            self._broadcaster.emit("login_status", {
                "status": status,
                "user_id": user_id
            })
        )

    async def ask_login(self) -> LoginData:
        """Request login credentials from the user.

        Returns:
            LoginData containing submitted credentials
        """
        self.update(_("gui", "login", "required"), None)
        self._login_event.clear()
        await self._broadcaster.emit("login_required", {})
        # Use coro_unless_closed to handle shutdown during login
        await self._manager.coro_unless_closed(self._login_event.wait())
        return self._login_data

    async def ask_enter_code(self, page_url, user_code: str):
        """Request OAuth device code entry from the user.

        Displays the activation URL and code to the user, waiting for them
        to complete the OAuth flow on Twitch's website.

        Args:
            page_url: URL where user should enter the code (e.g., twitch.tv/activate)
            user_code: The device code to enter
        """
        self.update(_("gui", "login", "required"), None)
        self._login_event.clear()
        # Store OAuth code for late-connecting clients
        self._oauth_pending = {
            "url": str(page_url),
            "code": user_code
        }
        await self._broadcaster.emit("oauth_code_required", self._oauth_pending)
        # Use coro_unless_closed to handle shutdown during login
        await self._manager.coro_unless_closed(self._login_event.wait())
        # Clear OAuth state after confirmation
        self._oauth_pending = None

    def submit_login(self, username: str, password: str, token: str = ''):
        """Submit login credentials (called by webapp when user submits form).

        Args:
            username: Twitch username or email
            password: Account password
            token: Optional 2FA token
        """
        self._login_data = LoginData(username, password, token)
        self._login_event.set()

    def get_status(self) -> dict[str, Any]:
        """Get current login status for client synchronization.

        Returns:
            Dictionary with status, user_id, and optional oauth_pending data
        """
        result = {
            "status": self._status,
            "user_id": self._user_id
        }
        # Include OAuth code if pending
        if self._oauth_pending:
            result["oauth_pending"] = self._oauth_pending
        return result
