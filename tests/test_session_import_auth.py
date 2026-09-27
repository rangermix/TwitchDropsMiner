"""Imported authentication integrates without replacing valid Android sessions."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.auth.browser_session import BrowserIdentity
from src.auth.imported_session import ImportedSession
from src.auth.session_bundle import SessionError
from src.core.client import Twitch
from src.web.managers.login import LoginFormManager


def client(monkeypatch, tmp_path):
    monkeypatch.setattr("src.core.client.DATA_DIR", tmp_path)
    monkeypatch.delenv("TDM_SESSION_IMPORT", raising=False)
    for name in ("TDM_BROWSER_URL", "TDM_BROWSER_VIEWER_URL", "TDM_BROWSER_DEBUGGER_ADDRESS"):
        monkeypatch.delenv(name, raising=False)
    miner = Twitch(SimpleNamespace(proxy=""))
    miner.gui = MagicMock()
    miner.gui.login.import_pending = AsyncMock()
    return miner


def test_helper_provider_is_default_and_obsolete_env_cannot_enable_alternative(monkeypatch, tmp_path):
    miner = client(monkeypatch, tmp_path)
    assert isinstance(miner._browser, ImportedSession)
    monkeypatch.setenv("TDM_BROWSER_URL", "http://browser:4444")
    monkeypatch.setenv("TDM_BROWSER_VIEWER_URL", "http://localhost:7900")
    monkeypatch.setenv("TDM_SESSION_IMPORT", "0")
    assert isinstance(Twitch(SimpleNamespace(proxy=""))._browser, ImportedSession)


@pytest.mark.asyncio
async def test_import_prompt_round_trip_has_no_secrets():
    form = LoginFormManager(MagicMock(emit=AsyncMock()), MagicMock())
    await form.import_pending(True)
    assert form.get_status()["import_pending"] is True
    await form.import_pending(False)
    assert not form.get_status().get("import_pending")


@pytest.mark.asyncio
async def test_renewed_oauth_updates_auth_and_requests_websocket_reauthentication(monkeypatch, tmp_path):
    miner = client(monkeypatch, tmp_path)
    browser = miner._browser
    assert isinstance(browser, ImportedSession)
    browser.authenticate = AsyncMock(return_value=BrowserIdentity(42, "old-token", "old-device", "Chrome"))
    await miner._auth_state._browser_login()
    ws = MagicMock()
    miner.websocket.websockets = [ws]
    new = BrowserIdentity(42, "new-token", "new-device", "Chrome")
    browser.authenticate.return_value = new
    auth = await miner.get_auth()
    assert auth.access_token == "new-token" and auth.device_id == "new-device"
    ws.request_reconnect.assert_called_once()
    await miner.get_auth()
    ws.request_reconnect.assert_called_once()


def test_existing_account_binding_is_available_before_import(monkeypatch, tmp_path):
    miner = client(monkeypatch, tmp_path)
    miner._auth_state.user_id = 17
    assert miner._browser.bound_user_id() == 17


@pytest.mark.asyncio
async def test_same_oauth_renewal_restores_login_status_after_expiry(monkeypatch, tmp_path):
    miner = client(monkeypatch, tmp_path)
    form = LoginFormManager(MagicMock(emit=AsyncMock()), MagicMock())
    miner.gui.login = form
    identity = BrowserIdentity(42, "token", "device", "Chrome")
    miner._browser.authenticate = AsyncMock(return_value=identity)
    await miner._auth_state._browser_login()
    await form.import_pending(True)
    miner._auth_state.accept_imported_identity(identity)
    await form.import_pending(False)
    assert form.get_status()["user_id"] == 42


@pytest.mark.asyncio
async def test_expected_fallback_account_binds_before_waiting_for_upload(monkeypatch, tmp_path):
    import asyncio

    from src.exceptions import ExitRequest
    from tests.test_imported_session import bundle_data, transport

    miner = client(monkeypatch, tmp_path)
    browser = miner._browser
    browser._clock = lambda: 1000
    browser._transport = transport("42")
    task = asyncio.create_task(miner._auth_state._browser_login(expected_user_id=17))
    await asyncio.sleep(0)
    with pytest.raises(SessionError, match="ACCOUNT_MISMATCH"):
        await browser.install(bundle_data())
    assert not browser.path.exists()
    browser.request_stop()
    with pytest.raises(ExitRequest):
        await asyncio.wait_for(task, 1)


def test_imported_transport_tracks_dashboard_proxy_changes(monkeypatch, tmp_path):
    miner = client(monkeypatch, tmp_path)
    assert miner._browser._transport.proxy() is None
    miner.settings.proxy = "http://proxy.test:8080"
    assert miner._browser._transport.proxy() == "http://proxy.test:8080"
    miner.settings.proxy = ""
    assert miner._browser._transport.proxy() is None
