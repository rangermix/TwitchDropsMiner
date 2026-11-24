"""Container Firefox login stays manual, isolated and verified before capture."""

import asyncio
import json
import os
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.auth.container_login import LoginChromium, LoginFirefox, create_login_browser
from src.auth.session_bundle import SessionError


@pytest.mark.asyncio
async def test_firefox_profile_disables_password_saving_before_login(tmp_path, monkeypatch):
    owned = []

    class Isolation:
        def directory(self, prefix):
            return tmp_path

        def own(self, path):
            owned.append(path)

    monkeypatch.setattr("src.auth.container_login.BrowserIsolation", Isolation)
    browser = LoginFirefox({}, "firefox")
    browser.executable = "firefox"

    async def launch():
        preferences = tmp_path / "user.js"
        values = dict(json.loads("[" + line.removeprefix("user_pref(").removesuffix(");") + "]")
                      for line in preferences.read_text().splitlines())
        assert values["signon.rememberSignons"] is False
        assert preferences in owned and tmp_path / "tmp" in owned
        if os.name == "posix":
            assert preferences.stat().st_mode & 0o777 == 0o600

    browser.launch = launch
    async with browser:
        pass
    assert not tmp_path.exists()


@pytest.mark.asyncio
async def test_firefox_manual_login_has_no_automation_and_runs_as_browser_uid(tmp_path, monkeypatch):
    process = SimpleNamespace(returncode=None)
    launch = AsyncMock(return_value=process)
    monkeypatch.setattr("asyncio.create_subprocess_exec", launch)
    monkeypatch.setattr("src.auth.container_login.loopback_port", lambda: 12345)
    browser = LoginFirefox({"DISPLAY": ":private"}, "firefox")
    browser.profile, browser.executable = tmp_path, "firefox"
    browser.isolation = SimpleNamespace(options={"user": 10001, "group": 10001, "extra_groups": []})
    browser.check_ready = AsyncMock()
    await browser.launch()
    args, options = launch.call_args
    assert "https://www.twitch.tv/login" in args
    assert "--remote-debugging-port" not in args and "--marionette" not in args
    assert args[args.index("--profile") + 1] == str(tmp_path)
    assert options["user"] == options["group"] == 10001 and options["extra_groups"] == []
    assert options["env"]["DISPLAY"] == ":private" and options["env"]["HOME"] == str(tmp_path)
    assert all(options["env"][name] == str(tmp_path / "tmp") for name in ("TMPDIR", "TMP", "TEMP"))
    browser.check_ready.assert_not_awaited()
    await browser.launch(capture=True)
    args = launch.call_args.args
    assert args[args.index("--remote-debugging-port") + 1] == "12345"
    assert args[-1] == "about:blank" and "https://www.twitch.tv/login" not in args
    browser.check_ready.assert_awaited_once()


@pytest.mark.parametrize("change", [
    {"moz:processID": 999}, {"moz:processID": True}, {"moz:profile": "/foreign"},
    {"moz:profile": None}, {"browserVersion": "142.0"}, {"browserVersion": "unknown"},
])
def test_firefox_rejects_foreign_capture_or_unsupported_protocol(tmp_path, change):
    browser = LoginFirefox({}, "firefox")
    browser.profile, browser.process = tmp_path, SimpleNamespace(pid=123)
    capabilities = {"moz:processID": 123, "moz:profile": str(tmp_path), "browserVersion": "160.0"}
    browser.require_owner({"capabilities": capabilities})
    with pytest.raises(SessionError):
        browser.require_owner({"capabilities": {**capabilities, **change}})
    for invalid in (None, [], {}, {"capabilities": []}):
        with pytest.raises(SessionError, match="BROWSER_OWNER"):
            browser.require_owner(invalid)


@pytest.mark.asyncio
@pytest.mark.parametrize("fault,code", [
    (None, None), ("missing", "LOGIN_MISSING"), ("foreign", "LOGIN_MISSING"),
    ("empty", "LOGIN_MISSING"), ("malformed", "BROWSER_PROTOCOL"), ("crash", "BROWSER_START"),
])
async def test_firefox_capture_waits_for_normal_close_and_requires_twitch_cookie(tmp_path, monkeypatch, fault, code):
    browser = LoginFirefox({}, "firefox")
    browser.profile = tmp_path
    browser.process = SimpleNamespace(wait=AsyncMock(return_value=1 if fault == "crash" else 0))
    cookie = {"name": "auth-token", "domain": "evil.invalid" if fault == "foreign" else ".twitch.tv",
              "value": {"type": "string", "value": "" if fault == "empty" else "synthetic-secret"}}
    data = None if fault == "malformed" else {"cookies": [] if fault == "missing" else [cookie]}
    browser.remote = SimpleNamespace(command=AsyncMock(return_value=data))
    capture = AsyncMock(return_value=object())
    monkeypatch.setattr("src.auth.container_login.FirefoxExporter",
                        lambda *args, **kwargs: SimpleNamespace(capture_seed=capture))

    async def launch(*, capture):
        assert browser.process.wait.await_count == 1 and capture is True
        assert browser.profile == tmp_path

    browser.launch = AsyncMock(side_effect=launch)
    if code:
        with pytest.raises(SessionError, match=code):
            await browser.capture()
        capture.assert_not_awaited()
    else:
        assert await browser.capture() is capture.return_value
        capture.assert_awaited_once()
    if fault == "crash":
        browser.launch.assert_not_awaited()
        browser.remote.command.assert_not_awaited()


@pytest.mark.asyncio
async def test_firefox_closes_protocol_on_stop_failure(tmp_path, monkeypatch):
    browser = LoginFirefox({}, "firefox")
    browser.profile = tmp_path
    browser.remote = SimpleNamespace(close=AsyncMock())
    browser.http = SimpleNamespace(close=AsyncMock())
    remote, http = browser.remote, browser.http
    monkeypatch.setattr(LoginChromium, "_close", AsyncMock(side_effect=SessionError("BROWSER_STOP")))
    with pytest.raises(SessionError, match="BROWSER_STOP"):
        await browser.close()
    remote.close.assert_awaited_once()
    http.close.assert_awaited_once()
    assert browser.remote is browser.http is None


@pytest.mark.asyncio
@pytest.mark.parametrize("versions,selected", [
    ({}, "chromium"),
    ({"firefox": (b"Mozilla Firefox 143.0", 0)}, "firefox"),
    ({"firefox-esr": (b"Mozilla Firefox 153.4.0esr", 0)}, "firefox-esr"),
    ({"firefox": (b"Mozilla Firefox 140.3.0esr", 0)}, "chromium"),
    ({"firefox": (b"Mozilla Firefox 140.3.0esr", 0), "firefox-esr": (b"Mozilla Firefox 153.4.0esr", 0)}, "firefox-esr"),
    ({"firefox": (b"Mozilla Firefox 143.0", 1)}, "chromium"),
    ({"firefox": (b"unexpected output", 0)}, "chromium"),
])
async def test_login_selects_supported_firefox_before_displaying_sign_in(monkeypatch, versions, selected):
    monkeypatch.setattr("src.auth.container_login.shutil.which",
                        lambda command: f"/usr/bin/{command}" if command in (*versions, "chromium") else None)
    options = {"user": 10001, "group": 10001, "extra_groups": []}
    monkeypatch.setattr("src.auth.container_login.BrowserIsolation", lambda: SimpleNamespace(options=options))
    environment = {"DISPLAY": ":private"}

    async def launch(executable, flag, **kwargs):
        assert flag == "--version" and kwargs["env"] == environment
        assert {key: kwargs[key] for key in options} == options
        output, returncode = versions[Path(executable).name]
        return SimpleNamespace(communicate=AsyncMock(return_value=(output, b"")), returncode=returncode)

    probe = AsyncMock(side_effect=launch)
    monkeypatch.setattr("asyncio.create_subprocess_exec", probe)
    browser = await create_login_browser(environment)
    assert browser.executable == f"/usr/bin/{selected}"
    assert type(browser) is (LoginChromium if selected == "chromium" else LoginFirefox)
    if not versions:
        probe.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("interruption", [TimeoutError, asyncio.CancelledError])
async def test_interrupted_firefox_version_probe_drains_group_after_leader_exits(monkeypatch, interruption):
    process = SimpleNamespace(communicate=AsyncMock(side_effect=interruption), returncode=0)
    monkeypatch.setattr(asyncio, "create_subprocess_exec", AsyncMock(return_value=process))
    stop = AsyncMock()
    monkeypatch.setattr("src.auth.container_login.OwnedChromium.finish_stop", stop)
    probe = LoginFirefox.supports("firefox", {}, SimpleNamespace(options={}))
    if interruption is TimeoutError:
        assert not await probe
    else:
        with pytest.raises(asyncio.CancelledError):
            await probe
    stop.assert_awaited_once_with(process)


def test_standard_image_includes_firefox_and_chromium():
    dockerfile = (Path(__file__).resolve().parents[1] / "Dockerfile").read_text()
    packages = next(line for line in dockerfile.splitlines() if "apk add" in line and "chromium" in line).split()
    assert "firefox" in packages and "chromium" in packages
