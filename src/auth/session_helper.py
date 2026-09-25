"""Local browser session export and renewal helper (never a password collector)."""

from __future__ import annotations

import argparse
import asyncio
import base64
import json
import re
import tempfile
import time
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager, suppress
from pathlib import Path
from typing import Any

import aiohttp
from yarl import URL

from src.auth.browser_session import BrowserSession
from src.auth.server_seed import SDKCookie, ServerSeed
from src.auth.session_bundle import PrivateSessionFile, SessionBundle, SessionError
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
        assert cookie is not None
        return ServerSeed(bundle, cookie)

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
                endpoint = URL(target["webSocketDebuggerUrl"])
                if (
                    endpoint.scheme != "ws" or endpoint.host not in ("127.0.0.1", "localhost", "::1")
                    or endpoint.port != URL(self.address).port or endpoint.user is not None
                    or endpoint.password is not None or endpoint.query or endpoint.fragment
                ):
                    raise SessionError("BROWSER_PROTOCOL")
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
                                cookie = SDKCookie.from_browser(data.get("cookies"), now=self.clock())
                            return bundle, cookie
        except TimeoutError:
            raise SessionError("CAPTURE_TIMEOUT") from None


class SessionHelper:
    @staticmethod
    def check_export_paths(output: str, seed: str) -> None:
        """Reject aliases before capture without modifying existing export files."""
        try:
            first, second = Path(output).resolve(), Path(seed).resolve()
            if first == second or first.is_dir() or second.is_dir():
                raise ValueError
            if first.exists() and second.exists() and first.samefile(second):
                raise ValueError
            for path in (first, second):
                path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            if first.parent.samefile(second.parent):
                # Probe the destination filesystem's case/Unicode equivalence, including
                # names that do not exist yet. Exclusive creation cannot truncate data.
                with tempfile.TemporaryDirectory(prefix=".tdm-export-", dir=first.parent) as probe:
                    (Path(probe) / first.name).touch(exist_ok=False)
                    (Path(probe) / second.name).touch(exist_ok=False)
        except (OSError, ValueError, RuntimeError):
            raise SessionError("OUTPUT_PATH") from None

    @staticmethod
    async def run(args: argparse.Namespace) -> None:
        if args.command == "renew":
            from src.auth.session_renewal import RenewalConnection, RenewalLoop, RenewalSender

            connection = RenewalConnection.from_dict(PrivateSessionFile(Path(args.connection)).read())
            await RenewalLoop(BrowserExporter(args.browser), RenewalSender(connection),
                              renew_before=args.renew_before).run()
        else:
            seed_path = getattr(args, "server_seed", None)
            if seed_path:
                SessionHelper.check_export_paths(args.output, seed_path)
            exporter = BrowserExporter(args.browser)
            if seed_path:
                seed = await exporter.capture_seed()
                # Validate the combined envelope before creating either export.
                seed = ServerSeed.from_dict(seed.to_dict())
                bundle = seed.bundle
                PrivateSessionFile(Path(seed_path)).write(seed.to_dict())
            else:
                bundle = await exporter.capture()
            PrivateSessionFile(Path(args.output)).write(bundle.to_dict())
            print(json.dumps({"success": True, "expires_at": bundle.expires_at}))

    @staticmethod
    def main() -> None:
        parser = argparse.ArgumentParser(description="Export Twitch context from a dedicated local Chrome profile.")
        commands = parser.add_subparsers(dest="command", required=True)
        export = commands.add_parser("export")
        export.add_argument("--browser", required=True)
        export.add_argument("--output", required=True)
        export.add_argument("--server-seed", help="Also save a private SDK cookie seed for server renewal.")
        renew = commands.add_parser("renew")
        renew.add_argument("--browser", required=True)
        renew.add_argument("--connection", required=True)
        renew.add_argument("--renew-before", type=int, default=300,
                           help="Refresh this many seconds before expiry (30–3600; default 300).")
        args = parser.parse_args()
        try:
            asyncio.run(SessionHelper.run(args))
        except SessionError as error:
            parser.exit(1, f"SESSION_{error.code}\n")
        except KeyboardInterrupt:
            parser.exit(130)


if __name__ == "__main__":
    SessionHelper.main()
