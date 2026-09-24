"""Persistent, interactive Chromium session owned by TDM via WebDriver.

Only this service handles browser credentials. The browser keeps its profile and
executes Twitch requests itself; web credentials never enter the Android cookie jar.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

import aiohttp
from yarl import URL

from src.config import GQL_OPERATIONS, ClientType
from src.exceptions import ExitRequest, LoginException
from src.i18n import _


if TYPE_CHECKING:
    from src.web.managers.login import LoginFormManager


def browser_error(code: str) -> LoginException:
    """Report a stable code, never a driver response containing credentials."""
    return LoginException(_.t["login"]["error_code"].format(error_code=f"BROWSER_{code}"))


@dataclass(frozen=True)
class BrowserConfig:
    endpoint: str
    viewer_url: str
    debugger_address: str | None = None

    @classmethod
    def from_env(cls) -> BrowserConfig | None:
        endpoint = os.environ.get("TDM_BROWSER_URL", "").strip().rstrip("/")
        viewer = os.environ.get("TDM_BROWSER_VIEWER_URL", "").strip()
        debugger = os.environ.get("TDM_BROWSER_DEBUGGER_ADDRESS", "").strip()
        if not endpoint and not viewer and not debugger:
            return None
        try:
            if not viewer and not debugger:
                raise ValueError
            if debugger:
                address = URL(f"http://{debugger}")
                if (
                    address.host not in ("127.0.0.1", "localhost", "::1")
                    or not address.explicit_port
                    or address.user is not None
                    or address.password is not None
                    or address.raw_path != "/"
                    or address.query
                    or address.fragment
                ):
                    raise ValueError
                host = "[::1]" if address.host == "::1" else address.host
                debugger = f"{host}:{address.port}"
            for raw in (endpoint, *([viewer] if viewer else [])):
                url = URL(raw)
                if (
                    url.scheme not in ("http", "https")
                    or not url.host
                    or url.user is not None
                    or url.password is not None
                    or url.fragment
                ):
                    raise ValueError
            # Driver URLs must never carry credentials or query parameters.
            if URL(endpoint).query or URL(viewer).query:
                raise ValueError
        except (ValueError, TypeError):
            raise browser_error("CONFIG") from None
        return cls(endpoint, viewer, debugger or None)


@dataclass(frozen=True)
class BrowserIdentity:
    user_id: int
    token: str = field(repr=False)
    device_id: str = field(repr=False)
    user_agent: str


class BrowserSession:
    PAGE = "https://www.twitch.tv/drops/campaigns"
    GQL_URL = "https://gql.twitch.tv/gql"
    VALIDATE_URL = "https://id.twitch.tv/oauth2/validate"
    _HEADER_NAMES = frozenset(
        {
            "authorization",
            "client-id",
            "client-integrity",
            "client-version",
            "client-session-id",
            "x-device-id",
            "device-id",
            "accept-language",
        }
    )
    # Arguments are passed through WebDriver JSON, never interpolated into code.
    _FETCH_SCRIPT = """const [url, headers, body, done] = arguments;
if (location.origin !== 'https://www.twitch.tv') {
    done({status: 0}); return;
}
const controller = new AbortController();
const timer = setTimeout(() => controller.abort(), 20000);
fetch(url, {method: body === null ? 'GET' : 'POST', headers,
    body: body === null ? undefined : JSON.stringify(body),
    credentials: 'omit', signal: controller.signal, redirect: 'error'})
    .then(async response => done({status: response.status, data: await response.json()}))
    .catch(() => done({status: 0}))
    .finally(() => clearTimeout(timer));
"""

    def __init__(self, config: BrowserConfig, state_path: Path):
        self.config = config
        self.state_path = state_path
        self._session_id: str | None = None
        self._http: aiohttp.ClientSession | None = None
        self._lock = asyncio.Lock()
        self._headers: dict[str, str] = {}
        self._token = ""
        self._login_task: asyncio.Task[BrowserIdentity] | None = None
        self._stopping = False

    async def _command(self, method: str, path: str, payload: Any = None) -> Any:
        if self._http is None or self._http.closed:
            self._http = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=65))
        try:
            async with self._http.request(
                method,
                self.config.endpoint + path,
                json=payload,
                allow_redirects=False,
            ) as response:
                data = await response.json()
                if response.status >= 300 or not isinstance(data, dict) or "value" not in data:
                    raise browser_error("DRIVER")
                value = data["value"]
                if isinstance(value, dict) and value.get("error"):
                    raise browser_error("DRIVER")
                return value
        except (aiohttp.ClientError, TimeoutError, ValueError):
            raise browser_error("DRIVER") from None

    def _path(self, suffix: str) -> str:
        if self._session_id is None:
            raise browser_error("DRIVER")
        return f"/session/{self._session_id}{suffix}"

    async def start(self) -> None:
        """Reconnect after a miner crash, or create a browser on its saved profile."""
        if self._session_id is not None:
            return
        try:
            state = json.loads(self.state_path.read_text())
            sid = state["session_id"]
            if (
                state["endpoint"] == self.config.endpoint
                and state.get("debugger_address") == self.config.debugger_address
                and re.fullmatch(r"[a-zA-Z0-9-]+", sid)
            ):
                self._session_id = sid
                try:
                    await self._command("GET", self._path("/url"))
                    return
                except LoginException:
                    self._session_id = None
        except (OSError, ValueError, KeyError, TypeError):
            pass
        creation = asyncio.create_task(
            self._command(
                "POST",
                "/session",
                {
                    "capabilities": {
                        "alwaysMatch": {
                            "browserName": "chrome",
                            "goog:chromeOptions": {"debuggerAddress": self.config.debugger_address}
                            if self.config.debugger_address
                            else {
                                "args": [
                                    "--user-data-dir=/home/seluser/tdm-profile",
                                    "--window-size=1280,900",
                                ]
                            },
                            "goog:loggingPrefs": {"performance": "ALL"},
                        }
                    }
                },
            )
        )
        try:
            value = await asyncio.shield(creation)
        except asyncio.CancelledError:
            # Allocation may precede the response. Wait for the bounded driver
            # request (65s maximum) so the caller can delete the allocated session.
            try:
                value = await creation
                self._remember_session(value)
            finally:
                raise
        self._remember_session(value)
        await self._command("POST", self._path("/timeouts"), {"script": 30000, "pageLoad": 60000})
        await self._command("POST", self._path("/url"), {"url": self.PAGE})

    def _remember_session(self, value: Any) -> None:
        sid = value.get("sessionId") if isinstance(value, dict) else None
        if not isinstance(sid, str) or not re.fullmatch(r"[a-zA-Z0-9-]+", sid):
            raise browser_error("DRIVER")
        self._session_id = sid
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        # No token is written here. The private browser volume owns credentials.
        with self.state_path.open("w") as handle:
            os.chmod(self.state_path, 0o600)
            json.dump(
                {
                    "session_id": sid,
                    "endpoint": self.config.endpoint,
                    "debugger_address": self.config.debugger_address,
                },
                handle,
            )

    async def _cookies(self) -> dict[str, str]:
        # The user can navigate the interactive browser elsewhere. Do not accept
        # similarly named cookies from a different origin.
        current = URL(await self._command("GET", self._path("/url")))
        if current.scheme != "https" or current.host != "www.twitch.tv" or current.port != 443:
            return {}
        cookies = await self._command("GET", self._path("/cookie"))
        return {
            c["name"]: c["value"]
            for c in cookies
            if c.get("domain", "").lstrip(".")
            in (
                "twitch.tv",
                "www.twitch.tv",
            )
            and c.get("name") in ("auth-token", "unique_id")
        }

    async def _refresh_headers(self, token: str) -> None:
        logs = await self._command("POST", self._path("/log"), {"type": "performance"})
        for entry in logs:
            try:
                message = json.loads(entry["message"])["message"]
                request = message["params"]["request"]
                if (
                    message["method"] != "Network.requestWillBeSent"
                    or request["url"] != self.GQL_URL
                ):
                    continue
                headers = {key.lower(): value for key, value in request["headers"].items()}
                if headers.get("authorization") != f"OAuth {token}":
                    continue
                if headers.get("client-id") != ClientType.WEB.CLIENT_ID:
                    continue
                integrity = headers.get("client-integrity")
                if not isinstance(integrity, str) or not integrity.strip():
                    # Twitch sends early authenticated requests without integrity,
                    # then retries protected operations with its complete context.
                    continue
                # Replace as a unit: never combine credentials from different requests.
                self._headers = {
                    key: value for key, value in headers.items() if key in self._HEADER_NAMES
                }
            except (KeyError, ValueError, TypeError):
                continue

    async def _fetch(self, url: str, headers: dict[str, str], body: Any = None) -> Any:
        if url not in (self.GQL_URL, self.VALIDATE_URL):
            raise browser_error("REQUEST")
        result = await self._command(
            "POST",
            self._path("/execute/async"),
            {
                "script": self._FETCH_SCRIPT,
                "args": [url, headers, body],
            },
        )
        if not isinstance(result, dict) or result.get("status") != 200:
            raise browser_error("REQUEST")
        return result.get("data")

    async def gql(self, operations: Any) -> Any:
        """Execute authenticated operations in the same actual browser context."""
        async with self._lock:
            cookies = await self._cookies()
            if not self._token or cookies.get("auth-token") != self._token:
                raise browser_error("SESSION_CHANGED")
            await self._refresh_headers(self._token)
            if self._headers.get("authorization") != f"OAuth {self._token}":
                raise browser_error("CONTEXT")
            return await self._fetch(
                self.GQL_URL, {"content-type": "application/json", **self._headers}, operations
            )

    async def authenticate(self, login: LoginFormManager) -> BrowserIdentity:
        if self._stopping:
            raise ExitRequest()
        self._login_task = asyncio.create_task(self._authenticate(login))
        try:
            return await self._login_task
        except asyncio.CancelledError:
            if self._stopping:
                raise ExitRequest() from None
            raise
        finally:
            self._login_task = None

    def request_stop(self) -> None:
        """Interrupt pending interactive login on dashboard Close or SIGTERM."""
        if self._stopping:
            return
        self._stopping = True
        if self._login_task is not None:
            self._login_task.cancel()

    async def _authenticate(self, login: LoginFormManager) -> BrowserIdentity:
        """Wait for user login, then prove identity and both inventory endpoints."""
        try:
            await self.start()
            if self.config.debugger_address and not self.config.viewer_url:
                await login.browser_pending(None, desktop=True)
            else:
                await login.browser_pending(self.config.viewer_url)
            token = await asyncio.wait_for(self._wait_for_token(), 15 * 60)
            identity = await self._fetch(self.VALIDATE_URL, {"Authorization": f"OAuth {token}"})
            if (
                not isinstance(identity, dict)
                or identity.get("client_id") != ClientType.WEB.CLIENT_ID
            ):
                raise browser_error("IDENTITY")
            try:
                user_id = int(identity["user_id"])
                device_id = self._headers.get("x-device-id") or self._headers["device-id"]
                if user_id <= 0 or not isinstance(device_id, str) or not device_id:
                    raise ValueError
            except (KeyError, TypeError, ValueError):
                raise browser_error("IDENTITY") from None
            self._token = token
            results = await self.gql([GQL_OPERATIONS["Inventory"], GQL_OPERATIONS["Campaigns"]])
            try:
                inventory = results[0]["data"]["currentUser"]["inventory"]
                campaigns = results[1]["data"]["currentUser"]["dropCampaigns"]
                if (
                    any(item.get("errors") for item in results)
                    or not isinstance(inventory, dict)
                    or not isinstance(inventory.get("gameEventDrops"), list)
                    or "dropCampaignsInProgress" not in inventory
                    or not isinstance(inventory.get("dropCampaignsInProgress"), (list, type(None)))
                    or not isinstance(campaigns, list)
                ):
                    raise ValueError
            except (IndexError, KeyError, TypeError, ValueError):
                raise browser_error("CATALOG") from None
            user_agent = await self._command(
                "POST",
                self._path("/execute/sync"),
                {
                    "script": "return navigator.userAgent",
                    "args": [],
                },
            )
            return BrowserIdentity(user_id, token, device_id, user_agent)
        except TimeoutError:
            await self.close()
            raise browser_error("LOGIN_TIMEOUT") from None
        except BaseException:
            await self.close()
            raise
        finally:
            await login.browser_pending(None)

    async def _wait_for_token(self) -> str:
        previous_token = ""
        while True:
            cookies = await self._cookies()
            token = cookies.get("auth-token", "")
            if token:
                await self._refresh_headers(token)
                if self._headers.get("authorization") == f"OAuth {token}":
                    return token
                if token != previous_token:
                    previous_token = token
                    await self._command("POST", self._path("/url"), {"url": self.PAGE})
            await asyncio.sleep(2)

    async def close(self) -> None:
        """Close our browser session; retain its profile for the next process."""
        try:
            if self._session_id is not None:
                await asyncio.wait_for(self._command("DELETE", self._path("")), 5)
                self.state_path.unlink(missing_ok=True)
        except (LoginException, TimeoutError, OSError):
            # Retain the session ID for reconnection if the driver was unavailable.
            pass
        finally:
            self._session_id = None
            self._token = ""
            self._headers.clear()
            if self._http is not None:
                await self._http.close()
                self._http = None
