"""Issue browser SDK proof on the server using a narrowly scoped private seed."""

from __future__ import annotations

import argparse
import asyncio
import base64
import json
import os
import re
import signal
import tempfile
import time
from collections.abc import AsyncIterator, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager, suppress
from pathlib import Path
from typing import Any, Protocol

from src.auth.browser_session import BrowserSession
from src.auth.imported_session import SessionTransport
from src.auth.server_seed import SDKCookie, ServerSeed
from src.auth.session_bundle import PrivateSessionFile, SessionBundle, SessionError
from src.auth.session_helper import BrowserExporter, DevToolsConnection
from src.auth.session_renewal import RenewalConnection, RenewalLoop, RenewalSender


class BrowserOwner(Protocol):
    def start(self) -> AbstractAsyncContextManager[str]: ...


class OwnedChromium:
    """Own a temporary profile and process group; never attach to a user's browser."""

    def __init__(self, executable: str, *, no_sandbox: bool = False, startup_timeout: float = 20):
        self.executable, self.no_sandbox = executable, no_sandbox
        self.startup_timeout = startup_timeout

    async def wait_ready(self, process: asyncio.subprocess.Process, profile: Path) -> str:
        async with asyncio.timeout(self.startup_timeout):  # type: ignore[attr-defined]
            while process.returncode is None:
                try:
                    lines = (profile / "DevToolsActivePort").read_text().splitlines()
                except FileNotFoundError:
                    await asyncio.sleep(.05)
                    continue
                if (len(lines) != 2 or not lines[0].isdecimal() or not 0 < int(lines[0]) < 65536
                        or not re.fullmatch(r"/devtools/browser/[a-zA-Z0-9-]+", lines[1])):
                    raise SessionError("BROWSER_START")
                return f"http://127.0.0.1:{int(lines[0])}"
        raise SessionError("BROWSER_START")

    @staticmethod
    async def stop(process: asyncio.subprocess.Process) -> None:
        def send(sig: signal.Signals) -> None:
            with suppress(ProcessLookupError):
                if os.name == "posix":
                    os.killpg(process.pid, sig)
                elif process.returncode is None:
                    process.terminate() if sig == signal.SIGTERM else process.kill()

        send(signal.SIGTERM)
        try:
            await asyncio.wait_for(process.wait(), 5)
        except TimeoutError:
            pass
        finally:
            # The leader can exit before descendants that ignore SIGTERM.
            send(signal.SIGKILL)
        if process.returncode is None:
            try:
                await asyncio.wait_for(process.wait(), 2)
            except TimeoutError:
                raise SessionError("BROWSER_STOP") from None

    @classmethod
    async def finish_stop(cls, process: asyncio.subprocess.Process) -> None:
        cleanup = asyncio.create_task(cls.stop(process))
        interrupted = False
        while not cleanup.done():
            try:
                await asyncio.shield(cleanup)
            except asyncio.CancelledError:
                interrupted = True
        cleanup.result()
        if interrupted:
            raise asyncio.CancelledError

    @asynccontextmanager
    async def start(self) -> AsyncIterator[str]:
        process = None
        with tempfile.TemporaryDirectory(prefix="tdm-renew-") as directory:
            profile = Path(directory)
            args = [
                self.executable, "--headless=new", f"--user-data-dir={profile}",
                "--remote-debugging-address=127.0.0.1", "--remote-debugging-port=0",
                "--no-first-run", "--no-default-browser-check", "--disable-dev-shm-usage",
                "--disable-blink-features=AutomationControlled", "about:blank",
            ]
            if self.no_sandbox:
                args.insert(1, "--no-sandbox")
            try:
                process = await asyncio.create_subprocess_exec(
                    *args, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL,
                    start_new_session=os.name == "posix",
                )
                try:
                    address = await self.wait_ready(process, profile)
                except (OSError, ValueError, TimeoutError):
                    raise SessionError("BROWSER_START") from None
                yield address
            except OSError:
                raise SessionError("BROWSER_START") from None
            finally:
                if process is not None:
                    await self.finish_stop(process)


class SDKExchange:
    """Correlate a POST issuance with its uncached network response."""

    URL = "https://gql.twitch.tv/integrity"

    def __init__(self):
        self.posts: set[str] = set()
        self.responses: dict[str, dict[str, Any]] = {}
        self.cached: set[str] = set()
        self.loaded = asyncio.Event()
        self.proof: asyncio.Future[Any] = asyncio.get_running_loop().create_future()

    async def run(self, protocol: DevToolsConnection) -> None:
        while True:
            event = await protocol.events.get()
            if event is None:
                raise SessionError("BROWSER_PROTOCOL")
            method, params = event["method"], event["params"]
            request_id = params.get("requestId")
            if len(self.posts) + len(self.responses) + len(self.cached) > 256:
                raise SessionError("CAPTURE_LIMIT")
            if method == "Fetch.requestPaused":
                request = params["request"]
                if (request.get("url") != BrowserSession.PAGE or request.get("method") != "GET"
                        or params.get("resourceType") != "Document"):
                    raise SessionError("SDK_PAGE")
                await protocol.command("Fetch.fulfillRequest", {
                    "requestId": request_id, "responseCode": 200,
                    "responseHeaders": [{"name": "Content-Type", "value": "text/html; charset=utf-8"}],
                    "body": base64.b64encode(b"<!doctype html><html><body></body></html>").decode(),
                })
            elif method == "Page.loadEventFired":
                self.loaded.set()
            elif method == "Network.requestWillBeSent":
                request = params["request"]
                if request.get("url") == self.URL and request.get("method") == "POST":
                    self.posts.add(request_id)
            elif method == "Network.requestServedFromCache":
                self.cached.add(request_id)
            elif method == "Network.responseReceived" and request_id in self.posts:
                self.responses[request_id] = params["response"]
            elif method == "Network.loadingFailed" and request_id in self.posts:
                raise SessionError("SDK_ISSUANCE")
            elif method == "Network.loadingFinished" and request_id in self.posts:
                response = self.responses.get(request_id, {})
                if (response.get("url") != self.URL or response.get("status") != 200
                        or response.get("fromDiskCache") or response.get("fromServiceWorker")
                        or request_id in self.cached):
                    raise SessionError("SDK_ISSUANCE")
                data = await protocol.body(request_id)
                if not self.proof.done():
                    self.proof.set_result(data)


class SDKAcquisition:
    """Shared network proof for server renewal and isolated native bootstrap."""

    EVENTS = frozenset({
        "Fetch.requestPaused", "Page.loadEventFired",
        "Network.requestServedFromCache", "Network.loadingFailed",
    })
    SDK_URL = (
        "https://k.twitchcdn.net/149e9513-01fa-4fb0-aad4-566afd725d1b/"
        "2d206a39-8ed7-437e-a3be-862e0f06eea3/p.js"
    )
    SCRIPT = """async function(headers, sdk) {
      return await new Promise(resolve => {
        const deadline = setTimeout(() => resolve({failure: 'sdk_timeout'}), 90000);
        const finish = value => { clearTimeout(deadline); resolve(value); };
        document.addEventListener('kpsdk-load', () => window.KPSDK.configure([
          {protocol: 'https:', method: 'POST', domain: 'gql.twitch.tv', path: '/integrity'}
        ]), {once: true});
        document.addEventListener('kpsdk-ready', async () => {
          try {
            const r = await window.fetch('https://gql.twitch.tv/integrity', {
              method: 'POST', headers, body: null, credentials: 'omit', mode: 'cors',
              signal: AbortSignal.timeout(30000)
            });
            finish({status: r.status, data: await r.json()});
          } catch (_) { finish({failure: 'issuance_fetch'}); }
        }, {once: true});
        const script = document.createElement('script');
        script.onerror = () => finish({failure: 'sdk_script'});
        script.src = sdk;
        document.body.appendChild(script);
      });
    }"""

    def __init__(self, *, clock: Callable[[], float] = time.time, timeout: float = 120):
        self.clock, self.timeout = clock, timeout

    async def run(self, protocol: DevToolsConnection, bundle: SessionBundle, cookie: SDKCookie | None = None) -> ServerSeed:
        try:
            async with asyncio.timeout(self.timeout):  # type: ignore[attr-defined]
                exchange = SDKExchange()
                events = asyncio.create_task(exchange.run(protocol))
                acquire = asyncio.create_task(self.acquire(protocol, exchange, bundle, cookie))
                try:
                    done, _ = await asyncio.wait({events, acquire}, return_when=asyncio.FIRST_COMPLETED)
                    if events in done:
                        await events
                        raise SessionError("BROWSER_PROTOCOL")
                    return await acquire
                finally:
                    for task in (events, acquire):
                        task.cancel()
                    await asyncio.gather(events, acquire, return_exceptions=True)
                    exchange.proof.cancel()
        except TimeoutError:
            raise SessionError("SDK_TIMEOUT") from None
        except (KeyError, TypeError, ValueError, AttributeError):
            raise SessionError("BROWSER_PROTOCOL") from None

    async def acquire(
        self, protocol: DevToolsConnection, exchange: SDKExchange,
        original: SessionBundle, previous_cookie: SDKCookie | None,
    ) -> ServerSeed:
        await protocol.command("Network.enable")
        await protocol.command("Network.setCacheDisabled", {"cacheDisabled": True})
        await protocol.command("Network.setBypassServiceWorker", {"bypass": True})
        if previous_cookie is not None:
            previous_cookie.require_fresh(self.clock())
            await protocol.command("Network.setCookies", {"cookies": [previous_cookie.to_browser_cookie()]})
        await protocol.command("Page.enable")
        await protocol.command("Fetch.enable", {"patterns": [{
            "urlPattern": BrowserSession.PAGE, "resourceType": "Document", "requestStage": "Request",
        }]})
        await protocol.command("Page.navigate", {"url": BrowserSession.PAGE})
        await exchange.loaded.wait()
        result = await protocol.command("Runtime.evaluate", {"expression": "navigator.userAgent", "returnByValue": True})
        user_agent = result["result"]["value"]
        result = await protocol.command("Runtime.evaluate", {"expression": "globalThis"})
        headers = {key: value for key, value in original.headers.items() if key != "client-integrity"}
        result = await protocol.command("Runtime.callFunctionOn", {
            "objectId": result["result"]["objectId"], "functionDeclaration": self.SCRIPT,
            "arguments": [{"value": headers}, {"value": self.SDK_URL}],
            "awaitPromise": True, "returnByValue": True,
        }, timeout=self.timeout)
        acquired = result.get("result", {}).get("value")
        if (result.get("exceptionDetails") or not isinstance(acquired, dict)
                or acquired.get("failure") or acquired.get("status") != 200):
            raise SessionError("SDK_ISSUANCE")
        data = acquired.get("data")
        observed = await exchange.proof
        if (not isinstance(data, dict) or not isinstance(observed, dict)
                or data.get("token") != observed.get("token")
                or data.get("expiration") != observed.get("expiration")
                or type(data.get("expiration")) not in (int, float)):
            raise SessionError("SDK_ISSUANCE")
        bundle = SessionBundle.from_dict({
            "version": 1, "captured_at": self.clock(), "expires_at": data["expiration"] / 1000,
            "user_agent": user_agent, "headers": {**headers, "client-integrity": data.get("token")},
        }, now=self.clock())
        minimum_expiry = self.clock() + 30
        if previous_cookie is not None:
            minimum_expiry = max(minimum_expiry, original.expires_at)
        if (bundle.headers["client-integrity"] == original.headers["client-integrity"]
                or bundle.expires_at <= minimum_expiry):
            raise SessionError("REPLAY")
        result = await protocol.command("Network.getCookies", {"urls": [SDKCookie.URL]})
        cookie = SDKCookie.from_browser(result.get("cookies"), now=self.clock())
        if cookie.expires_at <= max(previous_cookie.expires_at if previous_cookie else 0, bundle.expires_at):
            raise SessionError("SDK_COOKIE")
        return ServerSeed(bundle, cookie)


class SDKIssuer:
    """Renew only fresh SDK seeds in an owned temporary server browser."""

    def __init__(self, browser: BrowserOwner, *, clock: Callable[[], float] = time.time, timeout: float = 120):
        self.browser, self.clock, self.timeout = browser, clock, timeout

    async def issue(self, seed: ServerSeed) -> ServerSeed:
        seed.cookie.require_fresh(self.clock())
        async with (
            self.browser.start() as address,
            BrowserExporter(address).target(extra_events=SDKAcquisition.EVENTS) as protocol,
        ):
            return await SDKAcquisition(clock=self.clock, timeout=self.timeout).run(protocol, seed.bundle, seed.cookie)


class ServerContextSource:
    """Keep only independently validated state, ready for another server restart."""

    def __init__(
        self, path: Path, issuer: SDKIssuer, user_id: int, *,
        transport: SessionTransport | None = None, clock: Callable[[], float] = time.time,
    ):
        self.file, self.issuer, self.user_id = PrivateSessionFile(path), issuer, user_id
        self.transport, self.clock = transport or SessionTransport(), clock

    async def capture(self) -> SessionBundle:
        seed = ServerSeed.from_dict(self.file.read(), now=self.clock())
        renewed = await self.issuer.issue(seed)
        try:
            renewed.bundle.require_fresh(self.clock())
            await self.transport.validate(renewed.bundle, self.user_id)
            renewed.bundle.require_fresh(self.clock())
            self.file.write(renewed.to_dict())
            return renewed.bundle
        finally:
            await self.transport.close()


class ServerRenewalHelper:
    @staticmethod
    async def serve(args: argparse.Namespace) -> None:
        loop, task = asyncio.get_running_loop(), asyncio.current_task()
        assert task is not None
        if os.name == "posix":
            loop.add_signal_handler(signal.SIGTERM, task.cancel)
        try:
            await ServerRenewalHelper.run(args)
        finally:
            if os.name == "posix":
                loop.remove_signal_handler(signal.SIGTERM)

    @staticmethod
    async def run(args: argparse.Namespace) -> None:
        connection = RenewalConnection.from_dict(PrivateSessionFile(Path(args.connection)).read())
        # Fail invalid configuration before entering retries or starting Chromium.
        seed = ServerSeed.from_dict(PrivateSessionFile(Path(args.seed)).read())
        seed.cookie.require_fresh(time.time())
        source = ServerContextSource(
            Path(args.seed), SDKIssuer(OwnedChromium(args.chromium, no_sandbox=args.no_sandbox)), connection.user_id,
        )
        sender = RenewalSender(connection)
        if args.once:
            result = await sender.send(await source.capture())
            print(json.dumps({"event": "renewed", **result}), flush=True)
        else:
            await RenewalLoop(source, sender, renew_before=args.renew_before).run()

    @staticmethod
    def main() -> None:
        parser = argparse.ArgumentParser(description="Renew Twitch integrity on the server from a one-time private SDK seed.")
        parser.add_argument("--seed", required=True)
        parser.add_argument("--connection", required=True)
        parser.add_argument("--chromium", default="chromium")
        parser.add_argument("--no-sandbox", action="store_true", help="Required when running Chromium as root in the optional helper container.")
        parser.add_argument("--renew-before", type=int, default=300)
        parser.add_argument("--once", action="store_true", help="Validate and deliver one renewal, then exit.")
        try:
            asyncio.run(ServerRenewalHelper.serve(parser.parse_args()))
        except SessionError as error:
            parser.exit(1, f"SESSION_{error.code}\n")
        except KeyboardInterrupt:
            parser.exit(130)
        except asyncio.CancelledError:
            parser.exit(143)


if __name__ == "__main__":
    ServerRenewalHelper.main()
