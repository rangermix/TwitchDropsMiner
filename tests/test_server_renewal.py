"""Server SDK issuance against isolated protocol peers; never contacts Twitch."""

import asyncio
import base64
import json
import time
import os
import shutil
import signal
import sys
from contextlib import asynccontextmanager, suppress
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

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


async def serve_pipe(read_fd, write_fd, handle, closed):
    """A fake browser speaking flattened CDP over real NUL-delimited pipes."""
    loop = asyncio.get_running_loop()
    reader = asyncio.StreamReader(limit=16 * 1024 * 1024)
    read_file, write_file = open(read_fd, "rb", buffering=0), open(write_fd, "wb", buffering=0)  # noqa: SIM115 - transports close them
    read_transport, _ = await loop.connect_read_pipe(lambda: asyncio.StreamReaderProtocol(reader), read_file)
    write_transport, protocol = await loop.connect_write_pipe(asyncio.streams.FlowControlMixin, write_file)
    writer = asyncio.StreamWriter(write_transport, protocol, None, loop)

    async def send(data):
        writer.write(json.dumps(data).encode() + b"\0")
        await writer.drain()

    try:
        while True:
            try:
                command = json.loads((await reader.readuntil(b"\0"))[:-1])
            except asyncio.IncompleteReadError:
                return
            method, params, session = command["method"], command.get("params", {}), command.get("sessionId")
            if session is None:
                result = {}
                if method == "Target.createTarget":
                    assert params == {"url": "about:blank"}
                    result = {"targetId": "owned"}
                elif method == "Target.attachToTarget":
                    assert params == {"targetId": "owned", "flatten": True}
                    result = {"sessionId": "page-session"}
                elif method == "Target.closeTarget":
                    closed.append(params["targetId"])
                await send({"id": command["id"], "result": result})
                continue
            assert session == "page-session"

            async def reply(result, command=command, session=session):
                await send({"id": command["id"], "result": result, "sessionId": session})

            async def event(method, params, session=session):
                await send({"method": method, "params": params, "sessionId": session})

            await handle(method, params, reply, event)
    finally:
        read_transport.close()
        write_transport.close()


@asynccontextmanager
async def sdk_peer(*, fault=None, sdk_started=None, transport="pipe" if os.name == "posix" else "tcp"):
    from src.auth.session_helper import BrowserExporter, DevToolsPipe

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

            async def reply(result, command=command):
                await ws.send_json({"id": command["id"], "result": result})

            await handle(command["method"], command.get("params", {}), reply, event)
        return ws

    async def handle(method, params, reply, event):
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
                return
            assert "private-sdk-cookie" not in params["functionDeclaration"]
            headers = params["arguments"][0]["value"]
            assert "client-integrity" not in headers
            assert headers["authorization"] == "OAuth test-token"
            assert len(headers['client-request-id']) == 32
            assert 'accept-language' not in headers
            assert 'device-id' not in headers
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
        await reply(result)
        if method == "Page.navigate":
            assert params == {"url": BrowserSession.PAGE}
            await event("Fetch.requestPaused", {"requestId": "document", "resourceType": "Document", "request": {"url": BrowserSession.PAGE, "method": "GET"}})
        if method == "Fetch.fulfillRequest":
            await event("Page.loadEventFired", {"timestamp": 1})

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
                if transport == "tcp":
                    yield BrowserExporter(address[0])
                    return
                child_in, parent_out = os.pipe()
                parent_in, child_out = os.pipe()
                peer = asyncio.create_task(serve_pipe(child_in, child_out, handle, closed))
                try:
                    async with DevToolsPipe.open(parent_out, parent_in) as pipe:
                        yield pipe
                finally:
                    await asyncio.wait_for(peer, 5)  # closing our ends ends the peer
            finally:
                state["running"] = False
                state["exits"] += 1

    try:
        yield Browser(), commands, closed, state
    finally:
        await runner.cleanup()


@pytest.mark.asyncio
@pytest.mark.parametrize("transport", ["pipe", "tcp"] if os.name == "posix" else ["tcp"])
async def test_sdk_issuer_correlates_network_and_keeps_only_renewed_seed(transport):
    from src.auth.server_renewal import SDKIssuer

    seed = ServerSeed.from_dict(seed_data(), now=1000)
    async with sdk_peer(transport=transport) as (browser, commands, closed, state):
        renewed = await SDKIssuer(browser, clock=lambda: 1000).issue(seed)
        assert renewed.bundle.headers["client-integrity"] == "private-new-integrity"
        assert renewed.bundle.expires_at == 5600
        assert renewed.bundle.user_agent == "Server Chromium"
        assert 'client-request-id' not in renewed.bundle.headers
        assert renewed.cookie.value == "server-sdk-cookie"
        assert renewed.cookie.expires_at > seed.cookie.expires_at
        assert state == {"running": False, "exits": 1}
        assert closed == ["owned"]
        assert "Browser.close" not in [method for method, _ in commands]
        assert "private" not in repr(renewed)


def test_integrity_headers_use_fresh_request_id_and_preserve_device_identity():
    from src.auth.server_renewal import SDKAcquisition

    data = seed_data()
    headers = data['bundle']['headers']
    device = headers.pop('x-device-id', headers.get('device-id', 'device'))
    headers['device-id'] = device
    headers['accept-language'] = 'fr-FR'
    headers['client-session-id'] = 'session'
    headers['client-version'] = 'version'
    bundle = ServerSeed.from_dict(data, now=1000).bundle
    first, second = SDKAcquisition.issuance_headers(bundle), SDKAcquisition.issuance_headers(bundle)
    assert first['client-request-id'] != second['client-request-id']
    assert first['x-device-id'] == device
    assert first['client-session-id'] == 'session' and first['client-version'] == 'version'
    assert set(first) == {'authorization', 'client-id', 'x-device-id', 'client-request-id',
                          'client-session-id', 'client-version'}
    assert bundle.headers['accept-language'] == 'fr-FR'


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
@pytest.mark.parametrize("platform", ["posix", "nt"] if os.name == "posix" else ["nt"])
@pytest.mark.parametrize("outcome", ["success", "failure", "cancel", "startup_timeout"])
@pytest.mark.parametrize("displayed", [False, True])
async def test_owned_browser_always_stops_process_and_removes_profile(monkeypatch, outcome, platform, displayed):
    from src.auth import server_renewal
    from src.auth.session_helper import BrowserExporter, DevToolsPipe

    if platform != os.name:
        # Exercise the Windows loopback-port branch; pathlib and tempfile keep the host OS.
        monkeypatch.setattr(server_renewal, "os", SimpleNamespace(name=platform, environ=os.environ))
    if displayed and platform != "posix":
        pytest.skip("Private X display is POSIX only")
    calls, profiles, stopped = [], [], []
    async def wait():
        process.returncode = 0
        return 0

    process = SimpleNamespace(
        pid=987654, returncode=None, wait=AsyncMock(side_effect=wait),
        terminate=Mock(side_effect=lambda: stopped.append((987654, "terminate"))),
        kill=Mock(side_effect=lambda: stopped.append((987654, "kill"))),
    )

    async def spawn(*args, **kwargs):
        calls.append((args, kwargs))
        profile = Path(next(arg.split("=", 1)[1] for arg in args if arg.startswith("--user-data-dir=")))
        profiles.append(profile)
        if displayed:
            (profile / "Xauthority").write_text("synthetic authority")
        if os.name == "posix":
            assert profile.stat().st_mode & 0o077 == 0
        (profile / "DevToolsActivePort").write_text("54321\n/devtools/browser/test-id\n")
        return process

    monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn)
    monkeypatch.setattr("src.auth.server_renewal.shutil.which", lambda name: "/usr/bin/xvfb-run" if displayed else None)
    if platform == "posix":
        monkeypatch.setattr("os.killpg", lambda pid, sig: stopped.append((pid, sig)))
    browser = server_renewal.OwnedChromium("test-chromium", no_sandbox=True)
    if platform == "posix":
        monkeypatch.setattr(browser, "wait_pipe", AsyncMock())  # The fake process never speaks CDP.
    if outcome == "startup_timeout":
        monkeypatch.setattr(browser, "wait_pipe" if platform == "posix" else "wait_ready", AsyncMock(side_effect=TimeoutError))
    descriptors = set(os.listdir("/dev/fd")) if os.path.isdir("/dev/fd") else None
    try:
        async with browser.start() as owned:
            if platform == "posix":
                assert isinstance(owned, DevToolsPipe)
            else:
                assert isinstance(owned, BrowserExporter) and owned.address == "http://127.0.0.1:54321"
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
    assert ("--headless=new" in args) is not displayed
    assert kwargs["stdout"] == kwargs["stderr"] == asyncio.subprocess.DEVNULL
    assert kwargs["start_new_session"] is (platform == "posix")
    if platform == "posix":
        assert "--remote-debugging-pipe" in args
        assert not any(arg.startswith("--remote-debugging-") and arg != "--remote-debugging-pipe" for arg in args)
        assert args[:4] == (sys.executable, "-I", "-S", "-c")
        assert args[5:7] == tuple(map(str, kwargs["pass_fds"]))
        if displayed:
            # The launcher remaps fds 3/4, then execs xvfb-run, which runs Chromium with them.
            assert args[7:14] == ("/usr/bin/xvfb-run", "-a", "-f", str(profiles[0] / "Xauthority"),
                                  "-s", "-screen 0 1280x800x24 -nolisten tcp", "test-chromium")
            assert "--window-size=1200,760" in args
        else:
            assert args[7] == "test-chromium"
        if descriptors is not None:
            assert set(os.listdir("/dev/fd")) == descriptors, "a pipe descriptor leaked"
        assert stopped == [(process.pid, signal.SIGTERM), (process.pid, signal.SIGKILL)]
    else:
        assert "--remote-debugging-address=127.0.0.1" in args and "--remote-debugging-port=0" in args
        assert "--remote-debugging-pipe" not in args and "pass_fds" not in kwargs
        assert stopped == [(process.pid, "terminate")]


FAKE_CHROMIUM = r"""
import json, os, sys
fds = sorted(int(fd) for fd in os.listdir("/proc/self/fd" if os.path.isdir("/proc/self/fd") else "/dev/fd"))
profile = next(arg.split("=", 1)[1] for arg in sys.argv if arg.startswith("--user-data-dir="))
with open(os.path.join(os.path.dirname(profile), "launch.json"), "w") as report:
    json.dump({"argv": sys.argv[1:], "fds": fds}, report)
commands, replies = os.fdopen(3, "rb", buffering=0), os.fdopen(4, "wb", buffering=0)
buffer = b""
while chunk := commands.read(65536):
    buffer += chunk
    while b"\0" in buffer:
        raw, buffer = buffer.split(b"\0", 1)
        command = json.loads(raw)
        result = {"Target.createTarget": {"targetId": "fake-target"},
                  "Target.attachToTarget": {"sessionId": "fake-session"}}.get(command["method"], {})
        if command.get("sessionId") == "fake-session":
            result = {"result": {"value": "fake page"}}
        replies.write(json.dumps({**command, "result": result, "method": None}).encode() + b"\0")
"""


@pytest.mark.asyncio
@pytest.mark.skipif(os.name != "posix", reason="Chromium's debugging pipe requires POSIX descriptor passing")
async def test_owned_browser_pipe_reaches_fake_chromium_on_fds_3_and_4(tmp_path, monkeypatch):
    from src.auth.server_renewal import OwnedChromium

    executable = tmp_path / "fake-chromium"
    executable.write_text(f"#!{sys.executable}\n{FAKE_CHROMIUM}")
    executable.chmod(0o700)
    monkeypatch.setattr("tempfile.tempdir", str(tmp_path))
    async with OwnedChromium(str(executable), startup_timeout=10).start() as browser, browser.target() as page:
        assert await page.command("Runtime.evaluate", {"expression": "1"}) == {"result": {"value": "fake page"}}
    report = json.loads((tmp_path / "launch.json").read_text())
    assert "--remote-debugging-pipe" in report["argv"]
    assert not any(arg.startswith("--remote-debugging-") and arg != "--remote-debugging-pipe" for arg in report["argv"])
    # Only stdio and the two remapped pipe ends reach Chromium (listing adds one descriptor).
    assert report["fds"][:5] == [0, 1, 2, 3, 4] and len(report["fds"]) <= 6


@pytest.mark.asyncio
@pytest.mark.skipif(os.name != "posix", reason="Chromium's debugging pipe requires POSIX descriptor passing")
async def test_pipe_routes_bursts_ignores_other_sessions_and_fails_on_browser_exit():
    from src.auth.session_helper import DevToolsPipe

    child_in, parent_out = os.pipe()
    parent_in, child_out = os.pipe()
    try:
        async with DevToolsPipe.open(parent_out, parent_in) as pipe:
            pending = asyncio.create_task(pipe.browser.command("Browser.getVersion"))
            assert json.loads((await asyncio.to_thread(os.read, child_in, 4096)).rstrip(b"\0")) == {"id": 1, "method": "Browser.getVersion", "params": {}}
            os.write(child_out, b'{"id": 1, "result": {"forged": true}, "sessionId": "other"}\0')
            # One read can carry far more small events than any count-bounded queue.
            os.write(child_out, b'{"method": "Network.dataReceived", "params": {"requestId": "r"}}\0' * 400)
            os.write(child_out, b'{"id": 1, "result": {"product": "Chrome"}}\0')
            assert await asyncio.wait_for(pending, 5) == {"product": "Chrome"}
            pending = asyncio.create_task(pipe.browser.command("Browser.getVersion"))
            os.close(child_out)
            child_out = -1
            with pytest.raises(SessionError, match="BROWSER_PROTOCOL"):
                await asyncio.wait_for(pending, 5)
    finally:
        for fd in (child_in, child_out):
            if fd != -1:
                os.close(fd)


@pytest.mark.asyncio
@pytest.mark.parametrize("detach", [
    {"method": "Target.detachedFromTarget", "params": {"sessionId": "S1"}},
    {"method": "Inspector.detached", "params": {}, "sessionId": "S1"},
])
async def test_pipe_rejects_new_commands_after_page_detach(detach):
    from src.auth.session_helper import DevToolsConnection, DevToolsPipe

    reader = asyncio.StreamReader()
    writer = SimpleNamespace(write=Mock(), drain=AsyncMock())
    pipe = DevToolsPipe(reader, writer)
    page = DevToolsConnection(pipe._session("S1"), ids=pipe._ids)
    try:
        reader.feed_data(json.dumps(detach).encode() + b"\0")
        assert await asyncio.wait_for(page.events.get(), 1) is None
        with pytest.raises(SessionError, match="BROWSER_PROTOCOL"):
            await asyncio.wait_for(page.command("Runtime.evaluate", timeout=30), 1)
        writer.write.assert_not_called()
        assert not page._pending
    finally:
        await page.close()
        await pipe.close()


@pytest.mark.asyncio
@pytest.mark.skipif(os.name != "posix", reason="Chromium's debugging pipe requires POSIX descriptor passing")
async def test_pipe_ends_detached_page_sessions_and_shares_command_ids():
    from src.auth.session_helper import DevToolsConnection, DevToolsPipe

    child_in, parent_out = os.pipe()
    parent_in, child_out = os.pipe()

    async def sent():
        return json.loads((await asyncio.to_thread(os.read, child_in, 4096)).rstrip(b"\0"))

    try:
        async with DevToolsPipe.open(parent_out, parent_in) as pipe:
            for detach in (b'{"method": "Target.detachedFromTarget", "params": {"sessionId": "S1"}}\0',
                           b'{"method": "Inspector.detached", "params": {}, "sessionId": "S1"}\0'):
                page = DevToolsConnection(pipe._session("S1"), ids=pipe._ids)
                browser_command = asyncio.create_task(pipe.browser.command("Browser.getVersion"))
                browser_request = await sent()
                page_command = asyncio.create_task(page.command("Runtime.evaluate", timeout=30))
                page_request = await sent()
                # Ids are unique across the pipe, so a session-less error reply for a
                # detached session cannot complete the browser's own command.
                assert page_request["id"] != browser_request["id"]
                os.write(child_out, detach)
                with pytest.raises(SessionError, match="BROWSER_PROTOCOL"):
                    await asyncio.wait_for(page_command, 2)
                error = {"id": page_request["id"], "error": {"code": -32001, "message": "Session not found"}}
                os.write(child_out, json.dumps(error).encode() + b"\0")
                os.write(child_out, json.dumps({"id": browser_request["id"], "result": {"ok": 1}}).encode() + b"\0")
                assert await asyncio.wait_for(browser_command, 2) == {"ok": 1}
                await page.close()
    finally:
        for fd in (child_in, child_out):
            os.close(fd)


def listening_inodes() -> set[str]:
    inodes = set()
    for table in ("/proc/net/tcp", "/proc/net/tcp6"):
        with open(table) as rows:
            for row in rows.readlines()[1:]:
                fields = row.split()
                if fields[3] == "0A":  # TCP_LISTEN
                    inodes.add(fields[9])
    return inodes


CHROMIUM = next(filter(None, map(shutil.which, ("chromium", "chromium-browser", "google-chrome"))), None)
# The sandbox is irrelevant to the transport under test, and unprivileged user
# namespaces are blocked on some hosts (such as Ubuntu 24.04 CI runners). A cold
# first launch on a fresh CI runner can exceed the production startup timeout.


@pytest.mark.asyncio
@pytest.mark.skipif(CHROMIUM is None or not os.path.exists("/proc/net/tcp"),
                    reason="requires Linux and a local Chromium")
async def test_real_owned_chromium_opens_no_tcp_listener(monkeypatch):
    from src.auth.server_renewal import OwnedChromium

    processes = []
    spawn = asyncio.create_subprocess_exec

    async def record(*args, **kwargs):
        processes.append(await spawn(*args, **kwargs))
        return processes[-1]

    monkeypatch.setattr(asyncio, "create_subprocess_exec", record)
    async with OwnedChromium(CHROMIUM, no_sandbox=True, startup_timeout=60).start() as browser, browser.target() as page:
        result = await page.command("Runtime.evaluate", {"expression": "navigator.userAgent", "returnByValue": True})
        assert "Chrome" in result["result"]["value"]
        group = processes[0].pid
        sockets = set()
        for entry in os.listdir("/proc"):
            if not entry.isdecimal():
                continue
            with suppress(OSError):
                if os.getpgid(int(entry)) != group:
                    continue
                for fd in os.listdir(f"/proc/{entry}/fd"):
                    with suppress(OSError):
                        link = os.readlink(f"/proc/{entry}/fd/{fd}")
                        if link.startswith("socket:["):
                            sockets.add(link[8:-1])
        assert sockets, "could not inspect the browser process group"
        assert not sockets & listening_inodes(), "the owned browser opened a TCP listener"


@pytest.mark.asyncio
@pytest.mark.skipif(CHROMIUM is None or os.name != "posix", reason="requires POSIX and a local Chromium")
async def test_real_chromium_closed_target_fails_page_commands_fast():
    from src.auth.server_renewal import OwnedChromium

    async with OwnedChromium(CHROMIUM, no_sandbox=True, startup_timeout=60).start() as browser, browser.target() as page:
        targets = await browser.browser.command("Target.getTargets")
        target = next(info["targetId"] for info in targets["targetInfos"] if info["type"] == "page" and info.get("attached"))
        await browser.browser.command("Target.closeTarget", {"targetId": target})
        started = time.monotonic()
        with pytest.raises(SessionError, match="BROWSER_PROTOCOL"):
            await page.command("Runtime.evaluate", {"expression": "1"}, timeout=30)
        assert time.monotonic() - started < 5


@pytest.mark.asyncio
@pytest.mark.parametrize("platform", ["posix", "nt"])
@pytest.mark.parametrize("timeout", [False, True])
async def test_owned_browser_cleanup_uses_platform_api_and_escalates(monkeypatch, platform, timeout):
    from src.auth import server_renewal

    waits = 0

    async def wait():
        nonlocal waits
        waits += 1
        if timeout and waits == 1:
            raise TimeoutError
        process.returncode = 0
        return 0

    process = SimpleNamespace(
        pid=987654, returncode=None, wait=AsyncMock(side_effect=wait),
        terminate=Mock(), kill=Mock(),
    )
    killpg = Mock()
    # Patch only this module's OS/signal bindings; do not change pathlib's host OS.
    monkeypatch.setattr(server_renewal, "os", SimpleNamespace(name=platform, killpg=killpg))
    signals = {"SIGTERM": 15}
    if platform == "posix":
        signals["SIGKILL"] = 9
    monkeypatch.setattr(server_renewal, "signal", SimpleNamespace(**signals))
    await server_renewal.OwnedChromium.stop(process)
    assert process.returncode == 0
    assert process.wait.await_count == (2 if timeout else 1)
    if platform == "posix":
        assert killpg.call_args_list == [((process.pid, 15),), ((process.pid, 9),)]
        process.terminate.assert_not_called()
        process.kill.assert_not_called()
    else:
        killpg.assert_not_called()
        process.terminate.assert_called_once()
        assert process.kill.call_count == int(timeout)


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
        with suppress(ProcessLookupError):
            os.killpg(process.pid, signal.SIGKILL)
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
    monkeypatch.setattr(OwnedChromium, "wait_pipe", AsyncMock())  # The fake process never speaks CDP.

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
    if os.name == "posix":
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


@pytest.mark.asyncio
@pytest.mark.parametrize('fault', [None, 'http_error', 'cache', 'mismatch', 'missing_cookie', 'expired_cookie'])
async def test_fresh_token_bootstrap_requires_observed_proof_and_fresh_cookie(fault):
    from src.auth.server_renewal import SDKIssuer
    async with sdk_peer(fault=fault) as (browser, commands, closed, state):
        issuer = SDKIssuer(browser, clock=lambda: 1000)
        if fault is None:
            result = await issuer.bootstrap('test-token')
            assert result.bundle.token == 'test-token'
            assert result.bundle.user_agent == 'Server Chromium'
            assert result.cookie.expires_at > result.bundle.expires_at
        else:
            with pytest.raises(SessionError):
                await issuer.bootstrap('test-token')
        assert not any(method == 'Network.setCookies' for method, _ in commands)
        assert not state['running'] and closed == ['owned']
