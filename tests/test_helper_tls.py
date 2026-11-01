"""The helper's TLS exception is explicit, local to TDM, and visibly warned."""

import sys
from unittest.mock import AsyncMock, Mock

import pytest

from src.auth import login_helper
from src.auth.session_bundle import SessionError
from src.i18n import _
from tests.test_login_helper import CLOCK, FakeBrowser, instance, loopback_tls, seed


@pytest.fixture
def server_tls():
    return loopback_tls()


@pytest.mark.asyncio
async def test_self_signed_tls_requires_opt_in_and_does_not_change_later_clients(server_tls):
    async with instance(ssl_context=server_tls, lose_ack=True) as (address, requests):
        destination = login_helper.HelperDestination(address)
        async with login_helper.HelperHTTP(destination, clock=lambda: CLOCK) as client:
            with pytest.raises(SessionError) as error:
                await client.connect()
            assert error.value.code == "HELPER_NETWORK"
            assert error.value.diagnostic.reason == "tls"
        assert requests == []

        async with login_helper.HelperHTTP(destination, clock=lambda: CLOCK, insecure=True) as client:
            assert await client.send(await client.connect(), seed()) == 123
        assert [row[0] for row in requests] == [
            "/api/helper/connect", "/api/helper/session", "/api/helper/result",
        ]

        async with login_helper.HelperHTTP(destination, clock=lambda: CLOCK) as client:
            with pytest.raises(SessionError) as error:
                await client.connect()
            assert error.value.diagnostic.reason == "tls"
        assert len(requests) == 3


@pytest.mark.asyncio
@pytest.mark.parametrize("insecure", [False, True])
async def test_native_helper_warns_before_connecting_and_forwards_tls_choice(server_tls, insecure):
    browser = FakeBrowser()
    factory = Mock(return_value=browser)
    exporter = Mock(capture_seed=AsyncMock(return_value=seed()))
    output = []
    async with instance(ssl_context=server_tls) as (address, requests):
        helper = login_helper.NativeLoginHelper(address, insecure=insecure, clock=lambda: CLOCK,
            browser_factory=factory, exporter_factory=lambda *_args, **_kwargs: exporter,
            report=output.append)
        if insecure:
            await helper.run()
            assert browser.closed
            assert len(requests) == 2
            assert output == ["insecure_warning", "connecting", "login", "capturing", "sending", "success"]
        else:
            with pytest.raises(SessionError) as error:
                await helper.run()
            assert error.value.diagnostic.reason == "tls"
            assert output == ["connecting"]
            factory.assert_not_called()
            assert requests == []


@pytest.mark.parametrize("insecure", [False, True])
@pytest.mark.parametrize("language", ["English", "简体中文"])
def test_cli_tls_flag_and_translated_stderr_warning_before_network(monkeypatch, capsys, insecure, language):
    original_language = _.current_language
    monkeypatch.setattr(sys, "argv", ["login_helper.py", "--tdm", "https://127.0.0.1:8443",
        "--language", language, "--no-pause", *(["--insecure"] if insecure else [])])
    observed = []

    async def connect(client):
        observed.append((client.insecure, capsys.readouterr()))
        raise SessionError("HELPER_DISABLED")

    monkeypatch.setattr(login_helper.HelperHTTP, "connect", connect)
    try:
        with pytest.raises(SystemExit) as result:
            login_helper.LoginHelperCLI.main()
        assert result.value.code == 1
        assert len(observed) == 1
        choice, output = observed[0]
        assert choice is insecure
        assert output.err == (_.t["helper"]["insecure_warning"] + "\n" if insecure else "")
        assert _.t["helper"]["connecting"] in output.out
        assert "SESSION_HELPER_DISABLED" in capsys.readouterr().err
    finally:
        _.set_language(original_language)


def test_cli_help_describes_insecure_scope_and_default(monkeypatch, capsys):
    original_language = _.current_language
    monkeypatch.setattr(sys, "argv", ["login_helper.py", "--language", "English", "--help"])
    try:
        with pytest.raises(SystemExit) as result:
            login_helper.LoginHelperCLI.main()
        assert result.value.code == 0
        output = capsys.readouterr().out
        assert "--insecure" in output
        assert " ".join(_.t["helper"]["insecure_help"].split()) in " ".join(output.split())
    finally:
        _.set_language(original_language)
