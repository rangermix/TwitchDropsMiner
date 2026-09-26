"""In-memory capture of verified browser context for the direct login helper."""

from __future__ import annotations

import asyncio
import base64
import json
import re
import time
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager, suppress
from typing import Any

import aiohttp
from yarl import URL

from src.auth.browser_session import BrowserSession
from src.auth.server_seed import SDKCookie, ServerSeed
from src.auth.session_bundle import SessionBundle, SessionError
from src.config import ClientType


class DevToolsConnection:
    """Multiplex bounded protocol replies and the few relevant network events."""

    def __init__(self, socket: aiohttp.ClientWebSocketResponse, *, extra_events: frozenset[str] = frozenset()):
        self.socket = socket
        self.extra_events = extra_events
        self._sequence = 0
        self._pending: dict[int, asyncio.Future] = {}
        self.events: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue(maxsize=256)
        self._requests: set[str] = set()
        self._reader = asyncio.create_task(self._receive())

    async def _receive(self) -> None:
        try:
            async for message in self.socket:
                if message.type != aiohttp.WSMsgType.TEXT:
                    break
                data = message.json()
                if not isinstance(data, dict):
                    break
                sequence = data.get("id")
                future = self._pending.get(sequence) if isinstance(sequence, int) else None
                if future is not None and not future.done():
                    if "error" in data:
                        future.set_exception(SessionError("BROWSER_PROTOCOL"))
                    else:
                        future.set_result(data.get("result", {}))
                    continue
                method, params = data.get("method"), data.get("params", {})
                if not isinstance(params, dict):
                    break
                request_id = params.get("requestId")
                if method in self.extra_events:
                    if self.events.full():
                        break
                    self.events.put_nowait(data)
                    continue
                elif method == "Network.requestWillBeSent":
                    relevant = params.get("request", {}).get("url") in (BrowserSession.GQL_URL, "https://gql.twitch.tv/integrity")
                elif method == "Network.responseReceived":
                    relevant = params.get("type") != "Preflight" and params.get("response", {}).get("url") in (
                        BrowserSession.GQL_URL, "https://gql.twitch.tv/integrity",
                    )
                elif method == "Network.loadingFinished":
                    relevant = request_id in self._requests
                else:
                    continue
                if relevant:
                    if not isinstance(request_id, str):
                        break
                    if method == "Network.loadingFinished":
                        self._requests.discard(request_id)
                    else:
                        self._requests.add(request_id)
                    if len(self._requests) > 256 or self.events.full():
                        break
                    self.events.put_nowait(data)
        except (aiohttp.ClientError, ValueError, TypeError):
            pass
        finally:
            for future in self._pending.values():
                if not future.done():
                    future.set_exception(SessionError("BROWSER_PROTOCOL"))
            if self.events.full():
                self.events.get_nowait()
            self.events.put_nowait(None)

    async def command(self, method: str, params: dict[str, Any] | None = None, *, timeout: float = 30) -> Any:
        self._sequence += 1
        sequence = self._sequence
        future = asyncio.get_running_loop().create_future()
        self._pending[sequence] = future
        try:
            await self.socket.send_json({"id": sequence, "method": method, "params": params or {}})
            return await asyncio.wait_for(future, timeout)
        finally:
            self._pending.pop(sequence, None)

    async def body(self, request_id: str) -> Any:
        body = await self.command("Network.getResponseBody", {"requestId": request_id})
        raw = base64.b64decode(body["body"]) if body.get("base64Encoded") else body["body"]
        return json.loads(raw)

    async def close(self) -> None:
        self._reader.cancel()
        await asyncio.gather(self._reader, return_exceptions=True)


class CaptureObservation:
    """Accept only an issued token used by a successful authenticated request."""

    def __init__(self, clock: Callable[[], float]):
        self.clock = clock
        self.requests: dict[str, dict[str, str]] = {}
        self.responses: dict[str, dict[str, Any]] = {}
        self.finished: set[str] = set()
        self.issued: dict[str, tuple[float, float]] = {}
        self.campaigns: dict[str, bool] = {}

    async def observe(self, event: dict[str, Any], protocol: DevToolsConnection) -> None:
        method, params = event["method"], event["params"]
        request_id = params["requestId"]
        if len(self.responses) + len(self.requests) > 256:
            raise SessionError("CAPTURE_LIMIT")
        if method == "Network.requestWillBeSent":
            request = params["request"]
            headers = {key.lower(): value for key, value in request.get("headers", {}).items()}
            if (
                request["url"] == BrowserSession.GQL_URL
                and headers.get("authorization", "").startswith("OAuth ")
                and headers.get("client-id") == ClientType.WEB.CLIENT_ID
                and headers.get("client-integrity")
            ):
                self.requests[request_id] = {
                    key: value for key, value in headers.items() if key in SessionBundle.HEADER_NAMES
                }
        elif method == "Network.responseReceived":
            self.responses[request_id] = params["response"]
        elif method == "Network.loadingFinished":
            self.finished.add(request_id)
            response = self.responses.get(request_id, {})
            if response.get("status") != 200:
                return
            if response.get("url") == "https://gql.twitch.tv/integrity":
                data = await protocol.body(request_id)
                if isinstance(data, dict) and isinstance(data.get("token"), str) and type(data.get("expiration")) in (int, float):
                    self.issued[data["token"]] = (self.clock(), data["expiration"] / 1000)
            elif request_id in self.requests:
                rows = await protocol.body(request_id)
                if isinstance(rows, dict):
                    rows = [rows]
                if isinstance(rows, list):
                    for row in rows:
                        if not isinstance(row, dict) or row.get("errors"):
                            continue
                        user = (row.get("data") or {}).get("currentUser") or {}
                        if isinstance(user.get("dropCampaigns"), list):
                            self.campaigns[request_id] = True

    def bundle(self, user_agent: str) -> SessionBundle | None:
        for request_id, headers in self.requests.items():
            issued = self.issued.get(headers["client-integrity"])
            if not issued or not self.campaigns.get(request_id):
                continue
            captured, expiry = issued
            if expiry <= self.clock() + 30:
                continue
            return SessionBundle.from_dict({
                "version": 1, "headers": headers, "captured_at": captured,
                "expires_at": expiry, "user_agent": user_agent,
            }, now=self.clock())
        return None


class BrowserExporter:
    def __init__(
        self, address: str, *, clock: Callable[[], float] = time.time, timeout: float = 120,
    ):
        try:
            url = URL(address)
            if (
                url.scheme != "http" or url.host not in ("127.0.0.1", "localhost", "::1")
                or not url.explicit_port or url.user is not None or url.password is not None
                or url.raw_path != "/" or url.query or url.fragment
            ):
                raise ValueError
        except (ValueError, TypeError):
            raise SessionError("BROWSER_ADDRESS") from None
        self.address, self.clock, self.timeout = str(url).rstrip("/"), clock, timeout

    async def capture(self) -> SessionBundle:
        bundle, _cookie = await self._capture(include_sdk_cookie=False)
        return bundle

    async def capture_seed(self) -> ServerSeed:
        bundle, cookie = await self._capture(include_sdk_cookie=True)
        if cookie is not None:
            bundle.require_fresh(self.clock())
            cookie.require_fresh(self.clock())
            return ServerSeed(bundle, cookie)
        # A signed-in profile can still issue proof after its SDK cookie expires.
        # Bootstrap in empty, owned storage instead of modifying that profile.
        from src.auth.imported_session import SessionTransport
        from src.auth.server_renewal import SDKAcquisition

        transport = SessionTransport()
        try:
            identity = await transport.validate(bundle, None)
            async with self.isolated_target(extra_events=SDKAcquisition.EVENTS) as protocol:
                seed = await SDKAcquisition(clock=self.clock, timeout=self.timeout).run(protocol, bundle)
            seed.bundle.require_fresh(self.clock())
            seed.cookie.require_fresh(self.clock())
            await transport.validate(seed.bundle, identity.user_id)
            seed.bundle.require_fresh(self.clock())
            seed.cookie.require_fresh(self.clock())
            return seed
        finally:
            await transport.close()

    def endpoint(self, value: str, *, target_id: str | None = None) -> URL:
        endpoint = URL(value)
        if (
            endpoint.scheme != "ws" or endpoint.host not in ("127.0.0.1", "localhost", "::1")
            or endpoint.port != URL(self.address).port or endpoint.user is not None
            or endpoint.password is not None or endpoint.query or endpoint.fragment
            or (endpoint.raw_path != f"/devtools/page/{target_id}" if target_id is not None
                else not re.fullmatch(r"/devtools/browser/[a-zA-Z0-9-]+", endpoint.raw_path))
        ):
            raise SessionError("BROWSER_PROTOCOL")
        return endpoint

    @asynccontextmanager
    async def isolated_target(self, *, extra_events: frozenset[str] = frozenset()) -> AsyncIterator[DevToolsConnection]:
        """Dispose only the new context, including on browser-CDP disconnect."""
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=30)) as http:
            try:
                async with http.get(self.address + "/json/version", allow_redirects=False) as response:
                    if response.status != 200:
                        raise SessionError("BROWSER_PROTOCOL")
                    endpoint = self.endpoint((await response.json())["webSocketDebuggerUrl"])
                async with http.ws_connect(endpoint, timeout=aiohttp.ClientWSTimeout(ws_close=2)) as socket:
                    controller = DevToolsConnection(socket)
                    context_id = None
                    try:
                        result = await controller.command("Target.createBrowserContext", {"disposeOnDetach": True})
                        context_id = result["browserContextId"]
                        if not isinstance(context_id, str) or not re.fullmatch(r"[a-zA-Z0-9-]+", context_id):
                            raise SessionError("BROWSER_PROTOCOL")
                        result = await controller.command("Target.createTarget", {"url": "about:blank", "browserContextId": context_id})
                        target_id = result["targetId"]
                        if not isinstance(target_id, str) or not re.fullmatch(r"[a-zA-Z0-9-]+", target_id):
                            raise SessionError("BROWSER_PROTOCOL")
                        async with http.get(self.address + "/json/list", allow_redirects=False) as response:
                            if response.status != 200:
                                raise SessionError("BROWSER_PROTOCOL")
                            pages = await response.json()
                        if not isinstance(pages, list):
                            raise SessionError("BROWSER_PROTOCOL")
                        targets = [page for page in pages if isinstance(page, dict) and page.get("id") == target_id]
                        if len(targets) != 1:
                            raise SessionError("BROWSER_PROTOCOL")
                        endpoint = self.endpoint(targets[0]["webSocketDebuggerUrl"], target_id=target_id)
                        async with http.ws_connect(
                            endpoint, max_msg_size=16 * 1024 * 1024,
                            timeout=aiohttp.ClientWSTimeout(ws_close=2),
                        ) as page_socket:
                            protocol = DevToolsConnection(page_socket, extra_events=extra_events)
                            try:
                                yield protocol
                            finally:
                                await protocol.close()
                    finally:
                        try:
                            if context_id is not None:
                                await controller.command("Target.disposeBrowserContext", {"browserContextId": context_id}, timeout=2)
                        finally:
                            await controller.close()
            except (aiohttp.ClientError, ValueError, KeyError, TypeError, TimeoutError):
                raise SessionError("BROWSER_PROTOCOL") from None

    @asynccontextmanager
    async def target(self, *, extra_events: frozenset[str] = frozenset()) -> AsyncIterator[DevToolsConnection]:
        """Create, connect and close only the target owned by this operation."""
        target_id = None
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=30)) as http:
            try:
                async with http.put(self.address + "/json/new?about:blank", allow_redirects=False) as response:
                    if response.status != 200:
                        raise SessionError("BROWSER_PROTOCOL")
                    target = await response.json()
                if not re.fullmatch(r"[a-zA-Z0-9-]+", target["id"]):
                    raise SessionError("BROWSER_PROTOCOL")
                target_id = target["id"]
                endpoint = self.endpoint(target["webSocketDebuggerUrl"], target_id=target_id)
                async with http.ws_connect(
                    endpoint, max_msg_size=16 * 1024 * 1024,
                    timeout=aiohttp.ClientWSTimeout(ws_close=2),
                ) as socket:
                    protocol = DevToolsConnection(socket, extra_events=extra_events)
                    try:
                        yield protocol
                    finally:
                        await protocol.close()
            except (aiohttp.ClientError, ValueError, KeyError, TypeError):
                raise SessionError("BROWSER_PROTOCOL") from None
            finally:
                if target_id is not None:
                    with suppress(aiohttp.ClientError, TimeoutError):
                        async with http.get(
                            self.address + "/json/close/" + target_id, allow_redirects=False,
                            timeout=aiohttp.ClientTimeout(total=2),
                        ) as response:
                            await response.read()

    async def _capture(self, *, include_sdk_cookie: bool) -> tuple[SessionBundle, SDKCookie | None]:
        try:
            async with self.target() as protocol:
                # Runtime requires Python 3.12; Mypy still targets legacy 3.10.
                async with asyncio.timeout(self.timeout):  # type: ignore[attr-defined]
                    await protocol.command("Network.enable")
                    result = await protocol.command("Runtime.evaluate", {"expression": "navigator.userAgent", "returnByValue": True})
                    user_agent = result["result"]["value"]
                    await protocol.command("Page.navigate", {"url": BrowserSession.PAGE})
                    observation = CaptureObservation(self.clock)
                    while True:
                        event = await protocol.events.get()
                        if event is None:
                            raise SessionError("BROWSER_PROTOCOL")
                        await observation.observe(event, protocol)
                        bundle = observation.bundle(user_agent)
                        if bundle is not None:
                            cookie = None
                            if include_sdk_cookie:
                                data = await protocol.command("Network.getCookies", {"urls": [SDKCookie.URL]})
                                if not isinstance(data, dict):
                                    raise SessionError("BROWSER_PROTOCOL")
                                if data.get("cookies") != []:
                                    try:
                                        cookie = SDKCookie.from_browser(data.get("cookies"), now=self.clock())
                                    except SessionError as error:
                                        if error.code != "SDK_EXPIRED":
                                            raise
                            return bundle, cookie
        except TimeoutError:
            raise SessionError("CAPTURE_TIMEOUT") from None


if __name__ == "__main__":
    from src.auth.login_helper import LoginHelperCLI

    LoginHelperCLI.main()
