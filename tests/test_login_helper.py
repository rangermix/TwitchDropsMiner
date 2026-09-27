"""Native login handoff: destination, admission, browser lifecycle and privacy."""

import asyncio
import subprocess
import sys
from contextlib import asynccontextmanager
from pathlib import Path
from unittest.mock import AsyncMock, Mock

import pytest
from aiohttp import web

from src.auth import login_helper
from src.auth.server_seed import SDKCookie, ServerSeed
from src.auth.session_bundle import SessionBundle, SessionError
from src.config import ClientType


def test_native_entrypoint_explains_direct_connection_without_export_files():
    result = subprocess.run(
        [sys.executable, "login_helper.py", "--help"],
        cwd=Path(__file__).resolve().parents[1],
        capture_output=True, text=True, encoding="utf-8", check=False,
    )
    assert result.returncode == 0
    assert "--tdm" in result.stdout
    assert "--output" not in result.stdout
    assert "--server-seed" not in result.stdout

CLOCK = 1000
CONNECTION = "A" * 43


def seed():
    return ServerSeed(SessionBundle.from_dict({
        "version": 1, "captured_at": 1000, "expires_at": 4600,
        "user_agent": "Test Chrome", "headers": {
            "authorization": "OAuth secret-test-auth", "client-integrity": "secret-integrity",
            "client-id": ClientType.WEB.CLIENT_ID, "x-device-id": "test-device",
        },
    }, now=CLOCK), SDKCookie("secret-sdk-cookie", 9000))


def connected():
    return {"version": 1, "connection": CONNECTION, "expires_at": 1600}


def accepted():
    return {"success": True, "session": {
        "state": "ready", "user_id": 123, "generation": 2, "expires_at": 4700,
    }, "allow_helper_connection": False}


@asynccontextmanager
async def instance(connect=None, complete=None, *, connect_status=200, complete_status=200, headers=None, lose_ack=False, receipts=None, complete_text=None):
    requests = []
    results = list(receipts or [accepted()])

    async def handler(request):
        body = await request.json() if request.can_read_body else None
        requests.append((request.path, dict(request.headers), body))
        if request.path.endswith("result"):
            value = results.pop(0) if len(results) > 1 else results[0]
            return web.json_response(value)
        if lose_ack and request.path.endswith("session"):
            request.transport.close()
            return web.Response()
        if complete_text is not None and request.path.endswith("session"):
            return web.Response(text=complete_text, status=complete_status)
        value = (connected() if connect is None else connect) if request.path.endswith("connect") else (accepted() if complete is None else complete)
        status = connect_status if request.path.endswith("connect") else complete_status
        return web.json_response(value, status=status, headers=headers)

    app = web.Application()
    app.router.add_post("/api/helper/connect", handler)
    app.router.add_post("/api/helper/session", handler)
    app.router.add_get("/api/helper/result", handler)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    address = f"http://127.0.0.1:{site._server.sockets[0].getsockname()[1]}"
    try:
        yield address, requests
    finally:
        await runner.cleanup()


@pytest.mark.parametrize("value", [
    "https://user:secret@example.test", "file:///private", "http://example.test/path",
    "http://example.test?token=secret", "http://example.test#secret", "http://example.test?",
    "http://example.test#", "ftp://example.test", "http://example.test\\@other.test",
    "http://example.test:99999", "http://example.test\n", "https://", "//example.test",
])
def test_destination_rejects_ambiguous_or_credential_bearing_input(value):
    with pytest.raises(SessionError, match="HELPER_DESTINATION") as error:
        login_helper.HelperDestination(value)
    assert "secret" not in str(error.value)


@pytest.mark.parametrize("value,expected", [
    ("http://192.168.1.2:8080/", "http://192.168.1.2:8080"),
    ("https://tdm.example.test", "https://tdm.example.test"),
    ("http://[::1]:8080/", "http://[::1]:8080"),
])
def test_explicit_lan_http_and_root_https_destinations_are_allowed(value, expected):
    assert login_helper.HelperDestination(value).url == expected


@pytest.mark.asyncio
async def test_protocol_uses_only_selected_instance_and_keeps_tickets_out_of_repr():
    async with instance(headers={"Set-Cookie": "ignored=server-secret"}) as (address, requests):
        async with login_helper.HelperHTTP(login_helper.HelperDestination(address), clock=lambda: CLOCK) as client:
            ticket = await client.connect()
            assert CONNECTION not in repr(ticket)
            assert await client.send(ticket, seed()) == 123
    assert [row[0] for row in requests] == ["/api/helper/connect", "/api/helper/session"]
    assert requests[0][2] == {}
    assert requests[1][2] == seed().to_dict()
    assert requests[1][1]["Authorization"] == f"Bearer {CONNECTION}"
    assert all(row[1]["X-TDM-Request"] == "1" for row in requests)
    assert all("Cookie" not in row[1] for row in requests)


@pytest.mark.asyncio
@pytest.mark.parametrize("response", [
    {"version": True, "connection": CONNECTION, "expires_at": 1600},
    {"version": 2, "connection": CONNECTION, "expires_at": 1600},
    {"version": 1, "connection": "secret\nheader", "expires_at": 1600},
    {"version": 1, "connection": CONNECTION, "expires_at": 900},
    {"version": 1, "connection": CONNECTION, "expires_at": float("inf")},
    {"version": 1, "connection": CONNECTION, "expires_at": 999999},
    [], "private-error", {"version": 1},
])
async def test_admission_rejects_untrusted_or_expired_protocol(response):
    async with instance(connect=response) as (address, _requests):
        async with login_helper.HelperHTTP(login_helper.HelperDestination(address), clock=lambda: CLOCK) as client:
            with pytest.raises(SessionError, match="HELPER_RESPONSE"):
                await client.connect()


@pytest.mark.asyncio
@pytest.mark.parametrize("response", [
    {**accepted(), "success": False}, {**accepted(), "allow_helper_connection": True},
    {**accepted(), "session": {**accepted()["session"], "state": "validating"}},
    {**accepted(), "session": {**accepted()["session"], "expires_at": 900}},
    {**accepted(), "session": {**accepted()["session"], "user_id": True}},
    {**accepted(), "session": {**accepted()["session"], "generation": 0}},
    {"success": True}, [],
])
async def test_upload_never_reports_success_without_ready_renewed_server_state(response):
    async with instance(complete=response, receipts=[response]) as (address, _requests):
        async with login_helper.HelperHTTP(login_helper.HelperDestination(address), clock=lambda: CLOCK,
                receipt_attempts=2, receipt_interval=0) as client:
            with pytest.raises(SessionError, match="HELPER_RESULT_UNKNOWN"):
                await client.send(await client.connect(), seed())


@pytest.mark.asyncio
async def test_redirect_cannot_receive_credentials():
    async with instance() as (other, other_requests):
        async with instance(complete_status=307, headers={"Location": other + "/api/helper/session"}) as (address, _requests):
            async with login_helper.HelperHTTP(login_helper.HelperDestination(address), clock=lambda: CLOCK) as client:
                with pytest.raises(SessionError, match="HELPER_REDIRECT"):
                    await client.send(await client.connect(), seed())
        assert other_requests == []


@pytest.mark.asyncio
async def test_expired_admission_is_not_submitted():
    now = [CLOCK]
    async with instance() as (address, requests):
        async with login_helper.HelperHTTP(login_helper.HelperDestination(address), clock=lambda: now[0]) as client:
            ticket = await client.connect()
            now[0] = 1600
            with pytest.raises(SessionError, match="HELPER_EXPIRED"):
                await client.send(ticket, seed())
        assert len(requests) == 1


class FakeBrowser:
    address = "http://127.0.0.1:9222"

    def __init__(self, *, authentication_error=None):
        self.authentication_error = authentication_error
        self.entered = False
        self.closed = False

    async def __aenter__(self):
        self.entered = True
        return self

    async def __aexit__(self, *args):
        self.closed = True

    async def wait_authenticated(self, *, timeout):
        assert 0 < timeout <= 600
        if self.authentication_error:
            raise self.authentication_error


@pytest.mark.asyncio
async def test_disabled_admission_does_not_launch_a_browser():
    browser = FakeBrowser()
    output = []
    async with instance(connect={"detail": "session_helper_disabled"}, connect_status=403) as (address, requests):
        helper = login_helper.NativeLoginHelper(address, browser_factory=lambda: browser, clock=lambda: CLOCK, report=output.append)
        with pytest.raises(SessionError, match="HELPER_DISABLED"):
            await helper.run()
        assert not browser.entered
        assert len(requests) == 1


@pytest.mark.asyncio
async def test_handoff_closes_browser_before_reporting_success_and_never_writes_exports(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    browser = FakeBrowser()
    exporter = Mock(capture_seed=AsyncMock(return_value=seed()))
    output = []

    def report(key):
        if key == "success":
            assert browser.closed
        output.append(key)

    async with instance() as (address, requests):
        helper = login_helper.NativeLoginHelper(address, browser_factory=lambda: browser,
            exporter_factory=lambda _address, **kwargs: exporter, clock=lambda: CLOCK, report=report)
        await helper.run()
        assert browser.entered and browser.closed
        assert len(requests) == 2
        assert output[-1] == "success"
        assert list(tmp_path.rglob("*")) == []


@pytest.mark.asyncio
@pytest.mark.parametrize("error", [SessionError("CAPTURE_TIMEOUT"), asyncio.CancelledError()])
async def test_capture_failure_and_cancellation_close_browser_without_success_or_upload(error):
    browser = FakeBrowser()
    exporter = Mock(capture_seed=AsyncMock(side_effect=error))
    output = []
    async with instance() as (address, requests):
        helper = login_helper.NativeLoginHelper(address, browser_factory=lambda: browser,
            exporter_factory=lambda _address, **kwargs: exporter, clock=lambda: CLOCK, report=output.append)
        with pytest.raises(type(error)):
            await helper.run()
        assert browser.closed
        assert len(requests) == 1
        assert "success" not in output


@pytest.mark.asyncio
async def test_server_validation_failure_cleans_profile_and_redacts_response(capsys):
    browser = FakeBrowser()
    exporter = Mock(capture_seed=AsyncMock(return_value=seed()))
    output = []
    async with instance(complete={"detail": "secret-sdk-cookie"}, complete_status=400) as (address, _requests):
        helper = login_helper.NativeLoginHelper(address, browser_factory=lambda: browser,
            exporter_factory=lambda _address, **kwargs: exporter, clock=lambda: CLOCK, report=output.append)
        with pytest.raises(SessionError, match="HELPER_REJECTED") as error:
            await helper.run()
        assert browser.closed
        assert "secret" not in str(error.value)
        assert "success" not in output
        assert not capsys.readouterr().out


@pytest.mark.asyncio
async def test_native_browser_removes_owned_profile_after_launch_failure(monkeypatch, tmp_path):
    monkeypatch.setattr(login_helper.tempfile, "tempdir", str(tmp_path))
    executable = tmp_path / "chrome"
    executable.touch()
    monkeypatch.setattr(login_helper.subprocess, "Popen", Mock(side_effect=OSError("private-details")))
    with pytest.raises(SessionError, match="HELPER_BROWSER"):
        async with login_helper.NativeChrome(executable=executable):
            pytest.fail("Browser launch must fail")
    assert list(tmp_path.iterdir()) == [executable]


@pytest.mark.asyncio
async def test_native_browser_arguments_cleanup_and_no_ordinary_profile_access(monkeypatch, tmp_path):
    monkeypatch.setattr(login_helper.tempfile, "tempdir", str(tmp_path))
    executable = tmp_path / "chrome"
    executable.touch()
    ordinary = tmp_path / "ordinary-profile"
    ordinary.mkdir()
    (ordinary / "Cookies").write_text("private-existing-state")
    process = Mock(pid=777, poll=Mock(return_value=None), wait=Mock(return_value=0))
    command = []

    def launch(arguments, **kwargs):
        command.extend(arguments)
        profile = Path(next(arg.split("=", 1)[1] for arg in arguments if arg.startswith("--user-data-dir=")))
        assert profile != ordinary
        (profile / "DevToolsActivePort").write_text("9222\n/devtools/browser/test-id\n")
        (profile / "Cookies").write_text("temporary-sensitive-state")
        assert kwargs["stderr"] == subprocess.DEVNULL
        return process

    monkeypatch.setattr(login_helper.subprocess, "Popen", launch)
    monkeypatch.setattr(login_helper.NativeChrome, "available_port", staticmethod(lambda: 9222))
    monkeypatch.setattr(login_helper.NativeChrome, "_wait_ready", AsyncMock())
    monkeypatch.setattr(login_helper.NativeChrome, "_request_close", AsyncMock())
    async with login_helper.NativeChrome(executable=executable) as browser:
        profile = browser.profile
        assert profile is not None and profile.exists()
        assert browser.address == "http://127.0.0.1:9222"
        assert "https://www.twitch.tv/login" in command
        assert "--remote-debugging-port=9222" in command
        assert "--remote-debugging-address=127.0.0.1" in command
        assert "--enable-automation" not in command
        assert "--headless" not in command
    assert not profile.exists()
    assert (ordinary / "Cookies").read_text() == "private-existing-state"
    process.wait.assert_called()


def test_invalid_destination_cli_error_does_not_echo_untrusted_url():
    result = subprocess.run([sys.executable, "login_helper.py", "--tdm", "https://user:secret@example.test"],
        cwd=Path(__file__).resolve().parents[1], capture_output=True, text=True, encoding="utf-8", check=False)
    assert result.returncode == 1
    assert "secret" not in result.stdout + result.stderr
    assert "HELPER_DESTINATION" in result.stdout + result.stderr


@pytest.mark.asyncio
async def test_native_browser_removes_auxiliary_temp_files_without_touching_parent_temp(monkeypatch, tmp_path):
    monkeypatch.setattr(login_helper.tempfile, "tempdir", str(tmp_path))
    shared = tmp_path / "system-temp"
    shared.mkdir()
    unrelated = shared / "unrelated"
    unrelated.write_text("preserve-existing-state")
    for name in ("TMPDIR", "TMP", "TEMP"):
        monkeypatch.setenv(name, str(shared))
    executable = tmp_path / "chrome"
    executable.touch()
    process = Mock(pid=777, poll=Mock(return_value=None), wait=Mock(return_value=0))
    auxiliary = []

    def launch(_arguments, **kwargs):
        for name in ("TMPDIR", "TMP", "TEMP"):
            artifact = Path(kwargs["env"][name]) / (".com.google.Chrome.test-" + name)
            artifact.write_text("temporary-browser-data")
            auxiliary.append(artifact)
        return process

    monkeypatch.setattr(login_helper.subprocess, "Popen", launch)
    monkeypatch.setattr(login_helper.NativeChrome, "_wait_ready", AsyncMock())
    monkeypatch.setattr(login_helper.NativeChrome, "_request_close", AsyncMock())
    async with login_helper.NativeChrome(executable=executable) as browser:
        profile = browser.profile
        assert all(path.exists() for path in auxiliary)
    assert all(not path.exists() for path in auxiliary)
    assert profile is not None and not profile.exists()
    assert unrelated.read_text() == "preserve-existing-state"
    assert list(shared.iterdir()) == [unrelated]
    assert all(login_helper.os.environ[name] == str(shared) for name in ("TMPDIR", "TMP", "TEMP"))


@pytest.mark.asyncio
async def test_lost_ack_recovers_receipt_without_reposting_credentials():
    async with instance(lose_ack=True, receipts=[{"state": "pending"}, accepted()]) as (address, requests):
        async with login_helper.HelperHTTP(login_helper.HelperDestination(address), clock=lambda: CLOCK,
                receipt_attempts=3, receipt_interval=0) as client:
            assert await client.send(await client.connect(), seed()) == 123
    assert [row[0] for row in requests] == [
        "/api/helper/connect", "/api/helper/session", "/api/helper/result", "/api/helper/result"]
    assert requests[-1][2] is None
    assert requests[-1][1]["Authorization"] == f"Bearer {CONNECTION}"


@pytest.mark.asyncio
async def test_unconfirmed_lost_ack_reports_unknown_without_reopening_admission():
    async with instance(lose_ack=True, receipts=[{"state": "pending"}]) as (address, requests):
        async with login_helper.HelperHTTP(login_helper.HelperDestination(address), clock=lambda: CLOCK,
                receipt_attempts=2, receipt_interval=0) as client:
            with pytest.raises(SessionError, match="HELPER_RESULT_UNKNOWN"):
                await client.send(await client.connect(), seed())
    assert len(requests) == 4
    assert [row[0] for row in requests].count("/api/helper/connect") == 1
    assert [row[0] for row in requests].count("/api/helper/session") == 1


@pytest.mark.asyncio
async def test_login_wait_failure_closes_browser_and_does_not_capture():
    browser = FakeBrowser(authentication_error=SessionError("HELPER_LOGIN_TIMEOUT"))
    exporter = Mock()
    async with instance() as (address, requests):
        helper = login_helper.NativeLoginHelper(address, browser_factory=lambda: browser,
            exporter_factory=exporter, clock=lambda: CLOCK)
        with pytest.raises(SessionError, match="HELPER_LOGIN_TIMEOUT"):
            await helper.run()
    assert browser.closed
    exporter.assert_not_called()
    assert len(requests) == 1


def test_legacy_module_cli_no_longer_exposes_file_exports():
    result = subprocess.run([sys.executable, "-m", "src.auth.session_helper", "--help"],
        cwd=Path(__file__).resolve().parents[1], capture_output=True, text=True, encoding="utf-8", check=False)
    assert result.returncode == 0
    assert "--tdm" in result.stdout
    assert "--output" not in result.stdout


@pytest.mark.asyncio
async def test_protocol_accepts_a_json_reply_delivered_in_multiple_chunks(monkeypatch):
    import aiohttp

    first_read = asyncio.Event()
    original_read = aiohttp.StreamReader.read

    async def read(stream, count=-1):
        data = await original_read(stream, count)
        if data == b'{"version":1,':
            first_read.set()
        return data

    monkeypatch.setattr(aiohttp.StreamReader, "read", read)
    async def handler(request):
        await request.read()
        response = web.StreamResponse(headers={"Content-Type": "application/json"})
        await response.prepare(request)
        await response.write(b'{"version":1,')
        await first_read.wait()
        await response.write((f'"connection":"{CONNECTION}","expires_at":1600' + '}').encode())
        await response.write_eof()
        return response

    app = web.Application()
    app.router.add_post("/api/helper/connect", handler)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    try:
        address = f"http://127.0.0.1:{site._server.sockets[0].getsockname()[1]}"
        async with login_helper.HelperHTTP(login_helper.HelperDestination(address), clock=lambda: CLOCK) as client:
            assert (await client.connect()).connection == CONNECTION
    finally:
        await runner.cleanup()


@pytest.mark.asyncio
async def test_cleanup_removes_profile_even_if_owned_process_termination_errors(monkeypatch, tmp_path):
    profile = tmp_path / "owned"
    profile.mkdir()
    (profile / "Cookies").write_text("private-state")
    browser = login_helper.NativeChrome()
    browser.profile = profile
    browser.process = Mock(wait=Mock(side_effect=OSError("private-system-error")))
    monkeypatch.setattr(browser, "_request_close", AsyncMock())
    with pytest.raises(SessionError, match="HELPER_BROWSER_CLEANUP"):
        await browser.close()
    assert not profile.exists()


def test_frozen_chrome_launch_restores_external_library_environment(monkeypatch):
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "_MEIPASS", "/private/bundle", raising=False)
    monkeypatch.setenv("LD_LIBRARY_PATH", "/private/bundle:/system/original")
    monkeypatch.setenv("LD_LIBRARY_PATH_ORIG", "/system/original")
    monkeypatch.setenv("PATH", "/private/bundle/bin" + login_helper.os.pathsep + "/usr/bin")
    environment = login_helper.NativeChrome.external_environment()
    assert environment["LD_LIBRARY_PATH"] == "/system/original"
    assert environment["PATH"] == "/usr/bin"
    assert login_helper.os.environ["LD_LIBRARY_PATH"] == "/private/bundle:/system/original"


def test_unfrozen_chrome_launch_preserves_user_environment(monkeypatch):
    monkeypatch.delattr(sys, "frozen", raising=False)
    monkeypatch.setenv("LD_LIBRARY_PATH", "/user/libraries")
    assert login_helper.NativeChrome.external_environment()["LD_LIBRARY_PATH"] == "/user/libraries"


@pytest.mark.asyncio
async def test_native_browser_waits_for_twitch_authentication_without_exporting_cookie(monkeypatch):
    import json

    address = []
    commands = []

    async def version(request):
        return web.json_response({"webSocketDebuggerUrl": address[0].replace("http:", "ws:") + "/devtools/browser/test"})

    async def socket(request):
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        async for message in ws:
            data = message.json()
            commands.append(data["method"])
            await ws.send_json({"id": data["id"], "result": {"cookies": [
                {"name": "auth-token", "domain": ".twitch.tv", "value": "secret-test-cookie"}]}})
        return ws

    app = web.Application()
    app.router.add_get("/json/version", version)
    app.router.add_get("/devtools/browser/test", socket)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    address.append(f"http://127.0.0.1:{site._server.sockets[0].getsockname()[1]}")
    browser = login_helper.NativeChrome()
    browser.address = address[0]
    browser.process = Mock(poll=Mock(return_value=None))
    try:
        assert await browser.wait_authenticated(timeout=5) is None
        assert commands == ["Storage.getCookies"]
        assert "secret-test-cookie" not in json.dumps(browser.__dict__, default=str)
    finally:
        await runner.cleanup()


@pytest.mark.asyncio
async def test_native_chrome_uses_nonzero_debug_port_to_preserve_normal_browser_launch(monkeypatch, tmp_path):
    monkeypatch.setattr(login_helper.tempfile, "tempdir", str(tmp_path))
    executable = tmp_path / "chrome"
    executable.touch()
    process = Mock(pid=123, poll=Mock(return_value=None), wait=Mock(return_value=0))
    arguments = []

    def launch(command, **kwargs):
        arguments.extend(command)
        # Legacy implementation waits for this file; the new one verifies its peer.
        profile = Path(next(a.split("=", 1)[1] for a in command if a.startswith("--user-data-dir=")))
        (profile / "DevToolsActivePort").write_text("9222\n/devtools/browser/test-id\n")
        return process

    monkeypatch.setattr(login_helper.subprocess, "Popen", launch)
    monkeypatch.setattr(login_helper.NativeChrome, "_wait_ready", AsyncMock(), raising=False)
    monkeypatch.setattr(login_helper.NativeChrome, "_request_close", AsyncMock())
    async with login_helper.NativeChrome(executable=executable):
        assert "--remote-debugging-port=0" not in arguments
        port = int(next(a.split("=", 1)[1] for a in arguments if a.startswith("--remote-debugging-port=")))
        assert 0 < port <= 65535


@pytest.mark.asyncio
@pytest.mark.parametrize("owned", [False, True])
async def test_browser_close_verifies_process_owner_before_sending_close(owned):
    address, commands = [], []

    async def version(request):
        return web.json_response({"webSocketDebuggerUrl": address[0].replace("http:", "ws:") + "/devtools/browser/peer"})

    async def socket(request):
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        async for message in ws:
            data = message.json()
            commands.append(data["method"])
            result = {"processInfo": [{"type": "browser", "id": 123 if owned else 456}]}
            await ws.send_json({"id": data["id"], "result": result})
        return ws

    app = web.Application()
    app.router.add_get("/json/version", version)
    app.router.add_get("/devtools/browser/peer", socket)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    address.append(f"http://127.0.0.1:{site._server.sockets[0].getsockname()[1]}")
    browser = login_helper.NativeChrome()
    browser.address = address[0]
    browser.process = Mock(pid=123)
    try:
        if owned:
            await browser._request_close()
            assert commands == ["SystemInfo.getProcessInfo", "Browser.close"]
        else:
            with pytest.raises(SessionError, match="HELPER_BROWSER_OWNER"):
                await browser._request_close()
            assert commands == ["SystemInfo.getProcessInfo"]
    finally:
        await runner.cleanup()


@pytest.mark.asyncio
@pytest.mark.parametrize("status,text", [(504, "<html>gateway timeout</html>"), (200, '{"success":'), (200, '{}')])
async def test_ambiguous_upload_response_recovers_committed_receipt(status, text):
    async with instance(complete_status=status, complete_text=text, receipts=[{"state": "pending"}, accepted()]) as (address, requests):
        async with login_helper.HelperHTTP(login_helper.HelperDestination(address), clock=lambda: CLOCK,
                receipt_attempts=3, receipt_interval=0) as client:
            assert await client.send(await client.connect(), seed()) == 123
    assert [row[0] for row in requests] == ["/api/helper/connect", "/api/helper/session", "/api/helper/result", "/api/helper/result"]


@pytest.mark.asyncio
async def test_cancellation_during_close_waits_for_owned_process_and_profile_cleanup(monkeypatch, tmp_path):
    profile = tmp_path / "owned"
    profile.mkdir()
    (profile / "Cookies").write_text("private-state")
    browser = login_helper.NativeChrome()
    browser.profile = profile
    process = Mock(pid=123, wait=Mock(return_value=0))
    browser.process = process
    entered, release = asyncio.Event(), asyncio.Event()

    async def request_close():
        entered.set()
        await release.wait()

    monkeypatch.setattr(browser, "_request_close", request_close)
    closing = asyncio.create_task(browser.close())
    await entered.wait()
    closing.cancel()
    await asyncio.sleep(0)
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await closing
    process.wait.assert_called()
    assert browser.process is None
    assert not profile.exists()


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX terminal signals")
@pytest.mark.parametrize("signal_name", ["SIGTERM", "SIGHUP"])
def test_cli_termination_drains_cleanup_before_exit(tmp_path, signal_name):
    import os
    import signal
    import time

    ready, cleaned = tmp_path / "ready", tmp_path / "cleaned"
    program = '''
import asyncio
from pathlib import Path
from src.auth.login_helper import LoginHelperCLI
class Helper:
    async def run(self):
        Path(READY).touch()
        try:
            await asyncio.Event().wait()
        finally:
            await asyncio.sleep(0)
            Path(CLEANED).touch()
try:
    asyncio.run(LoginHelperCLI.run_cancellable(Helper()))
except asyncio.CancelledError:
    raise SystemExit(130)
'''.replace("READY", repr(str(ready))).replace("CLEANED", repr(str(cleaned)))
    child = subprocess.Popen([sys.executable, "-c", program], stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    try:
        deadline = time.monotonic() + 5
        while not ready.exists() and child.poll() is None and time.monotonic() < deadline:
            time.sleep(.01)
        assert ready.exists(), child.communicate(timeout=1)[1]
        os.kill(child.pid, getattr(signal, signal_name))
        assert child.wait(timeout=5) == 130
        assert cleaned.exists()
    finally:
        if child.poll() is None:
            child.kill()
        child.communicate(timeout=5)
