"""Server SDK issuance against isolated protocol peers; never contacts Twitch."""

import asyncio
import base64
import json
import os
import signal
import sys
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from aiohttp import web

from src.auth.browser_session import BrowserSession
from src.auth.server_seed import ServerSeed
from src.auth.session_bundle import PrivateSessionFile, SessionError
from tests.test_server_seed import seed_data


@pytest.mark.asyncio
async def test_initial_server_proof_accepts_fresh_shorter_expiry_but_renewal_does_not():
    from src.auth.server_renewal import SDKIssuer

    data = seed_data()
    data["bundle"]["expires_at"] = 8200
    original = ServerSeed.from_dict(data, now=1000)
    async with sdk_peer() as (browser, commands, closed, state):
        issuer = SDKIssuer(browser, clock=lambda: 1000)
        with pytest.raises(SessionError, match="REPLAY"):
            await issuer.issue(original)
        replacement = await issuer.issue(original, initial=True)
        assert replacement.bundle.expires_at == 5600
        assert replacement.bundle.headers["client-integrity"] != original.bundle.headers["client-integrity"]
        assert not state["running"]


@asynccontextmanager
async def sdk_peer(*, fault=None, sdk_started=None):
    commands, closed, address = [], [], []
    token = "private-new-integrity"
    issued = {"token": token, "expiration": 5600000}

    async def create(request):
        assert request.query_string == "about:blank"
        return web.json_response({"id": "owned", "webSocketDebuggerUrl": address[0].replace("http:", "ws:") + "/devtools/page/owned"})

    async def close(request):
        closed.append(request.match_info["target"])
        return web.Response()

    async def socket(request):
        ws = web.WebSocketResponse()
        await ws.prepare(request)

        async def event(method, params):
            await ws.send_json({"method": method, "params": params})

        async for message in ws:
            command = message.json()
            method, params = command["method"], command.get("params", {})
            commands.append((method, params))
            result = {}
            if method == "Runtime.evaluate":
                result = {"result": {"value": "Server Chromium"}} if params["expression"] == "navigator.userAgent" else {"result": {"objectId": "window"}}
            elif method == "Network.setCookies":
                assert len(params["cookies"]) == 1
                cookie = params["cookies"][0]
                assert cookie["name"] == "KP_UIDz-ssn"
                assert cookie["domain"] == "k.twitchcdn.net"
            elif method == "Fetch.fulfillRequest":
                assert params["requestId"] == "document"
                assert base64.b64decode(params["body"]).startswith(b"<!doctype html>")
            elif method == "Runtime.callFunctionOn":
                if sdk_started is not None:
                    sdk_started.set()
                if fault == "pending":
                    continue
                assert "private-sdk-cookie" not in params["functionDeclaration"]
                headers = params["arguments"][0]["value"]
                assert "client-integrity" not in headers
                assert headers["authorization"] == "OAuth test-token"
                if fault == "sdk_timeout":
                    result = {"result": {"value": {"failure": "sdk_timeout"}}}
                else:
                    body = {**issued}
                    if fault == "unchanged":
                        body["token"] = seed_data()["bundle"]["headers"]["client-integrity"]
                    if fault == "old_expiry":
                        body["expiration"] = seed_data()["bundle"]["expires_at"] * 1000
                    if fault == "mismatch":
                        body["token"] = "unobserved-private-token"
                    result = {"result": {"value": {"status": 200, "data": body}}}
                    for request_id, http_method in [("preflight", "OPTIONS"), ("proof", "POST")]:
                        await event("Network.requestWillBeSent", {"requestId": request_id, "request": {"url": "https://gql.twitch.tv/integrity", "method": http_method, "headers": {}}})
                        if fault == "cache_event" and http_method == "POST":
                            await event("Network.requestServedFromCache", {"requestId": request_id})
                        await event("Network.responseReceived", {"requestId": request_id, "type": "Preflight" if http_method == "OPTIONS" else "Fetch", "response": {
                            "url": "https://gql.twitch.tv/integrity", "status": 403 if fault == "http_error" else 200,
                            "fromDiskCache": fault == "cache", "fromServiceWorker": fault == "worker",
                        }})
                        await event("Network.loadingFinished", {"requestId": request_id})
            elif method == "Network.getResponseBody":
                assert params["requestId"] == "proof", "preflight cannot prove issuance"
                body = {**issued}
                if fault == "unchanged":
                    body["token"] = seed_data()["bundle"]["headers"]["client-integrity"]
                if fault == "old_expiry":
                    body["expiration"] = seed_data()["bundle"]["expires_at"] * 1000
                result = {"body": json.dumps(body), "base64Encoded": False}
            elif method == "Network.getCookies":
                assert params == {"urls": ["https://k.twitchcdn.net/"]}
                result = {"cookies": [] if fault == "missing_cookie" else [{
                    "name": "KP_UIDz-ssn", "value": "server-sdk-cookie", "expires": 999 if fault == "expired_cookie" else 87400,
                    "domain": "k.twitchcdn.net", "path": "/", "secure": True, "httpOnly": True,
                }]}
            await ws.send_json({"id": command["id"], "result": result})
            if method == "Page.navigate":
                assert params == {"url": BrowserSession.PAGE}
                await event("Fetch.requestPaused", {"requestId": "document", "resourceType": "Document", "request": {"url": BrowserSession.PAGE, "method": "GET"}})
            if method == "Fetch.fulfillRequest":
                await event("Page.loadEventFired", {"timestamp": 1})
        return ws

    app = web.Application()
    app.router.add_put("/json/new", create)
    app.router.add_get("/json/close/{target}", close)
    app.router.add_get("/devtools/page/owned", socket)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    address.append(f"http://127.0.0.1:{site._server.sockets[0].getsockname()[1]}")
    state = {"running": False, "exits": 0}

    class Browser:
        @asynccontextmanager
        async def start(self):
            state["running"] = True
            try:
                yield address[0]
            finally:
                state["running"] = False
                state["exits"] += 1

    try:
        yield Browser(), commands, closed, state
    finally:
        await runner.cleanup()


@pytest.mark.asyncio
async def test_sdk_issuer_correlates_network_and_keeps_only_renewed_seed():
    from src.auth.server_renewal import SDKIssuer

    seed = ServerSeed.from_dict(seed_data(), now=1000)
    async with sdk_peer() as (browser, commands, closed, state):
        renewed = await SDKIssuer(browser, clock=lambda: 1000).issue(seed)
        assert renewed.bundle.headers["client-integrity"] == "private-new-integrity"
        assert renewed.bundle.expires_at == 5600
        assert renewed.bundle.user_agent == "Server Chromium"
        assert renewed.cookie.value == "server-sdk-cookie"
        assert renewed.cookie.expires_at > seed.cookie.expires_at
        assert state == {"running": False, "exits": 1}
        assert closed == ["owned"]
        assert "Browser.close" not in [method for method, _ in commands]
        assert "private" not in repr(renewed)


@pytest.mark.asyncio
@pytest.mark.parametrize("fault", ["cache", "cache_event", "worker", "mismatch", "unchanged", "old_expiry", "http_error", "sdk_timeout", "missing_cookie"])
async def test_sdk_issuer_rejects_false_proof_and_cleans_up(fault):
    from src.auth.server_renewal import SDKIssuer

    seed = ServerSeed.from_dict(seed_data(), now=1000)
    async with sdk_peer(fault=fault) as (browser, _commands, closed, state):
        with pytest.raises(SessionError) as error:
            await SDKIssuer(browser, clock=lambda: 1000, timeout=2).issue(seed)
        assert "private" not in str(error.value)
        assert state == {"running": False, "exits": 1}
        assert closed == ["owned"]


@pytest.mark.asyncio
async def test_expired_sdk_seed_never_launches_browser():
    from src.auth.server_renewal import SDKIssuer

    browser = SimpleNamespace(start=AsyncMock(side_effect=AssertionError("browser must not launch")))
    with pytest.raises(SessionError, match="SDK_EXPIRED"):
        await SDKIssuer(browser, clock=lambda: 10000).issue(ServerSeed.from_dict(seed_data(), now=10000))
    browser.start.assert_not_called()


@pytest.mark.asyncio
async def test_cancel_during_sdk_wait_closes_owned_target_and_browser():
    from src.auth.server_renewal import SDKIssuer

    entered = asyncio.Event()
    async with sdk_peer(fault="pending", sdk_started=entered) as (browser, _commands, closed, state):
        task = asyncio.create_task(SDKIssuer(browser, clock=lambda: 1000).issue(ServerSeed.from_dict(seed_data(), now=1000)))
        await entered.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert closed == ["owned"]
        assert state == {"running": False, "exits": 1}


@pytest.mark.asyncio
@pytest.mark.parametrize("outcome", ["success", "failure", "cancel", "startup_timeout"])
async def test_owned_browser_always_stops_process_and_removes_profile(monkeypatch, outcome):
    from src.auth.server_renewal import OwnedChromium

    calls, profiles, stopped = [], [], []
    process = SimpleNamespace(pid=987654, returncode=None, wait=AsyncMock(return_value=0))

    async def spawn(*args, **kwargs):
        calls.append((args, kwargs))
        profile = Path(next(arg.split("=", 1)[1] for arg in args if arg.startswith("--user-data-dir=")))
        profiles.append(profile)
        assert profile.stat().st_mode & 0o077 == 0
        (profile / "DevToolsActivePort").write_text("54321\n/devtools/browser/test-id\n")
        return process

    monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn)
    monkeypatch.setattr("os.killpg", lambda pid, sig: stopped.append((pid, sig)))
    browser = OwnedChromium("test-chromium", no_sandbox=True)
    if outcome == "startup_timeout":
        monkeypatch.setattr(browser, "wait_ready", AsyncMock(side_effect=TimeoutError))
    try:
        async with browser.start() as address:
            assert address == "http://127.0.0.1:54321"
            if outcome == "failure":
                raise SessionError("TEST")
            if outcome == "cancel":
                raise asyncio.CancelledError
    except (SessionError, asyncio.CancelledError) as error:
        assert outcome != "success"
        if outcome == "startup_timeout":
            assert isinstance(error, SessionError) and error.code == "BROWSER_START"
    assert stopped and stopped[0][0] == process.pid
    process.wait.assert_awaited()
    assert all(not profile.exists() for profile in profiles)
    args, kwargs = calls[0]
    assert "--headless=new" in args and "--remote-debugging-address=127.0.0.1" in args
    assert "--remote-debugging-port=0" in args
    assert kwargs["stdout"] == kwargs["stderr"] == asyncio.subprocess.DEVNULL


@pytest.mark.asyncio
@pytest.mark.skipif(os.name != "posix", reason="process groups require POSIX")
async def test_owned_browser_stops_descendant_that_outlives_group_leader():
    from src.auth.server_renewal import OwnedChromium

    ready = asyncio.Event()
    child_ids = []

    async def receive(reader, writer):
        child_ids.append(int(await reader.readline()))
        writer.close()
        await writer.wait_closed()
        ready.set()

    server = await asyncio.start_server(receive, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    child_code = (
        "import os,signal,socket; signal.signal(signal.SIGTERM, signal.SIG_IGN); "
        f"s=socket.create_connection(('127.0.0.1',{port})); "
        "s.sendall((str(os.getpid())+'\\n').encode()); s.close(); signal.pause()"
    )
    leader_code = f"import subprocess,sys,signal; subprocess.Popen([sys.executable,'-c',{child_code!r}]); signal.pause()"
    process = await asyncio.create_subprocess_exec(
        sys.executable, "-c", leader_code, start_new_session=True,
        stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL,
    )
    try:
        await asyncio.wait_for(ready.wait(), 5)
        await OwnedChromium.stop(process)
        ps = await asyncio.create_subprocess_exec(
            "ps", "-o", "stat=", "-p", str(child_ids[0]), stdout=asyncio.subprocess.PIPE,
        )
        output, _ = await ps.communicate()
        assert not output.strip() or output.strip().startswith(b"Z"), "live browser descendant escaped cleanup"
    finally:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        await process.wait()
        server.close()
        await server.wait_closed()


@pytest.mark.asyncio
@pytest.mark.skipif(os.name != "posix", reason="POSIX signal lifecycle")
async def test_cli_sigterm_cancels_task_and_runs_cleanup(tmp_path):
    ready = asyncio.Event()
    marker = tmp_path / "cleanup-ran"

    async def receive(reader, writer):
        await reader.readline()
        writer.close()
        await writer.wait_closed()
        ready.set()

    server = await asyncio.start_server(receive, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    code = f"""
import asyncio
from pathlib import Path
from src.auth.server_renewal import ServerRenewalHelper
async def waiting(args):
    try:
        reader, writer = await asyncio.open_connection('127.0.0.1', {port})
        writer.write(b'ready\\n')
        await writer.drain()
        writer.close()
        await writer.wait_closed()
        await asyncio.Event().wait()
    finally:
        Path({str(marker)!r}).write_text('cleaned')
ServerRenewalHelper.run = staticmethod(waiting)
ServerRenewalHelper.main()
"""
    process = await asyncio.create_subprocess_exec(
        sys.executable, "-c", code, "--seed", "unused", "--connection", "unused",
        stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL,
    )
    try:
        await asyncio.wait_for(ready.wait(), 5)
        process.terminate()
        assert await asyncio.wait_for(process.wait(), 5) == 143
        assert marker.read_text() == "cleaned"
    finally:
        if process.returncode is None:
            process.kill()
            await process.wait()
        server.close()
        await server.wait_closed()


@pytest.mark.asyncio
async def test_second_cancellation_cannot_interrupt_browser_cleanup(monkeypatch):
    from src.auth.server_renewal import OwnedChromium

    inside, stopping, release = asyncio.Event(), asyncio.Event(), asyncio.Event()
    profiles, stopped = [], []

    async def spawn(*args, **kwargs):
        profile = Path(next(arg.split("=", 1)[1] for arg in args if arg.startswith("--user-data-dir=")))
        profiles.append(profile)
        (profile / "DevToolsActivePort").write_text("54321\n/devtools/browser/test-id\n")
        return SimpleNamespace(returncode=None)

    async def stop(process):
        stopping.set()
        await release.wait()
        stopped.append(True)

    monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn)
    monkeypatch.setattr(OwnedChromium, "stop", staticmethod(stop))

    async def run():
        async with OwnedChromium("test-chromium").start():
            inside.set()
            await asyncio.Event().wait()

    task = asyncio.create_task(run())
    try:
        await inside.wait()
        task.cancel()
        await stopping.wait()
        task.cancel()
        await asyncio.sleep(0)  # Deliver the second cancellation, not a timed delay.
        assert not task.done(), "cleanup was interrupted"
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert stopped == [True]
        assert all(not profile.exists() for profile in profiles)
    finally:
        release.set()
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
async def test_source_restarts_from_server_replacement_and_validates_before_save(tmp_path):
    from src.auth.server_renewal import ServerContextSource

    path = tmp_path / "seed.json"
    initial = seed_data()
    initial["bundle"]["captured_at"] = 500
    initial["bundle"]["expires_at"] = 900  # Initial token is already expired.
    PrivateSessionFile(path).write(initial)
    seen = []
    transport = SimpleNamespace(validate=AsyncMock(), close=AsyncMock())

    async def issue(seed):
        seen.append(seed)
        data = seed.to_dict()
        data["bundle"].update(captured_at=1000, expires_at=4600 + len(seen))
        data["bundle"]["headers"]["client-integrity"] = "new-proof-" + str(len(seen))
        data["sdk_cookie"] = {"value": "new-cookie-" + str(len(seen)), "expires_at": 87400 + len(seen)}
        return ServerSeed.from_dict(data, now=1000)

    issuer = SimpleNamespace(issue=AsyncMock(side_effect=issue))
    for _ in range(2):
        source = ServerContextSource(path, issuer, 123, transport=transport, clock=lambda: 1000)
        bundle = await source.capture()
        transport.validate.assert_awaited_with(bundle, 123)
        stored = ServerSeed.from_dict(PrivateSessionFile(path).read(), now=1000)
        assert stored.bundle == bundle
        assert stored.cookie.value == "new-cookie-" + str(len(seen))
    assert seen[1].cookie.value == "new-cookie-1"
    assert seen[1].bundle.headers["client-integrity"] == "new-proof-1"
    assert transport.close.await_count == 2
    assert path.stat().st_mode & 0o077 == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["CATALOG", "ACCOUNT_MISMATCH", "SAVE"])
async def test_source_preserves_last_seed_on_rejection_or_failed_save(tmp_path, monkeypatch, failure):
    from src.auth.server_renewal import ServerContextSource

    path = tmp_path / "seed.json"
    PrivateSessionFile(path).write(seed_data())
    previous = path.read_bytes()
    candidate = seed_data()
    candidate["bundle"]["headers"]["client-integrity"] = "new-private-proof"
    candidate["bundle"]["expires_at"] = 5600
    candidate["sdk_cookie"].update(value="new-private-cookie", expires_at=87400)
    issuer = SimpleNamespace(issue=AsyncMock(return_value=ServerSeed.from_dict(candidate, now=1000)))
    transport = SimpleNamespace(validate=AsyncMock(), close=AsyncMock())
    if failure == "SAVE":
        monkeypatch.setattr(PrivateSessionFile, "write", lambda *args: (_ for _ in ()).throw(SessionError("SAVE")))
    else:
        transport.validate.side_effect = SessionError(failure)
    with pytest.raises(SessionError, match=failure) as error:
        await ServerContextSource(path, issuer, 123, transport=transport, clock=lambda: 1000).capture()
    assert path.read_bytes() == previous
    assert "private" not in str(error.value)
    transport.close.assert_awaited_once()


@pytest.mark.asyncio
async def test_expired_sdk_state_ends_loop_without_repeated_browser_attempts():
    from src.auth.session_renewal import RenewalLoop

    source = SimpleNamespace(capture=AsyncMock(side_effect=SessionError("SDK_EXPIRED")))
    sender = SimpleNamespace(send=AsyncMock())
    sleep = AsyncMock(side_effect=AssertionError("must not retry an expired SDK seed"))
    with pytest.raises(SessionError, match="SDK_EXPIRED"):
        await RenewalLoop(source, sender, sleep=sleep).run()
    sender.send.assert_not_awaited()
    sleep.assert_not_awaited()
