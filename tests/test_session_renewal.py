"""Scoped renewal credentials and race-safe replacement, without live services."""

import asyncio
import hashlib
import json
from unittest.mock import AsyncMock

import pytest

from src.auth.browser_session import BrowserIdentity
from src.auth.session_bundle import SessionError
from tests.test_imported_session import bundle_data, imported, login


@pytest.mark.asyncio
async def test_pair_rotate_revoke_and_restart_are_account_bound(tmp_path):
    clock = [1000]
    session = imported(tmp_path, clock)
    with pytest.raises(SessionError, match="AUTH"):
        await session.pair()
    await session.install(bundle_data())
    first = await session.pair()
    assert len(first) >= 40 and session.status()["paired"]
    stored = session.path.read_text()
    assert first not in stored
    assert json.loads(stored)["renewal_digest"] == hashlib.sha256(first.encode()).hexdigest()
    restored = imported(tmp_path, clock)
    assert restored.status()["paired"]
    second = await session.pair()
    assert first != second
    clock[0] = 1100
    candidate = bundle_data(1100, "next-integrity")
    before = session._transport.request.await_count
    with pytest.raises(SessionError, match="PAIRING"):
        await session.install(candidate, renewal_token=first)
    assert session._transport.request.await_count == before
    await session.revoke()
    with pytest.raises(SessionError, match="PAIRING"):
        await session.install(candidate, renewal_token=second)
    assert not session.status()["paired"]


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["revoke", "rotate", "newer", "stop", "dashboard"])
async def test_delayed_validation_cannot_overwrite_newer_state_or_revocation(tmp_path, change):
    clock = [1000]
    session = imported(tmp_path, clock)
    await session.install(bundle_data())
    token = await session.pair()
    clock[0] = 1100
    entered, release = asyncio.Event(), asyncio.Event()
    permitted = [True]

    async def validate(bundle, expected):
        assert expected == 42
        entered.set()
        await release.wait()
        return BrowserIdentity(42, bundle.token, "device", bundle.user_agent)

    session._transport.validate = validate
    pending = asyncio.create_task(session.install(
        bundle_data(1100, "candidate"), renewal_token=token, authorized=lambda: permitted[0],
    ))
    await entered.wait()
    if change == "revoke":
        await asyncio.wait_for(session.revoke(), 1)
    elif change == "rotate":
        await asyncio.wait_for(session.pair(), 1)
    elif change == "newer":
        session._transport.validate = AsyncMock(return_value=BrowserIdentity(42, "new", "device", "Chrome"))
        clock[0] = 1200
        await session.install(bundle_data(1200, "newer"))
    elif change == "stop":
        session.request_stop()
    else:
        permitted[0] = False
    saved = session.path.read_bytes()
    release.set()
    with pytest.raises(SessionError):
        await pending
    assert session.path.read_bytes() == saved
    assert session._bundle.headers["client-integrity"] != "candidate"


@pytest.mark.asyncio
async def test_renewal_validates_identity_catalog_and_updates_waiting_consumer(tmp_path):
    clock = [1000]
    session = imported(tmp_path, clock)
    await session.install(bundle_data())
    token = await session.pair()
    clock[0] = 4601
    waiting = asyncio.create_task(session.authenticate(login()))
    await asyncio.sleep(0)
    session._transport.validate = AsyncMock(return_value=BrowserIdentity(42, "renewed", "device", "Chrome"))
    status = await session.install(bundle_data(4601, "renewed-integrity", "renewed"), renewal_token=token)
    assert status["generation"] == 2 and status["paired"]
    assert (await waiting).token == "renewed"
    assert token not in session.path.read_text()


def test_renewal_api_bearer_scope_and_dashboard_guards(api):
    from tests.test_session_api import authorize

    browser, session = api
    authorize(browser)
    assert browser.post('/api/session/import', json=bundle_data()).status_code == 200
    result = browser.post('/api/session/pair')
    assert result.status_code == 200
    connection = result.json()
    assert connection['endpoint'] == 'http://testserver/api/session/renew'
    token = connection['credential']
    assert token not in browser.get('/api/session').text
    browser.cookies.clear()
    bearer = {'Authorization': 'Bearer ' + token}
    assert browser.get('/api/settings', headers=bearer).status_code == 401
    assert browser.post('/api/session/import', json=bundle_data(), headers=bearer).status_code == 401
    assert browser.post('/api/session/pair', headers=bearer).status_code == 401
    assert browser.post('/api/session/renew', json=bundle_data(), headers={'Authorization': 'Bearer wrong'}).status_code == 401
    assert browser.post('/api/session/renew', json=bundle_data(), headers={**bearer, 'Origin': 'https://evil.test'}).status_code == 403
    assert browser.post('/api/session/renew', json=bundle_data(), headers={**bearer, 'X-TDM-Request': '0'}).status_code == 403
    session._clock = lambda: 1100
    from tests.test_imported_session import transport

    session._transport = transport()
    response = browser.post('/api/session/renew', json=bundle_data(1100, 'fresh'), headers=bearer)
    assert response.status_code == 200
    assert response.json()['session']['generation'] == 2
    assert token not in response.text
    assert 'test-token' not in response.text


# Use the production-ASGI fixture for renewal boundary tests as well.
from tests.test_session_api import api  # noqa: E402,F401


@pytest.mark.asyncio
@pytest.mark.parametrize('action', ['pair', 'revoke'])
async def test_management_rechecks_dashboard_authorization_after_lock_wait(tmp_path, action):
    session = imported(tmp_path, [1000])
    await session.install(bundle_data())
    await session.pair()
    original = session.path.read_bytes()
    allowed = [True]
    await session._lock.acquire()
    task = asyncio.create_task(getattr(session, action)(authorized=lambda: allowed[0]))
    await asyncio.sleep(0)
    allowed[0] = False
    session._lock.release()
    with pytest.raises(SessionError, match='AUTH'):
        await task
    assert session.path.read_bytes() == original
