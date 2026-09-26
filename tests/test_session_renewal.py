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


def test_legacy_renewal_bearer_cannot_bypass_new_gate_or_dashboard(api):
    from tests.test_helper_api import enable_dashboard_auth
    from tests.test_helper_connection import seed

    browser, helper, settings = api
    session = helper.session
    asyncio.run(session.install(bundle_data()))
    old_credential = asyncio.run(session.pair())
    helper.set_allowed(False)
    enable_dashboard_auth(browser)
    bearer = {"Authorization": "Bearer " + old_credential}
    assert browser.get("/api/settings", headers=bearer).status_code == 401
    assert browser.get("/api/session", headers=bearer).status_code == 401
    assert browser.post("/api/helper/connect", json={}).status_code == 403
    response = browser.post("/api/helper/session", json=seed().to_dict(), headers=bearer)
    assert response.status_code == 403
    assert old_credential not in response.text
    assert session.status()["generation"] == 1


# Use the production-ASGI fixture for renewal boundary tests as well.
from tests.test_helper_api import api  # noqa: E402,F401


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
