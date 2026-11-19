"""Container Firefox login stays manual, isolated and verified before capture."""

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
    browser = LoginFirefox({})
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
    browser = LoginFirefox({"DISPLAY": ":private"})
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
    browser = LoginFirefox({})
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
    browser = LoginFirefox({})
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
async def test_firefox_cleanup_removes_profile_and_closes_protocol_on_stop_failure(tmp_path, monkeypatch):
    browser = LoginFirefox({})
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


@pytest.mark.parametrize("firefox", [None, "/usr/bin/firefox"])
def test_default_login_prefers_installed_firefox_without_changing_chromium_fallback(monkeypatch, firefox):
    monkeypatch.setattr("src.auth.container_login.shutil.which",
                        lambda command: firefox if command in ("firefox", "firefox-esr") else "/usr/bin/chromium")
    browser = create_login_browser({"DISPLAY": ":private"})
    assert type(browser) is (LoginFirefox if firefox else LoginChromium)


def test_standard_image_includes_firefox_and_chromium():
    dockerfile = (Path(__file__).resolve().parents[1] / "Dockerfile").read_text()
    packages = next(line for line in dockerfile.splitlines() if "apk add" in line and "chromium" in line).split()
    assert "firefox" in packages and "chromium" in packages
