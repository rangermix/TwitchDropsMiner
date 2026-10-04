"""Chrome/Chromium manual sign-in, owned restart, discovery and failure recovery."""

import asyncio
from pathlib import Path
from unittest.mock import Mock

import pytest
from aiohttp import web

from src.auth import login_helper
from src.auth.session_bundle import SessionError


@pytest.mark.asyncio
@pytest.mark.parametrize("browser_type", [login_helper.NativeChrome, login_helper.NativeChromium])
@pytest.mark.parametrize("fault,code", [(None, None), ("missing", "HELPER_LOGIN_MISSING"),
    ("foreign", "HELPER_LOGIN_MISSING"), ("empty", "HELPER_LOGIN_MISSING"),
    ("malformed", "BROWSER_PROTOCOL"), ("owner", "HELPER_BROWSER_OWNER"),
    ("crash", "HELPER_BROWSER"), ("restart", "HELPER_BROWSER")])
async def test_manual_close_reopens_same_profile_and_verifies_saved_login(monkeypatch, tmp_path, browser_type, fault, code):
    commands, launches, address = [], [], []

    async def version(request):
        return web.json_response({"webSocketDebuggerUrl": address[0].replace("http:", "ws:") + "/devtools/browser/test"})

    async def socket(request):
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        async for message in ws:
            data = message.json()
            commands.append(data["method"])
            if data["method"] == "SystemInfo.getProcessInfo":
                result = {"processInfo": [{"type": "browser", "id": 999 if fault == "owner" else 200}]}
            elif data["method"] == "Storage.getCookies":
                cookie = {"name": "auth-token", "domain": "evil.invalid" if fault == "foreign" else ".twitch.tv",
                          "value": "" if fault == "empty" else "synthetic-secret"}
                result = None if fault == "malformed" else {"cookies": [] if fault == "missing" else [cookie]}
            else:
                assert data["method"] == "Browser.close"
                result = {}
            await ws.send_json({"id": data["id"], "result": result})
        return ws

    app = web.Application()
    app.router.add_get("/json/version", version)
    app.router.add_get("/devtools/browser/test", socket)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]
    address.append(f"http://127.0.0.1:{port}")
    executable = tmp_path / "browser"
    executable.touch()
    monkeypatch.setattr(login_helper.tempfile, "tempdir", str(tmp_path))
    manual = Mock(pid=100, poll=Mock(return_value=None), returncode=0, wait=Mock(return_value=0))
    capture = Mock(pid=200, poll=Mock(return_value=None), wait=Mock(return_value=0))
    browser = browser_type(executable)

    def launch(args, **kwargs):
        launches.append(args)
        assert "--enable-automation" not in args and "--headless" not in args
        assert f"--user-data-dir={browser.profile}" in args
        for name in ("TMPDIR", "TMP", "TEMP"):
            assert Path(kwargs["env"][name]) == browser.profile / "tmp"
        if len(launches) == 1:
            assert not any(arg.startswith("--remote-debugging-") for arg in args)
            assert browser.LOGIN_URL in args and not browser.address
            return manual
        assert manual.poll() == manual.returncode == 0
        assert (browser.profile / "retained-state").read_text() == "synthetic-state"
        assert launches[0][0] == args[0] == str(executable)
        assert browser.LOGIN_URL not in args and args[-1] == "about:blank"
        assert f"--remote-debugging-port={port}" in args and port > 0
        assert "--remote-debugging-address=127.0.0.1" in args
        if fault == "restart":
            raise OSError("private-error")
        return capture

    monkeypatch.setattr(login_helper.subprocess, "Popen", launch)
    monkeypatch.setattr(browser, "available_port", lambda: port)
    try:
        async with browser:
            profile = browser.profile
            assert profile is not None and len(launches) == 1 and not commands
            (profile / "retained-state").write_text("synthetic-state")
            manual.returncode = 1 if fault == "crash" else 0
            manual.poll.return_value = manual.returncode
            if code:
                with pytest.raises(SessionError, match=code):
                    await browser.wait_authenticated(timeout=2)
            else:
                await browser.wait_authenticated(timeout=2)
                assert browser._verified and browser.process is capture
            assert commands.count("Storage.getCookies") == (0 if fault in ("crash", "restart", "owner") else 1)
        assert not profile.exists() and browser.profile is None and browser.process is None
        assert len(launches) == (1 if fault == "crash" else 2)
        if fault in ("owner", "crash"):
            assert "Browser.close" not in commands
    finally:
        await runner.cleanup()


@pytest.mark.asyncio
@pytest.mark.parametrize("browser_type", [login_helper.NativeChrome, login_helper.NativeChromium])
@pytest.mark.parametrize("cancel", [False, True])
async def test_manual_wait_timeout_or_cancel_cleans_up_without_capture(monkeypatch, tmp_path, browser_type, cancel):
    executable = tmp_path / "browser"
    executable.touch()
    monkeypatch.setattr(login_helper.tempfile, "tempdir", str(tmp_path))
    process = Mock(pid=100, poll=Mock(return_value=None), wait=Mock(return_value=0))
    launch = Mock(return_value=process)
    monkeypatch.setattr(login_helper.subprocess, "Popen", launch)
    browser = browser_type(executable)
    async with browser:
        profile = browser.profile
        if cancel:
            waiting = asyncio.create_task(browser.wait_authenticated(timeout=60))
            await asyncio.sleep(0)
            waiting.cancel()
            with pytest.raises(asyncio.CancelledError):
                await waiting
        else:
            with pytest.raises(SessionError, match="HELPER_LOGIN_TIMEOUT"):
                await browser.wait_authenticated(timeout=.01)
        assert not browser._capturing and not browser.address
    launch.assert_called_once()
    process.wait.assert_called_once()
    assert not profile.exists()


@pytest.mark.parametrize("platform,relative", [("win32", "Chromium/Application/chrome.exe"),
    ("darwin", "Applications/Chromium.app/Contents/MacOS/Chromium")])
def test_chromium_desktop_discovery(monkeypatch, tmp_path, platform, relative):
    executable = tmp_path / relative
    executable.parent.mkdir(parents=True)
    executable.touch()
    monkeypatch.setattr(login_helper.sys, "platform", platform)
    monkeypatch.setattr(login_helper.Path, "home", classmethod(lambda cls: tmp_path))
    for name in ("PROGRAMFILES", "PROGRAMFILES(X86)", "LOCALAPPDATA"):
        monkeypatch.setenv(name, str(tmp_path))
    is_file = Path.is_file
    monkeypatch.setattr(Path, "is_file", lambda path: path == executable and is_file(path))
    assert login_helper.NativeChromium.find_chromium() == executable


@pytest.mark.parametrize("find,relative,code", [
    (login_helper.NativeChrome.find_chrome, "Google Chrome.app/Contents/MacOS/Google Chrome", "HELPER_CHROME_MISSING"),
    (login_helper.NativeChromium.find_chromium, "Chromium.app/Contents/MacOS/Chromium", "HELPER_CHROMIUM_MISSING"),
    (login_helper.NativeFirefox.find_firefox, "Firefox.app/Contents/MacOS/firefox", "HELPER_FIREFOX_MISSING"),
])
def test_macos_discovery_prefers_system_then_user_installation(monkeypatch, tmp_path, find, relative, code):
    system = Path("/Applications") / relative
    user = tmp_path / "Applications" / relative
    installed = {system, user}
    monkeypatch.setattr(login_helper.sys, "platform", "darwin")
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    monkeypatch.setattr(Path, "is_file", lambda path: path in installed)
    assert find() == system
    installed.remove(system)
    assert find() == user
    installed.remove(user)
    with pytest.raises(SessionError, match=code):
        find()


@pytest.mark.parametrize("command", ["chromium", "chromium-browser"])
def test_chromium_linux_discovery(monkeypatch, tmp_path, command):
    executable = tmp_path / command
    executable.touch()
    monkeypatch.setattr(login_helper.sys, "platform", "linux")
    monkeypatch.setattr(login_helper.shutil, "which", lambda name: str(executable) if name == command else None)
    assert login_helper.NativeChromium.find_chromium() == executable


@pytest.mark.parametrize("path", [None, Path("missing-custom-chromium")])
def test_explicit_chromium_never_falls_back(monkeypatch, path):
    monkeypatch.setattr(login_helper.NativeChromium, "find_chromium",
                        Mock(side_effect=SessionError("HELPER_CHROMIUM_MISSING")))
    for cls, name in [(login_helper.NativeChrome, "find_chrome"), (login_helper.NativeFirefox, "find_firefox")]:
        monkeypatch.setattr(cls, name, Mock(side_effect=AssertionError("Unexpected fallback")))
    browser = login_helper.BrowserSelection.create("chromium" if path is None else "auto", chromium=path)
    assert type(browser) is login_helper.NativeChromium
    with pytest.raises(SessionError, match="HELPER_CHROMIUM_MISSING"):
        browser.find_executable()


def test_auto_chromium_discovery_error_is_not_an_absent_browser(monkeypatch):
    monkeypatch.setattr(login_helper.NativeChrome, "find_chrome",
                        Mock(side_effect=SessionError("HELPER_CHROME_MISSING")))
    monkeypatch.setattr(login_helper.NativeChromium, "find_chromium",
                        Mock(side_effect=SessionError("HELPER_BROWSER_OWNER")))
    firefox = Mock(side_effect=AssertionError("Unexpected fallback"))
    monkeypatch.setattr(login_helper.NativeFirefox, "find_firefox", firefox)
    with pytest.raises(SessionError, match="HELPER_BROWSER_OWNER"):
        login_helper.BrowserSelection.create()
    firefox.assert_not_called()
