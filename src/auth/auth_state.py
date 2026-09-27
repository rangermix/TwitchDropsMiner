"""Authentication state management for Twitch Drops Miner."""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, cast

import aiohttp

from src.auth.browser_session import BrowserIdentity, browser_error
from src.auth.imported_session import ImportedSession
from src.config import COOKIES_PATH, ClientInfo, ClientType
from src.i18n import _
from src.utils import CHARS_HEX_LOWER, create_nonce


if TYPE_CHECKING:
    from src.config import ClientInfo, JsonType
    from src.core.client import Twitch


logger = logging.getLogger("TwitchDrops")


class _AuthState:
    """
    Manages authentication state including tokens, session, and login flow.

    This class handles:
    - Helper-assisted fresh login and legacy saved-session restoration
    - Access token validation and management
    - Session and device ID management
    - Cookie persistence
    """

    def __init__(self, twitch: Twitch):
        self._twitch: Twitch = twitch
        self._lock = asyncio.Lock()
        self._logged_in = asyncio.Event()
        self.user_id: int
        self.device_id: str
        self.session_id: str
        self.access_token: str
        self.client_version: str
        self.browser_active = False

    def _hasattrs(self, *attrs: str) -> bool:
        """Check if all specified attributes exist."""
        return all(hasattr(self, attr) for attr in attrs)

    def _delattrs(self, *attrs: str) -> None:
        """Delete all specified attributes if they exist."""
        for attr in attrs:
            if hasattr(self, attr):
                delattr(self, attr)

    def clear(self) -> None:
        """Clear all authentication state."""
        self._delattrs(
            "user_id",
            "device_id",
            "session_id",
            "access_token",
            "client_version",
        )
        self._logged_in.clear()
        self.browser_active = False

    async def _browser_login(self, expected_user_id: int | None = None) -> None:
        if expected_user_id is None:
            expected_user_id = getattr(self, "user_id", None)
        browser = self._twitch._browser
        assert browser is not None
        if isinstance(browser, ImportedSession):
            browser.bind_account(expected_user_id)
        identity = await browser.authenticate(self._twitch.gui.login)
        if expected_user_id is not None and identity.user_id != expected_user_id:
            await browser.close()
            raise browser_error("ACCOUNT_MISMATCH")
        self._use_browser_identity(identity)

    def _use_browser_identity(self, identity: BrowserIdentity) -> None:
        client_info = ClientInfo(
            ClientType.WEB.CLIENT_URL, ClientType.WEB.CLIENT_ID, identity.user_agent,
        )
        self._twitch._client_type = client_info
        self._twitch._ensure_api_clients()
        assert self._twitch._http_client is not None
        assert self._twitch._gql_client is not None
        self._twitch._http_client.enable_browser_mode(client_info)
        self._twitch._gql_client._client_type = client_info
        self.access_token = identity.token
        self.user_id = identity.user_id
        self.device_id = identity.device_id
        self.browser_active = True
        self._logged_in.set()
        self._twitch.gui.login.update(_.t["login"]["status"]["logged_in"], self.user_id)

    def accept_imported_identity(self, identity: BrowserIdentity) -> None:
        """Refresh every consumer when an already active imported account renews."""
        if not self.browser_active:
            return
        if getattr(self, "user_id", None) != identity.user_id:
            raise browser_error("ACCOUNT_MISMATCH")
        changed_token = getattr(self, "access_token", None) != identity.token
        if (
            changed_token or getattr(self, "device_id", None) != identity.device_id
            or identity.user_agent != self._twitch._client_type.USER_AGENT
            or self._twitch.gui.login.get_status().get("import_pending")
            or not self._logged_in.is_set()
        ):
            self._use_browser_identity(identity)
        if changed_token:
            for websocket in self._twitch.websocket.websockets:
                websocket.request_reconnect()


    def headers(self, *, user_agent: str = "", gql: bool = False) -> JsonType:
        """
        Build HTTP headers for Twitch API requests.

        Args:
            user_agent: Optional custom User-Agent string
            gql: If True, include GraphQL-specific headers

        Returns:
            Dictionary of HTTP headers
        """
        client_info: ClientInfo = self._twitch._client_type
        headers = {
            "Accept": "*/*",
            "Accept-Encoding": "gzip",
            "Accept-Language": "en-US",
            "Pragma": "no-cache",
            "Cache-Control": "no-cache",
            "Client-Id": client_info.CLIENT_ID,
        }
        if user_agent:
            headers["User-Agent"] = user_agent
        if hasattr(self, "session_id"):
            headers["Client-Session-Id"] = self.session_id
        # if hasattr(self, "client_version"):
        # headers["Client-Version"] = self.client_version
        if hasattr(self, "device_id"):
            headers["X-Device-Id"] = self.device_id
        if gql:
            headers["Origin"] = str(client_info.CLIENT_URL)
            headers["Referer"] = str(client_info.CLIENT_URL)
            headers["Authorization"] = f"OAuth {self.access_token}"
        return headers

    async def validate(self):
        """Thread-safe wrapper for _validate()."""
        async with self._lock:
            await self._validate()
        return self

    async def _validate(self):
        """Restore accepted helper state or legacy Android cookies; fresh login uses the helper."""
        if not hasattr(self, "session_id"):
            self.session_id = create_nonce(CHARS_HEX_LOWER, 16)
        browser = self._twitch._browser
        if browser.status()["generation"] > 0 or self.browser_active:
            if browser.status()["state"] != "ready":
                self._logged_in.clear()
            identity = await browser.authenticate(self._twitch.gui.login)
            if self.browser_active:
                self.accept_imported_identity(identity)
            else:
                self._use_browser_identity(identity)
            return
        if self._hasattrs("access_token", "user_id", "device_id"):
            self._logged_in.set()
            return
        session = await self._twitch.get_session()
        jar = cast(aiohttp.CookieJar, session.cookie_jar)
        client_info = ClientType.ANDROID_APP
        cookie = jar.filter_cookies(client_info.CLIENT_URL)
        if "auth-token" not in cookie:
            await self._browser_login()
            return
        token = cookie["auth-token"].value
        async with self._twitch.request(
            "GET", "https://id.twitch.tv/oauth2/validate",
            headers={"Authorization": f"OAuth {token}"},
        ) as response:
            identity_data = await response.json() if response.status == 200 else {}
        if identity_data.get("client_id") != client_info.CLIENT_ID:
            await self._browser_login()
            return
        try:
            user_id = int(identity_data["user_id"])
            if user_id <= 0:
                raise ValueError
        except (KeyError, ValueError, TypeError):
            await self._browser_login()
            return
        self.access_token, self.user_id = token, user_id
        self.device_id = cookie["unique_id"].value if "unique_id" in cookie else create_nonce(CHARS_HEX_LOWER, 32)
        cookie["unique_id"] = self.device_id
        cookie["persistent"] = str(user_id)
        jar.update_cookies(cookie, client_info.CLIENT_URL)
        jar.save(COOKIES_PATH)
        self._twitch.gui.login.update(_.t["login"]["status"]["logged_in"], user_id)
        self._logged_in.set()

    def invalidate(self):
        """Invalidate the current access token."""
        self._delattrs("access_token")
