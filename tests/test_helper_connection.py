"""Helper admission and autonomous renewal through the real persisted provider."""

import asyncio
import importlib
import json
import stat
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.auth.imported_session import ImportedSession, SessionTransport
from src.auth.server_seed import SDKCookie, ServerSeed
from src.auth.session_bundle import SessionBundle, SessionError
from src.config import ClientType
from src.config.settings import default_settings
from src.core.client import Twitch
from tests.test_imported_session import bundle_data, catalog


def seed(now=1000, token="oauth-42", integrity="initial-integrity"):
    return ServerSeed(
        SessionBundle.from_dict(bundle_data(now, integrity, token), now=now),
        SDKCookie("private-sdk-cookie", now + 86400),
    )


def test_helper_provider_is_always_available_without_environment_flags(tmp_path, monkeypatch):
    monkeypatch.setattr("src.core.client.DATA_DIR", tmp_path)
    for name in ("TDM_SESSION_IMPORT", "TDM_BROWSER_URL", "TDM_BROWSER_VIEWER_URL", "TDM_BROWSER_DEBUGGER_ADDRESS"):
        monkeypatch.delenv(name, raising=False)
    miner = Twitch(SimpleNamespace(proxy="", allow_helper_connection=True))
    assert isinstance(miner._browser, ImportedSession)


def test_new_installations_allow_the_initial_helper_connection():
    assert default_settings.get("allow_helper_connection") is True


@pytest.fixture
def connection(tmp_path):
    try:
        module = importlib.import_module("src.auth.helper_connection")
    except ModuleNotFoundError:
        pytest.fail("The helper connection controller is not implemented")
    clock = [1000.0]
    transport = SessionTransport()

    async def request(method, url, *, headers, body=None):
        if method == "GET":
            return {"client_id": ClientType.WEB.CLIENT_ID, "user_id": headers["Authorization"].split("-")[-1]}
        return catalog()

    transport.request = AsyncMock(side_effect=request)
    session = ImportedSession(tmp_path / "imported-session.json", transport=transport, clock=lambda: clock[0])
    settings = SimpleNamespace(allow_helper_connection=True)
    issuer = SimpleNamespace(issue=AsyncMock())

    async def issue(previous, *, initial=False):
        clock[0] += 1
        data = previous.bundle.to_dict()
        data.update(captured_at=clock[0], expires_at=previous.bundle.expires_at + 60)
        data["headers"]["client-integrity"] = "new-integrity-" + str(clock[0])
        return ServerSeed(SessionBundle.from_dict(data, now=clock[0]), SDKCookie("rotated-sdk-cookie", previous.cookie.expires_at + 60))

    issuer.issue.side_effect = issue
    controller = module.HelperConnections(session, settings, issuer=issuer, clock=lambda: clock[0])
    return controller, session, settings, clock, issuer


@pytest.mark.asyncio
async def test_success_atomically_saves_seed_closes_gate_and_returns_no_credentials(connection):
    controller, session, settings, clock, issuer = connection
    ticket = controller.connect()
    assert ticket["version"] == 1 and len(ticket["connection"]) == 43
    result = await controller.accept(ticket["connection"], seed().to_dict())
    assert result["success"] is True
    assert result["session"]["state"] == "ready"
    assert result["session"]["user_id"] == 42
    assert result["allow_helper_connection"] is False
    assert settings.allow_helper_connection is False
    assert controller.allowed is False
    assert issuer.issue.await_count == 1
    assert session.seed().cookie.value == "rotated-sdk-cookie"
    assert stat.S_IMODE(session.path.stat().st_mode) == 0o600
    for secret in ("oauth-42", "private-sdk-cookie", "rotated-sdk-cookie", "new-integrity"):
        assert secret not in json.dumps(result)
    with pytest.raises(SessionError, match="HELPER_DISABLED"):
        controller.connect()


@pytest.mark.asyncio
async def test_restart_preserves_closed_permission_seed_and_lost_ack_receipt(connection):
    controller, session, settings, clock, issuer = connection
    ticket = controller.connect()
    result = await controller.accept(ticket["connection"], seed().to_dict())
    restored = ImportedSession(session.path, transport=session._transport, clock=lambda: clock[0])
    cls = type(controller)
    restarted = cls(restored, SimpleNamespace(allow_helper_connection=True), issuer=issuer, clock=lambda: clock[0])
    assert restarted.allowed is False
    assert restored.seed().cookie.value == "rotated-sdk-cookie"
    assert restarted.result(ticket["connection"]) == result
    clock[0] += 601
    with pytest.raises(SessionError, match="CONNECTION"):
        restarted.result(ticket["connection"])


@pytest.mark.asyncio
async def test_inflight_connection_cannot_survive_disabling_then_reenabling(connection):
    controller, session, settings, clock, issuer = connection
    ticket = controller.connect()
    entered, release = asyncio.Event(), asyncio.Event()
    actual_issue = issuer.issue.side_effect

    async def issue(previous, *, initial=False):
        entered.set()
        await release.wait()
        return await actual_issue(previous)

    issuer.issue.side_effect = issue
    pending = asyncio.create_task(controller.accept(ticket["connection"], seed().to_dict()))
    await entered.wait()
    controller.set_allowed(False)
    controller.set_allowed(True)
    release.set()
    with pytest.raises(SessionError, match="CONNECTION|HELPER_DISABLED"):
        await pending
    assert session.status()["generation"] == 0
    assert controller.allowed is True
    assert session.seed() is None


@pytest.mark.asyncio
async def test_only_first_competing_helper_can_commit(connection):
    controller, session, settings, clock, issuer = connection
    first, second = controller.connect(), controller.connect()
    await controller.accept(first["connection"], seed().to_dict())
    with pytest.raises(SessionError, match="HELPER_DISABLED|CONNECTION"):
        await controller.accept(second["connection"], seed().to_dict())
    assert session.status()["generation"] == 1


@pytest.mark.asyncio
async def test_failed_server_validation_or_save_keeps_gate_and_previous_state(connection, monkeypatch):
    controller, session, settings, clock, issuer = connection
    ticket = controller.connect()
    monkeypatch.setattr(session._file, "write", lambda data: (_ for _ in ()).throw(SessionError("SAVE")))
    with pytest.raises(SessionError, match="SAVE"):
        await controller.accept(ticket["connection"], seed().to_dict())
    assert controller.allowed and settings.allow_helper_connection
    assert session.status()["generation"] == 0
    assert session.seed() is None
    assert not session.path.exists()


@pytest.mark.asyncio
async def test_renewal_works_with_admission_closed_and_survives_restart(connection):
    controller, session, settings, clock, issuer = connection
    ticket = controller.connect()
    await controller.accept(ticket["connection"], seed().to_dict())
    first = session.seed()
    result = await controller.renew_once()
    assert result["generation"] == 2
    assert session.seed().bundle.expires_at > first.bundle.expires_at
    assert controller.allowed is False
    restored = ImportedSession(session.path, transport=session._transport, clock=lambda: clock[0])
    restarted = type(controller)(restored, settings, issuer=issuer, clock=lambda: clock[0])
    assert (await restarted.renew_once())["generation"] == 3
    assert restarted.allowed is False


@pytest.mark.asyncio
async def test_expired_ticket_and_renewal_account_switch_are_rejected(connection):
    controller, session, settings, clock, issuer = connection
    ticket = controller.connect()
    clock[0] += 601
    with pytest.raises(SessionError, match="CONNECTION"):
        await controller.accept(ticket["connection"], seed(clock[0]).to_dict())
    assert issuer.issue.await_count == 0
    ticket = controller.connect()
    await controller.accept(ticket["connection"], seed(clock[0]).to_dict())
    before = session.path.read_bytes()
    actual_issue = issuer.issue.side_effect

    async def wrong_account(previous):
        renewed = await actual_issue(previous)
        data = renewed.bundle.to_dict()
        data["headers"]["authorization"] = "OAuth oauth-17"
        return ServerSeed(SessionBundle.from_dict(data, now=clock[0]), renewed.cookie)

    issuer.issue.side_effect = wrong_account
    with pytest.raises(SessionError, match="ACCOUNT_MISMATCH"):
        await controller.renew_once()
    assert session.path.read_bytes() == before


@pytest.mark.asyncio
async def test_reenabled_gate_allows_explicit_replacement_and_invalidates_old_receipt(connection):
    controller, session, settings, clock, issuer = connection
    ticket = controller.connect()
    await controller.accept(ticket["connection"], seed().to_dict())
    controller.set_allowed(True)
    replacement = controller.connect()
    result = await controller.accept(replacement["connection"], seed(clock[0], token="oauth-17", integrity="new-login").to_dict())
    assert result["session"]["user_id"] == 17
    assert controller.allowed is False
    with pytest.raises(SessionError, match="CONNECTION"):
        controller.result(ticket["connection"])


@pytest.mark.asyncio
async def test_stopping_drains_issuance_in_an_active_upload(connection):
    controller, session, settings, clock, issuer = connection
    entered, disposed = asyncio.Event(), asyncio.Event()

    async def issue(previous, **kwargs):
        entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            disposed.set()

    issuer.issue.side_effect = issue
    task = asyncio.create_task(controller.accept(controller.connect()["connection"], seed().to_dict()))
    await entered.wait()
    await controller.stop()
    try:
        assert disposed.is_set()
        assert task.done()
        assert session.seed() is None
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
async def test_status_distinguishes_legacy_import_without_server_seed(connection):
    controller, session, settings, clock, issuer = connection
    assert controller.status()["renewal_available"] is False
    await session.install(seed().bundle.to_dict())
    assert controller.status()["session"]["state"] == "ready"
    assert controller.status()["renewal_available"] is False
    token = controller.connect()["connection"]
    await controller.accept(token, seed().to_dict())
    assert controller.status()["renewal_available"] is True
    assert "private-sdk-cookie" not in json.dumps(controller.status())


@pytest.mark.asyncio
async def test_worker_renews_on_normal_schedule_with_admission_closed(connection):
    controller, session, settings, clock, issuer = connection
    await controller.accept(controller.connect()["connection"], seed().to_dict())
    initial_expiry = session.status()["expires_at"]
    scheduled, proceed, completed = asyncio.Event(), asyncio.Event(), asyncio.Event()
    waits = []

    async def wait(delay=None):
        waits.append(delay)
        if len(waits) == 1:
            scheduled.set()
            await proceed.wait()
            clock[0] = initial_expiry - controller.RENEW_BEFORE
            return False
        completed.set()
        await asyncio.Event().wait()

    controller._wait = wait
    controller.start()
    try:
        await asyncio.wait_for(scheduled.wait(), 1)
        assert waits[0] == initial_expiry - clock[0] - 300
        assert not controller.allowed
        proceed.set()
        await asyncio.wait_for(completed.wait(), 1)
        assert issuer.issue.await_count == 2
        assert session.status()["generation"] == 2
        assert session.status()["expires_at"] > initial_expiry
        assert not controller.allowed
    finally:
        await controller.stop()


@pytest.mark.parametrize("error,needs_login", [(None, False), ("REQUEST", False), ("CATALOG", False), ("SDK_EXPIRED", True), ("ACCOUNT_MISMATCH", True), ("AUTH", True)])
def test_status_distinguishes_retryable_renewal_failures_from_new_login(connection, error, needs_login):
    controller, *_ = connection
    controller._renewal_error = error
    assert controller.status()["renewal_requires_login"] is needs_login
