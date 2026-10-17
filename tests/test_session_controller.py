"""Verified installation, renewal, migration and logout without live credentials."""

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.auth.imported_session import ImportedSession, SessionTransport
from src.auth.server_seed import SDKCookie, ServerSeed
from src.auth.session_bundle import SessionBundle, SessionError
from src.auth.session_controller import SessionController
from src.config import ClientType
from tests.test_imported_session import bundle_data, catalog


def seed(now=1000, token="oauth-42", integrity="initial-integrity"):
    return ServerSeed(SessionBundle.from_dict(bundle_data(now, integrity, token), now=now),
                      SDKCookie("private-sdk-cookie", now + 86400))


@pytest.fixture
def controller(tmp_path):
    clock = [1000.0]
    transport = SessionTransport()

    async def request(method, url, *, headers, body=None):
        if method == "GET":
            return {"client_id": ClientType.WEB.CLIENT_ID, "user_id": headers["Authorization"].split("-")[-1]}
        return catalog()

    transport.request = AsyncMock(side_effect=request)
    session = ImportedSession(tmp_path / "imported-session.json", transport=transport, clock=lambda: clock[0])

    async def issue(previous, *, initial=False):
        clock[0] += 1
        data = previous.bundle.to_dict()
        data.update(captured_at=clock[0], expires_at=previous.bundle.expires_at + 60)
        data["headers"]["client-integrity"] = "new-integrity-" + str(clock[0])
        return ServerSeed(SessionBundle.from_dict(data, now=clock[0]), SDKCookie("rotated-sdk-cookie", previous.cookie.expires_at + 60))

    issuer = SimpleNamespace(issue=AsyncMock(side_effect=issue))
    return SessionController(session, issuer=issuer, clock=lambda: clock[0]), session, clock, issuer


@pytest.mark.asyncio
async def test_validates_original_and_issued_context_then_persists_redacted_status(controller):
    control, session, clock, issuer = controller
    result = await control.accept(seed(), authorized=lambda: True)
    assert result["state"] == "ready" and result["user_id"] == 42
    assert session._transport.request.await_count == 4
    issuer.issue.assert_awaited_once_with(seed(), initial=True)
    assert session.seed().cookie.value == "rotated-sdk-cookie"
    stored = json.loads(session.path.read_text())
    assert stored["version"] == 3 and stored["logged_out"] is False
    assert "helper_allowed" not in stored and "helper_receipt" not in stored
    for secret in ("oauth-42", "private-sdk-cookie", "rotated-sdk-cookie", "new-integrity"):
        assert secret not in json.dumps(control.status())


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["identity", "catalog", "issuer", "storage"])
async def test_failure_preserves_previous_session(controller, monkeypatch, failure):
    control, session, clock, issuer = controller
    await control.accept(seed(), authorized=lambda: True)
    before = session.path.read_bytes()
    if failure == "identity":
        session._transport.request.side_effect = [{"client_id": "wrong", "user_id": "42"}]
    elif failure == "catalog":
        session._transport.request.side_effect = [{"client_id": ClientType.WEB.CLIENT_ID, "user_id": "42"}, {}]
    elif failure == "issuer":
        issuer.issue.side_effect = SessionError("SDK_ISSUANCE")
    else:
        monkeypatch.setattr(session._file, "write", lambda state: (_ for _ in ()).throw(SessionError("FILE")))
    with pytest.raises(SessionError):
        await control.accept(seed(1100), authorized=lambda: True)
    assert session.path.read_bytes() == before
    assert session.status()["generation"] == 1


@pytest.mark.asyncio
async def test_independent_issued_account_must_match_capture(controller):
    control, session, clock, issuer = controller
    issuer.issue.return_value = seed(1001, token="oauth-17")
    issuer.issue.side_effect = None
    with pytest.raises(SessionError, match="ACCOUNT_MISMATCH"):
        await control.accept(seed(), authorized=lambda: True)
    assert not session.path.exists()


@pytest.mark.asyncio
async def test_logout_clears_credentials_and_survives_legacy_cookie_deletion_failure(controller):
    control, session, clock, issuer = controller
    await control.accept(seed(), authorized=lambda: True)
    with pytest.raises(OSError):
        await control.logout(AsyncMock(side_effect=OSError("private path")))
    restored = ImportedSession(session.path, transport=session._transport, clock=lambda: clock[0])
    assert restored.logged_out and restored.seed() is None
    assert restored.status()["user_id"] is None and restored.status()["generation"] == 0
    assert "oauth-42" not in session.path.read_text()
    await control.accept(seed(1100), authorized=lambda: True)
    assert session.logged_out is False


@pytest.mark.asyncio
async def test_logout_invalidates_and_drains_cancellation_resistant_login(controller):
    control, session, clock, issuer = controller
    entered, cleanup, release = asyncio.Event(), asyncio.Event(), asyncio.Event()
    actual = issuer.issue.side_effect

    async def issue(previous, *, initial=False):
        entered.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            cleanup.set()
            await release.wait()
            return await actual(previous)

    issuer.issue.side_effect = issue
    accepting = asyncio.create_task(control.accept(seed(), authorized=lambda: True))
    await entered.wait()
    logout = asyncio.create_task(control.logout(AsyncMock()))
    await cleanup.wait()
    assert not logout.done()
    release.set()
    await logout
    with pytest.raises(SessionError, match="STALE"):
        await accepting
    assert session.logged_out and session.seed() is None


@pytest.mark.asyncio
async def test_stop_drains_inflight_renewal(controller):
    control, session, clock, issuer = controller
    await control.accept(seed(), authorized=lambda: True)
    entered, cleaned = asyncio.Event(), asyncio.Event()

    async def issue(previous):
        entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            cleaned.set()

    issuer.issue.side_effect = issue
    task = asyncio.create_task(control.renew_once())
    await entered.wait()
    await control.stop()
    assert task.cancelled() and cleaned.is_set()
    assert session.status()["generation"] == 1


@pytest.mark.asyncio
async def test_renewal_rotates_same_account_context(controller):
    control, session, clock, issuer = controller
    await control.accept(seed(), authorized=lambda: True)
    initial = session.seed()
    await control.renew_once()
    assert session.status()["generation"] == 2
    assert session.seed().bundle.captured_at > initial.bundle.captured_at
    assert session.seed().bundle.expires_at > initial.bundle.expires_at


@pytest.mark.asyncio
async def test_legacy_v2_migration_preserves_login_and_ignores_retired_gate(controller):
    control, session, clock, issuer = controller
    await control.accept(seed(), authorized=lambda: True)
    saved = json.loads(session.path.read_text())
    saved.update(version=2, helper_allowed=False, helper_epoch=4, helper_receipt={})
    saved.pop("logged_out")
    session._file.write(saved)
    restored = ImportedSession(session.path, transport=session._transport, clock=lambda: clock[0])
    identity = await restored.authenticate(SimpleNamespace(import_pending=AsyncMock()))
    assert identity.user_id == 42 and restored.seed().cookie == session.seed().cookie
    await restored.logout()
    assert json.loads(session.path.read_text())["version"] == 3
