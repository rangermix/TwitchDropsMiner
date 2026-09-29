"""Every helper code has safe translated guidance and honest recovery advice."""

import json
import sys
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from src.auth import login_helper
from src.auth.helper_errors import HelperDiagnostics
from src.auth.session_bundle import SessionError
from src.config import LANG_PATH
from src.i18n import _
from src.i18n.translator import HelperErrors, HelperMessages
from tests.test_login_helper import instance, seed


@pytest.fixture(autouse=True)
def restore_language():
    original = _.current_language
    _.set_language("English")
    yield
    _.set_language(original)


def test_every_diagnostic_has_nonempty_translated_guidance_and_matching_schema():
    required = set(HelperDiagnostics.KEYS.values())
    assert required == set(HelperErrors.__annotations__)
    for path in LANG_PATH.glob("*.json"):
        helper = json.loads(path.read_text(encoding="utf-8"))["helper"]
        assert set(helper) == set(HelperMessages.__annotations__), path.name
        assert set(helper["errors"]) == required, path.name
        assert all(isinstance(value, str) and value.strip() for value in helper["errors"].values())


@pytest.mark.parametrize("code", list(HelperDiagnostics.KEYS))
def test_cli_prints_code_and_explanation(code, capsys):
    login_helper.LoginHelperCLI.report_error(code)
    output = capsys.readouterr()
    assert output.out == ""
    assert "SESSION_" + code in output.err
    assert _.t["helper"]["errors"][HelperDiagnostics.KEYS[code]] in output.err
    assert len(output.err.splitlines()) == 2


def test_unknown_error_text_is_never_printed(capsys):
    login_helper.LoginHelperCLI.report_error("private-cookie\nOAuth secret")
    output = capsys.readouterr().err
    assert "SESSION_HELPER_FAILED" in output
    assert "private-cookie" not in output and "OAuth secret" not in output


@pytest.mark.parametrize("code", ["HELPER_RESULT_UNKNOWN", "HELPER_BROWSER_CLEANUP", "HELPER_PROFILE_CLEANUP", "HELPER_FAILED"])
def test_post_upload_failures_tell_users_to_check_tdm_before_retry(code):
    _safe_code, text = HelperDiagnostics.describe(code)
    assert "TDM" in text and "before retrying" in text


def test_browser_failure_distinguishes_local_desktop_from_miner_host():
    assert "desktop" in HelperDiagnostics.describe("HELPER_BROWSER")[1]
    assert "miner host" in HelperDiagnostics.describe("HELPER_SERVER_BROWSER")[1]
    assert "PATH" in HelperDiagnostics.describe("HELPER_SERVER_BROWSER")[1]


def test_login_timeout_guidance_requires_closing_only_the_helper_browser():
    guidance = HelperDiagnostics.describe("HELPER_LOGIN_TIMEOUT")[1]
    assert "close all helper browser windows" in guidance
    assert "macOS: quit that instance" in guidance
    assert "leave the helper open" in guidance


def test_diagnostics_are_translated_with_cli_language(monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["login_helper.py", "--language", "简体中文", "--tdm", "invalid"])
    with pytest.raises(SystemExit) as result:
        login_helper.LoginHelperCLI.main()
    assert result.value.code == 1
    output = capsys.readouterr().err
    assert "SESSION_HELPER_DESTINATION" in output
    assert "地址无效" in output


@pytest.mark.parametrize("args", [
    ["--browser", "chrome", "--firefox", "firefox"],
    ["--browser", "firefox", "--chrome", "chrome"],
    ["--chrome", "chrome", "--firefox", "firefox"],
    ["--browser", "chromium", "--chrome", "chrome"],
    ["--browser", "chromium", "--firefox", "firefox"],
    ["--browser", "chrome", "--chromium", "chromium"],
    ["--browser", "firefox", "--chromium", "chromium"],
    ["--chromium", "chromium", "--firefox", "firefox"],
    ["--chrome", "chrome", "--chromium", "chromium"],
])
def test_cli_rejects_conflicting_browser_options_before_connect(monkeypatch, args):
    connect = AsyncMock()
    monkeypatch.setattr(login_helper.HelperHTTP, "connect", connect)
    monkeypatch.setattr(sys, "argv", ["login_helper.py", *args])
    with pytest.raises(SystemExit) as result:
        login_helper.LoginHelperCLI.main()
    assert result.value.code == 2
    connect.assert_not_called()


@pytest.mark.parametrize("args,expected,paths", [
    ([], "auto", {}), (["--browser", "firefox"], "firefox", {}),
    (["--browser", "chromium"], "chromium", {}),
    (["--firefox", "custom-firefox"], "auto", {"firefox": Path("custom-firefox")}),
    (["--chromium", "custom-chromium"], "auto", {"chromium": Path("custom-chromium")}),
    (["--browser", "chromium", "--chromium", "custom"], "chromium", {"chromium": Path("custom")}),
])
def test_cli_passes_browser_choice_to_admitted_factory(monkeypatch, args, expected, paths):
    selected = []
    monkeypatch.setattr(sys, "argv", ["login_helper.py", "--tdm", "http://localhost:8080", *args])
    monkeypatch.setattr(login_helper.BrowserSelection, "create",
                        lambda choice, **paths: selected.append((choice, paths)))

    async def run(helper):
        helper.browser_factory()

    monkeypatch.setattr(login_helper.LoginHelperCLI, "run_cancellable", run)
    with pytest.raises(SystemExit) as result:
        login_helper.LoginHelperCLI.main()
    assert result.value.code == 0
    assert selected == [(expected, {"chrome": None, "chromium": None, "firefox": None, **paths})]


@pytest.mark.asyncio
@pytest.mark.parametrize("status,detail,code", [
    (401, "session_connection", "HELPER_EXPIRED"),
    (429, "session_busy", "HELPER_BUSY"),
    (429, "arbitrary-private-text", "HELPER_REJECTED"),
])
async def test_actual_server_rejections_map_only_fixed_codes(status, detail, code):
    async with (instance(connect_status=status, connect={"detail": detail}) as (address, _requests),
                login_helper.HelperHTTP(login_helper.HelperDestination(address)) as client):
        with pytest.raises(SessionError) as error:
            await client.connect()
        assert error.value.code == code
        assert detail not in str(error.value)


@pytest.mark.asyncio
async def test_misleading_5xx_detail_still_reconciles_without_reupload():
    async with (instance(complete_status=503, complete={"detail": "session_connection"}) as (address, requests),
                login_helper.HelperHTTP(login_helper.HelperDestination(address), clock=lambda: 1000) as client):
        assert await client.send(await client.connect(), seed()) == 123
    assert [row[0] for row in requests] == ["/api/helper/connect", "/api/helper/session", "/api/helper/result"]
