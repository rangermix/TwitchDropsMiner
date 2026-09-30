"""Browser ownership, state transitions, retry and resource cleanup."""

import asyncio
import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.auth.container_login import BrowserIsolation, ContainerDesktop, ContainerLogin, LoginChromium
from src.auth.session_bundle import SessionError
from tests.test_session_controller import seed


class Desktop:
    def __init__(self):
        self.environment = {"DISPLAY": ":private"}
        self.port = 12345
        self.closed = False

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        self.closed = True


class Browser:
    def __init__(self, environment):
        self.exit = asyncio.Event()
        self.process = SimpleNamespace(wait=self.exit.wait)
        self.capture = AsyncMock(return_value=seed())
        self.closed = False

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        self.closed = True

    async def finish(self):
        self.exit.set()


async def state(login, expected):
    async with asyncio.timeout(1):
        while login.state != expected:
            await asyncio.sleep(0)


@pytest.fixture
def attempt():
    desktop = Desktop()
    browsers = []

    def create(environment):
        browser = Browser(environment)
        browsers.append(browser)
        return browser

    controller = SimpleNamespace(accept=AsyncMock(), session=SimpleNamespace(status=lambda: {"state": "waiting"}))
    return ContainerLogin(controller, desktop_factory=lambda: desktop, browser_factory=create), desktop, browsers


@pytest.mark.asyncio
async def test_login_finishes_verifies_and_cleans_before_dashboard(attempt):
    login, desktop, browsers = attempt
    entered, release = asyncio.Event(), asyncio.Event()

    async def accept(seed, *, authorized):
        assert authorized()
        entered.set()
        await release.wait()

    login.controller.accept.side_effect = accept
    login.request_login()
    login.request_login()
    await state(login, "sign_in")
    assert len(browsers) == 1 and login.attempt == 1
    await login.finish()
    await entered.wait()
    assert login.state == "verifying" and not browsers[0].closed
    release.set()
    await state(login, "idle")
    assert browsers[0].closed and desktop.closed
    assert login.desktop is None and login.browser is None
    login.controller.accept.assert_awaited_once()


@pytest.mark.asyncio
async def test_capture_failure_is_redacted_and_retry_uses_new_attempt(attempt):
    login, desktop, browsers = attempt
    login.request_login()
    await state(login, "sign_in")
    browsers[0].capture.side_effect = SessionError("LOGIN_MISSING")
    await login.finish()
    await state(login, "error")
    assert login.status() == {"state": "error", "error": "LOGIN_MISSING", "attempt": 1}
    assert browsers[0].closed and desktop.closed
    await login.retry()
    await state(login, "sign_in")
    assert len(browsers) == 2 and login.attempt == 2
    await login.stop()
    assert browsers[1].closed and login.state == "idle"


@pytest.mark.asyncio
async def test_external_renewal_closes_waiting_browser(attempt):
    login, desktop, browsers = attempt
    login.request_login()
    await state(login, "sign_in")
    login.controller.session.status = lambda: {"state": "ready"}
    login.session_changed()
    await state(login, "idle")
    assert desktop.closed and browsers[0].closed
    login.controller.accept.assert_not_awaited()


@pytest.mark.asyncio
async def test_startup_failure_does_not_expose_exception_text(attempt):
    login, desktop, browsers = attempt
    login.desktop_factory = lambda: (_ for _ in ()).throw(OSError("private credential"))
    login.request_login()
    await state(login, "error")
    assert login.error == "BROWSER_FAILED"
    assert "private" not in str(login.status())


@pytest.mark.asyncio
async def test_logout_cancel_resets_a_failed_attempt_before_new_login(attempt):
    login, desktop, browsers = attempt
    login.change("error", "BROWSER_FAILED")
    await login.cancel()
    login.request_login()
    await state(login, "sign_in")
    assert login.error is None
    await login.stop()


@pytest.mark.asyncio
async def test_window_finish_requests_normal_close_only_for_owned_pid():
    browser = LoginChromium({"DISPLAY": ":owned"})
    browser.process = SimpleNamespace(pid=123)
    browser.window_command = AsyncMock(side_effect=[b"44\n", b"123\n", b""])
    await browser.finish()
    assert browser.window_command.await_args_list[-1].args == ("windowquit", "44")
    browser.window_command = AsyncMock(side_effect=[b"44\n", b"999\n"])
    with pytest.raises(SessionError, match="BROWSER_CLOSE"):
        await browser.finish()
    assert browser.window_command.await_count == 2


@pytest.mark.parametrize("data", [None, [], {}, {"processInfo": None}, {"processInfo": [{"type": "browser", "id": 999}]}])
def test_capture_cannot_attach_a_foreign_or_malformed_browser(data):
    browser = LoginChromium({})
    browser.process = SimpleNamespace(pid=123)
    with pytest.raises(SessionError, match="BROWSER_OWNER"):
        browser.require_owner(data)
    browser.require_owner({"processInfo": [{"type": "renderer", "id": 999}, {"type": "browser", "id": 123}]})


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["desktop", "browser"])
async def test_repeated_cancellation_drains_processes_and_removes_profiles(tmp_path, monkeypatch, kind):
    resource = ContainerDesktop() if kind == "desktop" else LoginChromium({})
    directory = tmp_path / "private"
    directory.mkdir()
    (directory / "cookie").write_text("private credential")
    process = SimpleNamespace(pid=123)
    if kind == "desktop":
        resource.root, resource.processes = directory, [process]
    else:
        resource.profile, resource.process = directory, process
    entered, release = asyncio.Event(), asyncio.Event()

    async def stop(process):
        entered.set()
        await release.wait()

    monkeypatch.setattr("src.auth.container_login.OwnedChromium.stop", stop)
    closing = asyncio.create_task(resource.close())
    await entered.wait()
    closing.cancel()
    await asyncio.sleep(0)
    closing.cancel()
    await asyncio.sleep(0)
    assert not closing.done()
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await closing
    assert not directory.exists()
    await resource.close()


def test_interactive_environment_omits_miner_secrets(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "private token")
    monkeypatch.setenv("CHROMIUM_USER_FLAGS", "--allow-file-access")
    monkeypatch.setenv("TZ", "Australia/Sydney")
    environment = BrowserIsolation.environment()
    assert environment.get("TZ") == "Australia/Sydney"
    assert "TELEGRAM_BOT_TOKEN" not in environment and "CHROMIUM_USER_FLAGS" not in environment


@pytest.mark.skipif(os.name != "posix", reason="Integrated desktop is Linux only")
def test_private_data_directory_is_protected_and_unsupported_mounts_fail_closed(tmp_path, monkeypatch):
    isolation = object.__new__(BrowserIsolation)
    isolation.uid = 10001
    private = tmp_path / "data"
    private.mkdir()
    (private / "settings.json").write_text("private token")
    isolation.protect(private)
    assert private.stat().st_mode & 0o777 == 0o700
    private.chmod(0o777)
    monkeypatch.setattr(Path, "chmod", lambda path, mode: None)
    with pytest.raises(SessionError, match="BROWSER_ISOLATION"):
        isolation.protect(private)


@pytest.mark.skipif(os.name != "posix" or getattr(os, "geteuid", lambda: -1)() != 0,
                    reason="Kernel UID isolation requires a root Linux test process")
def test_browser_uid_cannot_read_miner_files_even_when_the_file_is_world_readable():
    isolation = object.__new__(BrowserIsolation)
    isolation.uid, isolation.gid = 10001, 10001
    parent = Path(tempfile.mkdtemp(prefix="tdm-isolation-test-"))
    try:
        parent.chmod(0o755)
        private = parent / "data"
        private.mkdir(mode=0o755)
        sentinel = private / "settings.json"
        sentinel.write_text("fake miner secret")
        sentinel.chmod(0o644)

        def read_as_browser():
            return subprocess.run(["/bin/cat", str(sentinel)], capture_output=True,
                                  user=isolation.uid, group=isolation.gid, extra_groups=[])

        assert read_as_browser().stdout == b"fake miner secret"
        isolation.protect(private)
        denied = read_as_browser()
        assert denied.returncode != 0 and not denied.stdout
        os.chown(private, isolation.uid, isolation.gid)
        with pytest.raises(SessionError, match="BROWSER_ISOLATION"):
            isolation.protect(private)
    finally:
        shutil.rmtree(parent)
