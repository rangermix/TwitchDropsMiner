"""Helper login replaces authenticated runtime state and survives process restart."""

import asyncio
import time
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import aiohttp
import pytest

from src.auth.browser_session import BrowserIdentity
from src.auth.session_bundle import SessionBundle
from src.config import ClientType, State
from src.core.client import Twitch
from tests.test_helper_connection import seed
from tests.test_imported_session import transport


def miner(tmp_path, monkeypatch):
    monkeypatch.setattr("src.core.client.DATA_DIR", tmp_path)
    client = Twitch(SimpleNamespace(proxy="", allow_helper_connection=True))
    client.gui = MagicMock()
    client.gui.login.import_pending = AsyncMock()
    return client


@pytest.mark.asyncio
async def test_fresh_login_waits_for_helper_without_device_authorization(tmp_path, monkeypatch):
    client = miner(tmp_path, monkeypatch)
    client._browser.authenticate = AsyncMock(return_value=BrowserIdentity(42, "helper-token", "device", "Chrome"))
    client.get_session = AsyncMock(return_value=SimpleNamespace(cookie_jar=aiohttp.CookieJar()))
    client.request = MagicMock(side_effect=AssertionError("Fresh login must not request a device code"))
    auth = await client.get_auth()
    assert auth.user_id == 42 and auth.browser_active
    assert not hasattr(auth, "_oauth_login")
    client.request.assert_not_called()


@pytest.mark.asyncio
async def test_saved_helper_session_takes_precedence_over_preserved_android_cookies(tmp_path, monkeypatch):
    client = miner(tmp_path, monkeypatch)
    now = time.time()
    client._browser._transport = transport()
    source = seed(now)
    await client._browser.install(source.bundle.to_dict(), sdk_cookie=source.cookie, replace=True)
    legacy = tmp_path / "cookies.jar"
    legacy.write_bytes(b"untouched legacy Android cookies")
    restarted = miner(tmp_path, monkeypatch)
    restarted._browser._transport = transport()
    restarted.get_session = AsyncMock(side_effect=AssertionError("Do not load superseded Android cookies"))
    auth = await restarted.get_auth()
    assert auth.user_id == 42 and auth.browser_active
    assert auth.access_token == source.bundle.token
    assert legacy.read_bytes() == b"untouched legacy Android cookies"


@pytest.mark.asyncio
async def test_valid_saved_android_session_still_runs_without_new_login(tmp_path, monkeypatch):
    client = miner(tmp_path, monkeypatch)
    jar = aiohttp.CookieJar()
    jar.update_cookies({"auth-token": "android-token", "unique_id": "old-device"}, ClientType.ANDROID_APP.CLIENT_URL)
    client.get_session = AsyncMock(return_value=SimpleNamespace(cookie_jar=jar))

    @asynccontextmanager
    async def request(method, url, **kwargs):
        assert method == "GET" and str(url).endswith("/validate")
        yield SimpleNamespace(status=200, json=AsyncMock(return_value={"client_id": ClientType.ANDROID_APP.CLIENT_ID, "user_id": "17"}))

    client.request = request
    monkeypatch.setattr("src.auth.auth_state.COOKIES_PATH", tmp_path / "cookies.jar")
    auth = await client.get_auth()
    assert not auth.browser_active
    assert auth.user_id == 17 and auth.access_token == "android-token"


@pytest.mark.asyncio
async def test_replacement_drains_old_authenticated_work_before_resuming(tmp_path, monkeypatch):
    client = miner(tmp_path, monkeypatch)
    assert hasattr(client, "authentication_change")
    entered, resumed = asyncio.Event(), asyncio.Event()
    calls = 0

    async def run():
        nonlocal calls
        calls += 1
        (entered if calls == 1 else resumed).set()
        await asyncio.Event().wait()

    client._run = run
    client._inventory_service.clear_cached_state = MagicMock()
    client.websocket.stop = AsyncMock()
    client.helper.start = MagicMock()
    client.helper.stop = AsyncMock()
    task = asyncio.create_task(client.run())
    await entered.wait()
    client._watching_task = asyncio.create_task(asyncio.Event().wait())
    client._mnt_task = asyncio.create_task(asyncio.Event().wait())
    old_run, old_watch, old_maintenance = client._run_task, client._watching_task, client._mnt_task
    client._auth_state.user_id = 17
    client._auth_state.access_token = "old-token"
    async with client.authentication_change():
        assert old_run.done() and old_watch.done() and old_maintenance.done()
        assert calls == 1
        assert not client._resume_mining.is_set()
    await asyncio.wait_for(resumed.wait(), 1)
    assert not hasattr(client._auth_state, "user_id")
    client.websocket.stop.assert_awaited_with(clear_topics=True)
    client._inventory_service.clear_cached_state.assert_called_once()
    client._state = State.EXIT
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    client.helper.stop.assert_awaited()
