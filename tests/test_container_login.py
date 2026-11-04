"""Browser ownership, state transitions, retry and resource cleanup."""

import asyncio
import json
import logging
import os
import shutil
import subprocess
import tempfile
from contextlib import suppress
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from src.auth.container_login import (
    _SHELL_LAUNCHER,
    BrowserIsolation,
    ContainerDesktop,
    ContainerLogin,
    LoginChromium,
)
from src.auth.session_bundle import SessionError
from tests.test_session_controller import seed


@pytest.mark.asyncio
async def test_password_saving_disabled_before_interactive_browser_starts(tmp_path, monkeypatch):
    class Isolation:
        def directory(self, prefix):
            return tmp_path

        def own(self, path):
            pass

    monkeypatch.setattr('src.auth.container_login.BrowserIsolation', Isolation)
    browser = LoginChromium({})
    browser.executable = 'chromium'

    async def launch():
        preferences = json.loads((tmp_path / 'Default/Preferences').read_text())
        assert preferences['credentials_enable_service'] is False
        assert preferences['profile']['password_manager_enabled'] is False
        if os.name == 'posix':
            assert (tmp_path / 'Default/Preferences').stat().st_mode & 0o777 == 0o600

    browser.launch = launch
    async with browser:
        pass


@pytest.mark.skipif(os.name != "posix", reason="Private pipe control is POSIX only")
@pytest.mark.asyncio
async def test_login_uses_a_private_pipe_and_capture_keeps_loopback(tmp_path, monkeypatch):
    launch = AsyncMock(return_value=SimpleNamespace(returncode=None))
    stop = AsyncMock()
    monkeypatch.setattr('asyncio.create_subprocess_exec', launch)
    monkeypatch.setattr('src.auth.container_login.OwnedChromium.stop', stop)
    monkeypatch.setattr('src.auth.container_login.loopback_port', lambda: 12345)
    monkeypatch.setattr('src.auth.container_login.os.geteuid', lambda: 1000, raising=False)
    browser = LoginChromium({})
    browser.profile, browser.executable = tmp_path, 'chromium'
    browser.check_ready = AsyncMock()
    await browser.launch()
    login_args = launch.call_args.args
    assert login_args[0:2] == ('/bin/bash', '-c')
    assert 'exec 3<&$in' in login_args[2] and 'exec 4>&$out' in login_args[2]
    assert login_args[3] == 'tdm-launcher' and login_args[6] == 'chromium'
    passed = launch.call_args.kwargs["pass_fds"]
    assert len(set(passed)) == 2 and not {0, 1, 2} & set(passed)
    assert '--remote-debugging-pipe' in login_args
    assert not any(arg.startswith('--remote-debugging-port') for arg in login_args)
    assert any(arg.startswith('--load-extension=') for arg in login_args)
    assert login_args[-1] == 'https://www.twitch.tv/login'
    assert '--no-sandbox' not in login_args
    await browser.launch(capture=True)
    capture_args = launch.call_args.args
    assert not any(arg.startswith('--load-extension=') for arg in capture_args)
    assert '--remote-debugging-port=12345' in capture_args
    assert '--remote-debugging-pipe' not in capture_args
    await browser.close()


@pytest.mark.skipif(os.name != "posix", reason="Pipe descriptors are POSIX only")
def test_shell_launcher_remaps_inherited_pipe_ends_and_closes_originals():
    child_in, parent_out = os.pipe()
    parent_in, child_out = os.pipe()
    child = (
        'case $ORIG_IN in 3|4) ;; *) '
        '[ -e "/proc/self/fd/$ORIG_IN" ] && echo "leak:$ORIG_IN" >&2 ;; esac; '
        'case $ORIG_OUT in 3|4) ;; *) '
        '[ -e "/proc/self/fd/$ORIG_OUT" ] && echo "leak:$ORIG_OUT" >&2 ;; esac; '
        'read line <&3; printf "reply:%s" "$line" >&4'
    )
    result = None
    reply = b""
    try:
        os.write(parent_out, b"CMD\n")
        os.close(parent_out)
        parent_out = -1
        result = subprocess.run(
            ["/bin/bash", "-c", _SHELL_LAUNCHER, "tdm-launcher",
             str(child_in), str(child_out), "/bin/sh", "-c", child],
            pass_fds=(child_in, child_out), capture_output=True, text=True, timeout=10,
            env={**os.environ, "ORIG_IN": str(child_in), "ORIG_OUT": str(child_out)})
        os.close(child_in)
        child_in = -1
        os.close(child_out)
        child_out = -1
        reply = os.read(parent_in, 100)
    finally:
        for fd in (child_in, child_out, parent_out, parent_in):
            if fd != -1:
                with suppress(OSError):
                    os.close(fd)
    assert result is not None, "launcher subprocess did not run"
    assert result.returncode == 0, result.stderr
    assert result.stderr == ""
    assert reply == b"reply:CMD"


@pytest.mark.skipif(os.name != "posix", reason="Embedded login is Linux only")
@pytest.mark.asyncio
async def test_root_miner_disables_the_login_browser_sandbox_for_default_docker(tmp_path, monkeypatch):
    launch = AsyncMock(return_value=SimpleNamespace(returncode=None))
    monkeypatch.setattr('asyncio.create_subprocess_exec', launch)
    monkeypatch.setattr('src.auth.container_login.OwnedChromium.stop', AsyncMock())
    monkeypatch.setattr('src.auth.container_login.os.geteuid', lambda: 0, raising=False)
    browser = LoginChromium({})
    browser.profile, browser.executable = tmp_path, 'chromium'
    await browser.launch()
    assert '--no-sandbox' in launch.call_args.args
    await browser.close()


class _Attach:
    def __init__(self, protocol):
        self.protocol = protocol

    async def __aenter__(self):
        return self.protocol

    async def __aexit__(self, *_args):
        pass


def pipe_with_forms(forms):
    calls = []

    class Browser:
        async def command(self, method, params=None, timeout=30):
            calls.append(method)
            return {}

    class Page:
        async def command(self, method, params=None):
            calls.append(method)
            if method == "Runtime.evaluate":
                form = forms.pop(0) if forms else {"ready": True, "alternative": False}
                return {"result": {"value": json.dumps(form)}}
            if method == "Storage.getCookies":
                return {"cookies": [{"name": "unique_id", "domain": ".twitch.tv"}]}
            return {}

    return SimpleNamespace(browser=Browser(), attach=lambda _target: _Attach(Page())), calls


@pytest.mark.asyncio
async def test_page_target_polls_until_the_first_page_exists(monkeypatch):
    monkeypatch.setattr("src.auth.container_login.asyncio.sleep", AsyncMock())
    browser = LoginChromium({})
    replies = [
        {"targetInfos": []},
        {"targetInfos": [{"type": "page", "targetId": "late-target",
                          "url": "https://www.twitch.tv/login"}]},
    ]
    browser.pipe = SimpleNamespace(browser=SimpleNamespace(command=AsyncMock(side_effect=replies)))
    assert await browser.page_target() == "late-target"
    assert browser.pipe.browser.command.await_count == 2


@pytest.mark.asyncio
async def test_prepare_rotates_device_cookies_and_reloads_before_the_form_is_usable(monkeypatch):
    monkeypatch.setattr("src.auth.container_login.asyncio.sleep", AsyncMock())
    browser = LoginChromium({})
    browser.pipe, calls = pipe_with_forms([
        {"ready": True, "alternative": True},
        {"ready": True, "alternative": True},
        {"ready": True, "alternative": False},
    ])
    browser.page_target = AsyncMock(return_value="target")
    await browser.prepare()
    assert calls.count("Page.reload") == 2
    assert calls.count("Network.deleteCookies") == 2
    assert calls.count("Network.enable") == 1


@pytest.mark.asyncio
async def test_prepare_reports_an_alternative_form_that_survives_rotation(monkeypatch, caplog):
    monkeypatch.setattr("src.auth.container_login.asyncio.sleep", AsyncMock())
    browser = LoginChromium({})
    browser.pipe, calls = pipe_with_forms([{"ready": True, "alternative": True}] * 5)
    browser.page_target = AsyncMock(return_value="target")
    with caplog.at_level(logging.WARNING, logger="TwitchDrops"):
        await browser.prepare()
    assert calls.count("Page.reload") == 4
    assert "LOGIN_FORM_ALTERNATIVE" in caplog.text


@pytest.mark.asyncio
async def test_pipe_close_failure_terminates_only_a_browser_that_stays_alive():
    browser = LoginChromium({})
    browser.pipe = SimpleNamespace(browser=SimpleNamespace(
        command=AsyncMock(side_effect=SessionError("BROWSER_PROTOCOL"))))

    hung = SimpleNamespace(terminate=Mock(), returncode=None,
                           wait=AsyncMock(side_effect=TimeoutError))
    browser.process = hung
    await browser.close_browser()
    hung.terminate.assert_called_once_with()

    exited = SimpleNamespace(terminate=Mock(), returncode=None, wait=AsyncMock(return_value=0))
    browser.process = exited
    await browser.close_browser()
    exited.terminate.assert_not_called()

    browser.pipe.browser.command = AsyncMock(return_value={})
    await browser.close_browser()
    exited.terminate.assert_not_called()


@pytest.mark.asyncio
async def test_close_browser_never_terminates_a_process_reopened_for_capture():
    browser = LoginChromium({})
    browser.pipe = SimpleNamespace(browser=SimpleNamespace(
        command=AsyncMock(side_effect=SessionError("BROWSER_PROTOCOL"))))
    bound = SimpleNamespace(terminate=Mock(), returncode=None,
                            wait=AsyncMock(side_effect=TimeoutError))
    reopened = SimpleNamespace(terminate=Mock(), returncode=None, wait=AsyncMock(return_value=0))
    browser.process = reopened
    await browser.close_browser(bound)
    bound.terminate.assert_called_once_with()
    reopened.terminate.assert_not_called()


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
        self.prepare = AsyncMock()
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
    browsers[0].prepare.assert_awaited_once()
    assert login.state == "verifying" and not browsers[0].closed
    release.set()
    await state(login, "idle")
    assert browsers[0].closed and desktop.closed
    assert login.desktop is None and login.browser is None
    login.controller.accept.assert_awaited_once()


@pytest.mark.asyncio
async def test_device_rotation_completes_before_the_viewer_shows_the_form(attempt):
    login, desktop, browsers = attempt
    states = []
    create = login.browser_factory

    def factory(environment):
        browser = create(environment)

        async def prepare():
            states.append(login.state)

        browser.prepare = prepare
        return browser

    login.browser_factory = factory
    login.request_login()
    await state(login, "sign_in")
    assert states == ["starting"]
    await login.stop()


@pytest.mark.asyncio
@pytest.mark.parametrize("code,expected", [("BROWSER_PROTOCOL", "sign_in"), ("BROWSER_START", "error")])
async def test_prepare_failure_only_aborts_when_the_browser_is_unusable(attempt, code, expected):
    login, desktop, browsers = attempt
    create = login.browser_factory

    def factory(environment):
        browser = create(environment)
        browser.prepare = AsyncMock(side_effect=SessionError(code))
        return browser

    login.browser_factory = factory
    login.request_login()
    await state(login, expected)
    assert login.error == (None if expected == "sign_in" else "BROWSER_START")
    await login.stop()


@pytest.mark.asyncio
async def test_unexpected_prepare_failure_keeps_the_usable_browser(attempt, caplog):
    login, desktop, browsers = attempt
    create = login.browser_factory

    def factory(environment):
        browser = create(environment)
        browser.prepare = AsyncMock(side_effect=AttributeError("malformed reply"))
        return browser

    login.browser_factory = factory
    with caplog.at_level(logging.WARNING, logger="TwitchDrops"):
        login.request_login()
        await state(login, "sign_in")
    assert "LOGIN_FORM_PREPARE" in caplog.text
    assert login.error is None
    await login.stop()


@pytest.mark.asyncio
async def test_login_form_probe_and_device_rotation_touch_only_twitch_device_cookies():
    browser = LoginChromium({})
    calls = []

    class Protocol:
        async def command(self, method, params=None):
            calls.append((method, params))
            if method == "Runtime.evaluate":
                return {"result": {"value": json.dumps({"ready": True, "alternative": True})}}
            if method == "Storage.getCookies":
                return {"cookies": [
                    {"name": "unique_id", "domain": ".twitch.tv"},
                    {"name": "unique_id_durable", "domain": ".twitch.tv"},
                    {"name": "experiment_overrides", "domain": ".twitch.tv"},
                    {"name": "auth-token", "domain": ".twitch.tv"},
                    {"name": "unique_id", "domain": ".not-twitch.tv"},
                ]}
            return {}

    protocol = Protocol()
    assert await browser.login_form(protocol) == {"ready": True, "alternative": True}
    await browser.rotate_device_id(protocol)
    deleted = [params["name"] for method, params in calls if method == "Network.deleteCookies"]
    assert deleted == ["unique_id", "unique_id_durable", "experiment_overrides"]


@pytest.mark.asyncio
async def test_login_form_probe_treats_a_command_timeout_as_no_form():
    browser = LoginChromium({})

    class Protocol:
        async def command(self, *_args, **_kwargs):
            raise TimeoutError

    assert await browser.login_form(Protocol()) is None


@pytest.mark.asyncio
@pytest.mark.parametrize("capture_failure", [SessionError("CAPTURE_TIMEOUT"), RuntimeError("private capture")])
async def test_cleanup_failure_has_its_own_stage_after_capture_failure(attempt, monkeypatch, capture_failure):
    login, desktop, browsers = attempt

    async def fail_cleanup(browser, *_args):
        browser.closed = True
        raise OSError("private profile path")

    monkeypatch.setattr(Browser, "__aexit__", fail_cleanup)
    login.request_login()
    await state(login, "sign_in")
    browsers[0].capture.side_effect = capture_failure
    await login.finish()
    await state(login, "error")
    diagnostic = login.status()["diagnostic"]
    assert diagnostic["code"] == "BROWSER_FAILED"
    assert diagnostic["stage"] == "browser_cleanup"
    assert diagnostic["reason"] == "unexpected_exception"
    assert "private" not in str(login.status())
    assert desktop.closed and browsers[0].closed


@pytest.mark.asyncio
async def test_capture_failure_is_redacted_and_retry_uses_new_attempt(attempt):
    login, desktop, browsers = attempt
    login.request_login()
    await state(login, "sign_in")
    browsers[0].capture.side_effect = SessionError("LOGIN_MISSING")
    await login.finish()
    await state(login, "error")
    status = login.status()
    assert {key: status[key] for key in ("state", "error", "attempt")} == {
        "state": "error", "error": "LOGIN_MISSING", "attempt": 1,
    }
    assert status["diagnostic"]["code"] == "LOGIN_MISSING"
    assert status["diagnostic"]["stage"] == "browser_capture"
    assert browsers[0].closed and desktop.closed
    await login.retry()
    assert login.status()["diagnostic"] is None
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
async def test_finish_requests_normal_shutdown_before_waiting_for_exit():
    browser = LoginChromium({})
    exited = asyncio.Event()
    steps = []

    async def close_browser(process=None):
        steps.append("close")
        exited.set()

    async def wait():
        await exited.wait()
        steps.append("wait")
        return 0

    browser.process = SimpleNamespace(wait=wait)
    browser.close_browser = close_browser
    await browser.finish()
    assert steps == ["close", "wait"]


@pytest.mark.asyncio
async def test_finish_waits_for_the_process_captured_before_shutdown():
    browser = LoginChromium({})
    closed = SimpleNamespace(wait=AsyncMock(return_value=0))
    reopened = SimpleNamespace(wait=asyncio.Event().wait)
    browser.process = closed

    async def close_browser(process=None):
        # capture() reassigns self.process when it reopens the flushed profile; finish()
        # must keep waiting for the login process it bound before closing the browser.
        assert process is closed
        browser.process = reopened

    browser.close_browser = close_browser
    async with asyncio.timeout(2):
        await browser.finish()
    closed.wait.assert_awaited_once()


@pytest.mark.asyncio
async def test_finish_reports_a_browser_that_does_not_close():
    browser = LoginChromium({})

    async def close_browser(process=None):
        raise SessionError("BROWSER_CLOSE")

    browser.process = SimpleNamespace(wait=asyncio.Event().wait)
    browser.close_browser = close_browser
    with pytest.raises(SessionError, match="BROWSER_CLOSE"):
        await browser.finish()

    async def wait():
        raise TimeoutError

    browser.process = SimpleNamespace(wait=wait)
    browser.close_browser = AsyncMock()
    with pytest.raises(SessionError, match="BROWSER_CLOSE"):
        await browser.finish()


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
