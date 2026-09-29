"""Keep Firefox's manual sign-in outside WebDriver, then verify saved login."""

import asyncio
import os
from pathlib import Path
from unittest.mock import AsyncMock, Mock

import pytest

from src.auth import login_helper
from src.auth.session_bundle import SessionError


@pytest.mark.parametrize("platform", ["linux", "darwin", "win32"])
def test_firefox_manual_login_does_not_enable_browser_automation(monkeypatch, tmp_path, platform):
    monkeypatch.setattr(login_helper.sys, "platform", platform)
    browser = login_helper.NativeFirefox()
    browser.profile = tmp_path
    args = browser.launch_arguments(Path("firefox"), 9222)
    assert "--remote-debugging-port" not in args
    assert "--marionette" not in args
    assert browser.LOGIN_URL in args
    assert ("--wait-for-browser" in args) == (platform == "win32")


def test_firefox_child_does_not_inherit_automation_or_system_access(monkeypatch):
    names = ("MOZ_MARIONETTE", "MOZ_MARIONETTE_PREF_STATE_ACROSS_RESTARTS", "MOZ_REMOTE_ALLOW_SYSTEM_ACCESS")
    for name in names:
        monkeypatch.setenv(name, "1")
    original = dict(os.environ)
    with login_helper.NativeFirefox.external_libraries() as environment:
        assert all(name not in environment for name in names)
    assert dict(os.environ) == original


@pytest.mark.asyncio
@pytest.mark.parametrize("fault,code", [(None, None), ("missing", "HELPER_FIREFOX_LOGIN"),
    ("foreign", "HELPER_FIREFOX_LOGIN"), ("malformed", "BROWSER_PROTOCOL"),
    ("owner", "HELPER_BROWSER_OWNER"), ("crash", "HELPER_BROWSER"), ("restart", "HELPER_BROWSER")])
async def test_firefox_closes_manual_process_before_restarting_owned_profile(monkeypatch, tmp_path, fault, code):
    executable = tmp_path / "firefox"
    executable.touch()
    monkeypatch.setattr(login_helper.tempfile, "tempdir", str(tmp_path))
    manual = Mock(pid=100, poll=Mock(return_value=None), returncode=0, wait=Mock(return_value=0))
    capture = Mock(pid=200, poll=Mock(return_value=None), wait=Mock(return_value=0))
    cookie = {"name": "auth-token", "domain": "evil.invalid" if fault == "foreign" else ".twitch.tv",
              "value": {"type": "string", "value": "synthetic-secret"}}
    response = {"cookies": [] if fault == "missing" else [cookie]}
    remote = Mock(command=AsyncMock(return_value=None if fault == "malformed" else response), close=AsyncMock())
    launches = []
    browser = login_helper.NativeFirefox(executable)
    original_ready = browser._wait_ready

    def launch(args, **kwargs):
        launches.append(args)
        if len(launches) == 1:
            assert browser.remote is None
            assert "--remote-debugging-port" not in args
            return manual
        assert manual.poll() == 0 and manual.returncode == 0
        assert browser.remote is None and not browser._verified
        assert args[args.index("--profile") + 1] == launches[0][launches[0].index("--profile") + 1]
        assert (browser.profile / "retained-state").read_text() == "synthetic-state"
        assert browser.LOGIN_URL not in args and args[-1] == "about:blank"
        assert int(args[args.index("--remote-debugging-port") + 1]) > 0
        for name in ("TMPDIR", "TMP", "TEMP"):
            assert Path(kwargs["env"][name]) == browser.profile / "tmp"
        if fault == "restart":
            raise OSError("private-error")
        return capture

    async def ready():
        if not browser._capturing:
            await original_ready()
            return
        browser.remote = remote
        browser._require_firefox_owner({"capabilities": {"moz:processID": 999 if fault == "owner" else 200,
            "moz:profile": str(browser.profile), "browserVersion": "156.0.1"}})

    monkeypatch.setattr(login_helper.subprocess, "Popen", launch)
    monkeypatch.setattr(browser, "_wait_ready", ready)
    monkeypatch.setattr(browser, "_kill_owned_process", Mock())

    async with browser:
        profile = browser.profile
        assert profile is not None and browser.remote is None and len(launches) == 1
        (profile / "retained-state").write_text("synthetic-state")
        manual.returncode = 1 if fault == "crash" else 0
        manual.poll.return_value = manual.returncode
        if code:
            with pytest.raises(SessionError, match=code):
                await browser.wait_authenticated(timeout=1)
        else:
            await browser.wait_authenticated(timeout=1)
            assert browser._verified and browser.process is capture
    assert not profile.exists() and browser.profile is None and browser.process is None
    assert len(launches) == (1 if fault == "crash" else 2)
    if fault in ("crash", "restart", "owner"):
        remote.command.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("cancel", [False, True])
async def test_firefox_manual_wait_timeout_or_cancel_never_starts_capture(monkeypatch, tmp_path, cancel):
    executable = tmp_path / "firefox"
    executable.touch()
    monkeypatch.setattr(login_helper.tempfile, "tempdir", str(tmp_path))
    process = Mock(pid=100, poll=Mock(return_value=None), wait=Mock(return_value=0))
    launch = Mock(return_value=process)
    monkeypatch.setattr(login_helper.subprocess, "Popen", launch)
    browser = login_helper.NativeFirefox(executable)
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
        assert not browser._capturing and browser.remote is None
    launch.assert_called_once()
    process.wait.assert_called_once()
    assert not profile.exists()
