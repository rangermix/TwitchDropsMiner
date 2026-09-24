"""Authentication state management for Twitch Drops Miner."""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, cast

import aiohttp
from yarl import URL

from src.auth.browser_session import BrowserIdentity, browser_error
from src.auth.imported_session import ImportedSession
from src.config import COOKIES_PATH, ClientInfo, ClientType
from src.exceptions import LoginException
from src.i18n import _
from src.utils import CHARS_HEX_LOWER, create_nonce


if TYPE_CHECKING:
    from src.config import ClientInfo, JsonType
    from src.core.client import Twitch
    from src.web.gui_manager import LoginForm


logger = logging.getLogger("TwitchDrops")


class _AuthState:
    """
    Manages authentication state including tokens, session, and login flow.

    This class handles:
    - OAuth device code flow for authentication
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

    async def _oauth_login(self) -> str:
        """
        Perform OAuth device code flow authentication.

        This implements the OAuth 2.0 Device Authorization Grant flow:
        1. Request device code and user code from Twitch
        2. Display code to user for entry at twitch.tv/activate
        3. Poll token endpoint until user completes authorization
        4. Return access token

        Returns:
            str: The access token
        """
        login_form: LoginForm = self._twitch.gui.login
        client_info: ClientInfo = self._twitch._client_type
        headers = {
            "Accept": "application/json",
            "Accept-Encoding": "gzip",
            "Accept-Language": "en-US",
            "Cache-Control": "no-cache",
            "Client-Id": client_info.CLIENT_ID,
            "Host": "id.twitch.tv",
            "Origin": str(client_info.CLIENT_URL),
            "Pragma": "no-cache",
            "Referer": str(client_info.CLIENT_URL),
            "User-Agent": client_info.USER_AGENT,
            "X-Device-Id": self.device_id,
        }
        payload = {
            "client_id": client_info.CLIENT_ID,
            "scopes": "",  # no scopes needed
        }
        while True:
            try:
                from datetime import datetime, timedelta, timezone

                from src.exceptions import RequestInvalid

                now = datetime.now(timezone.utc)
                async with self._twitch.request(
                    "POST", "https://id.twitch.tv/oauth2/device", headers=headers, data=payload
                ) as response:
                    if response.status != 200:
                        raise LoginException(
                            _.t["login"]["error_code"].format(
                                error_code=f"DEVICE_AUTH_{response.status}"
                            )
                        )
                    # {
                    #     "device_code": "40 chars [A-Za-z0-9]",
                    #     "expires_in": 1800,
                    #     "interval": 5,
                    #     "user_code": "8 chars [A-Z]",
                    #     "verification_uri": "https://www.twitch.tv/activate?device-code=ABCDEFGH"
                    # }
                    response_json: JsonType = await response.json()
                    device_code: str = response_json["device_code"]
                    user_code: str = response_json["user_code"]
                    interval: int = response_json["interval"]
                    verification_uri: URL = URL(response_json["verification_uri"])
                    expires_at = now + timedelta(seconds=response_json["expires_in"])

                # Print the code to the user, open them the activate page so they can type it in
                await login_form.ask_enter_code(verification_uri, user_code)

                payload = {
                    "client_id": self._twitch._client_type.CLIENT_ID,
                    "device_code": device_code,
                    "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
                }
                while True:
                    # sleep first, not like the user is gonna enter the code *that* fast
                    await asyncio.sleep(interval)
                    async with self._twitch.request(
                        "POST",
                        "https://id.twitch.tv/oauth2/token",
                        headers=headers,
                        data=payload,
                        invalidate_after=expires_at,
                    ) as response:
                        # 200 means success, 400 means the user haven't entered the code yet
                        if response.status != 200:
                            continue
                        response_json = await response.json()
                        # {
                        #     "access_token": "40 chars [A-Za-z0-9]",
                        #     "refresh_token": "40 chars [A-Za-z0-9]",
                        #     "scope": [...],
                        #     "token_type": "bearer"
                        # }
                        self.access_token = cast(str, response_json["access_token"])
                        return self.access_token
            except RequestInvalid:
                # the device_code has expired, request a new code
                continue

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
        """
        Validate and restore authentication state.

        This method:
        1. Generates session ID if needed
        2. Extracts device ID from Twitch cookies
        3. Validates existing access token or initiates login flow
        4. Ensures token client ID matches expected client
        5. Saves validated cookies to disk

        Raises:
            RuntimeError: On repeated validation failures
        """
        if not hasattr(self, "session_id"):
            self.session_id = create_nonce(CHARS_HEX_LOWER, 16)
        if self.browser_active and isinstance(self._twitch._browser, ImportedSession):
            if self._twitch._browser.status()["state"] != "ready":
                self._logged_in.clear()
            identity = await self._twitch._browser.authenticate(self._twitch.gui.login)
            self.accept_imported_identity(identity)
            return
        if self.browser_active and not self._hasattrs("access_token", "user_id"):
            await self._browser_login(expected_user_id=getattr(self, "user_id", None))
            return
        if not self._hasattrs("device_id", "access_token", "user_id"):
            session = await self._twitch.get_session()
            jar = cast(aiohttp.CookieJar, session.cookie_jar)
            client_info: ClientInfo = self._twitch._client_type
            if self._twitch._browser is not None and "auth-token" not in jar.filter_cookies(client_info.CLIENT_URL):
                await self._browser_login()
                return
        if not self._hasattrs("device_id"):
            async with self._twitch.request(
                "GET", client_info.CLIENT_URL, headers=self.headers()
            ) as response:
                page_html = await response.text("utf8")
                assert page_html is not None
            #     match = re.search(r'twilightBuildID="([-a-z0-9]+)"', page_html)
            # if match is None:
            #     raise MinerException("Unable to extract client_version")
            # self.client_version = match.group(1)
            # doing the request ends up setting the "unique_id" value in the cookie
            cookie = jar.filter_cookies(client_info.CLIENT_URL)
            self.device_id = cookie["unique_id"].value
        if not self._hasattrs("access_token", "user_id"):
            # looks like we're missing something
            login_form: LoginForm = self._twitch.gui.login
            logger.info("Checking login")
            login_form.update(_.t["login"]["status"]["logging_in"], None)
            for _client_mismatch_attempt in range(2):
                for _invalid_token_attempt in range(2):
                    cookie = jar.filter_cookies(client_info.CLIENT_URL)
                    if "auth-token" not in cookie:
                        self.access_token = await self._oauth_login()
                        cookie["auth-token"] = self.access_token
                    elif not hasattr(self, "access_token"):
                        logger.info("Restoring session from cookie")
                        self.access_token = cookie["auth-token"].value
                    # validate the auth token, by obtaining user_id
                    async with self._twitch.request(
                        "GET",
                        "https://id.twitch.tv/oauth2/validate",
                        headers={"Authorization": f"OAuth {self.access_token}"},
                    ) as response:
                        if response.status == 401:
                            if self._twitch._browser is not None:
                                await self._browser_login()
                                return
                            # the access token we have is invalid - clear the cookie and reauth
                            logger.info("Restored session is invalid")
                            assert client_info.CLIENT_URL.host is not None
                            jar.clear_domain(client_info.CLIENT_URL.host)
                            continue
                        elif response.status == 200:
                            validate_response = await response.json()
                            break
                else:
                    raise RuntimeError("Login verification failure (step #2)")
                # ensure the cookie's client ID matches the currently selected client
                if validate_response["client_id"] == client_info.CLIENT_ID:
                    break
                # A client switch cannot convert a token. Preserve the saved
                # credentials instead of destroying them before a new login fails.
                logger.info("Cookie client ID mismatch")
                self._delattrs("access_token", "user_id")
                if self._twitch._browser is not None:
                    await self._browser_login(expected_user_id=int(validate_response["user_id"]))
                    return
                raise LoginException(
                    _.t["login"]["error_code"].format(error_code="CLIENT_MISMATCH")
                )
            else:
                raise RuntimeError("Login verification failure (step #1)")
            self.user_id = int(validate_response["user_id"])
            cookie["persistent"] = str(self.user_id)
            logger.info(f"Login successful, user ID: {self.user_id}")
            login_form.update(_.t["login"]["status"]["logged_in"], self.user_id)
            # update our cookie and save it
            jar.update_cookies(cookie, client_info.CLIENT_URL)
            jar.save(COOKIES_PATH)
        self._logged_in.set()

    def invalidate(self):
        """Invalidate the current access token."""
        self._delattrs("access_token")
