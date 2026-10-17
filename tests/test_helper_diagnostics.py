"""Offline helper diagnostics distinguish failure phases without claiming root causes."""

import asyncio
import json
from unittest.mock import AsyncMock

import pytest

from src.auth.session_bundle import SessionError
from src.config import ClientType
from tests.test_desktop_helper_api import connect_helper
from tests.test_desktop_helper_api import protocol as protocol
from tests.test_imported_session import catalog
from tests.test_session_controller import controller as controller
from tests.test_session_controller import seed


def upload(client, ticket):
    return client.post("/api/helper/session", json=seed().to_dict(),
                       headers={"Authorization": "Bearer " + ticket})


def assert_failure(client, response, *, code, stage, reason=None, operation=None):
    assert response.json()["detail"] == "session_" + code.lower()
    diagnostic = response.json()["diagnostic"]
    assert diagnostic["code"] == code and diagnostic["stage"] == stage
    assert diagnostic["http_status"] == response.status_code
    status = client.get("/api/session").json()
    assert status["helper"]["error"] == code
    assert status["helper"]["diagnostic"]["code"] == code
    assert status["helper"]["diagnostic"]["stage"] == stage
    if reason is not None:
        assert diagnostic["reason"] == reason
        assert status["helper"]["diagnostic"]["reason"] == reason
    if operation is not None:
        assert diagnostic["operation"] == operation
    return diagnostic


@pytest.mark.parametrize("phase", ["captured_validation", "issued_validation"])
@pytest.mark.parametrize("message,reason", [
    ("failed integrity check", "integrity"),
    ("PersistedQueryNotFound", "persisted_query_not_found"),
    ("service error", "service"),
    ("private remote message and OAuth secret", "unknown"),
])
def test_issue148_catalog_failure_identifies_phase_and_fixed_reason(protocol, phase, message, reason, caplog):
    client, _helper, _auth, issuer, session = protocol
    identity = {"client_id": ClientType.WEB.CLIENT_ID, "user_id": "42"}
    failed = catalog()
    failed[1] = {"errors": [{"message": message}], "data": {"currentUser": None}}
    replies = [identity]
    if phase == "issued_validation":
        replies.extend([catalog(), identity])
    replies.append(failed)
    session._transport.request.side_effect = replies
    response = upload(client, connect_helper(client))
    assert response.status_code == 400
    assert_failure(client, response, code="CATALOG", stage=phase, reason=reason, operation="Campaigns")
    assert issuer.issue.await_count == (phase == "issued_validation")
    assert session.status()["state"] == "waiting" and session.seed() is None
    assert not session.path.exists()
    logged = [json.loads(record.getMessage().split("SESSION_DIAGNOSTIC ", 1)[1])
              for record in caplog.records if record.getMessage().startswith("SESSION_DIAGNOSTIC ")]
    assert logged and logged[-1]["stage"] == phase
    assert logged[-1]["reason"] == reason
    if reason == "unknown":
        assert message not in response.text + json.dumps(client.get("/api/session").json()) + caplog.text


def test_inventory_rejection_is_distinct_from_campaigns_rejection(protocol):
    client, _helper, _auth, issuer, session = protocol
    failed = catalog()
    failed[0] = {"errors": [{"message": "failed integrity check"}]}
    session._transport.request.side_effect = [
        {"client_id": ClientType.WEB.CLIENT_ID, "user_id": "42"}, failed,
    ]
    response = upload(client, connect_helper(client))
    assert_failure(client, response, code="CATALOG", stage="captured_validation",
                   reason="integrity", operation="Inventory")
    issuer.issue.assert_not_awaited()


def test_server_issuance_failure_does_not_install_valid_original_session(protocol):
    client, _helper, _auth, issuer, session = protocol
    issuer.issue.side_effect = SessionError("SDK_ISSUANCE", reason="http_error", upstream_status=403)
    response = upload(client, connect_helper(client))
    assert response.status_code == 400
    diagnostic = assert_failure(client, response, code="SDK_ISSUANCE", stage="server_issuance",
                                reason="http_error")
    assert diagnostic["upstream_status"] == 403
    assert session._transport.request.await_count == 2 and session.seed() is None
    assert not session.path.exists()


def test_identity_mismatch_is_rejected_after_server_issuance(protocol):
    client, _helper, _auth, issuer, session = protocol
    issuer.issue.side_effect = None
    issuer.issue.return_value = seed(1001, token="oauth-17")
    response = upload(client, connect_helper(client))
    assert response.status_code == 400
    assert_failure(client, response, code="ACCOUNT_MISMATCH", stage="issued_validation",
                   reason="account_mismatch", operation="identity")
    assert session.seed() is None and not session.path.exists()


def test_session_storage_failure_has_own_stage_and_preserves_waiting_state(protocol, monkeypatch):
    client, _helper, _auth, _issuer, session = protocol

    def fail(_state):
        raise SessionError("SAVE")

    monkeypatch.setattr(session._file, "write", fail)
    response = upload(client, connect_helper(client))
    assert response.status_code == 400
    assert_failure(client, response, code="SAVE", stage="persistence")
    assert session.seed() is None and not session.path.exists()


@pytest.mark.parametrize("phase", ["captured_validation", "server_issuance", "issued_validation"])
def test_unexpected_failure_retains_phase_and_only_safe_labels(protocol, phase, monkeypatch, caplog):
    client, _helper, _auth, issuer, session = protocol
    secret = "private OAuth cookie verification code 123456"
    if phase == "server_issuance":
        issuer.issue.side_effect = RuntimeError(secret)
    elif phase == "captured_validation":
        monkeypatch.setattr(session._transport, "validate", AsyncMock(side_effect=RuntimeError(secret)))
    else:
        validate = session._transport.validate
        calls = 0

        async def fail_second(bundle, expected):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise RuntimeError(secret)
            return await validate(bundle, expected)

        monkeypatch.setattr(session._transport, "validate", fail_second)
    response = upload(client, connect_helper(client))
    assert response.status_code == 503
    assert_failure(client, response, code="HELPER_FAILED", stage=phase, reason="unexpected_exception")
    assert secret not in response.text + json.dumps(client.get("/api/session").json()) + caplog.text
    assert session.seed() is None and not session.path.exists()


def test_twitch_http_error_is_distinct_from_helper_response_http_status(protocol):
    client, _helper, _auth, _issuer, session = protocol
    session._transport.request.side_effect = SessionError("AUTH", reason="http_error", upstream_status=401)
    response = upload(client, connect_helper(client))
    assert response.status_code == 400
    diagnostic = assert_failure(client, response, code="AUTH", stage="captured_validation",
                                reason="http_error", operation="identity")
    assert diagnostic["http_status"] == 400 and diagnostic["upstream_status"] == 401
    assert session.seed() is None


def test_new_helper_attempt_clears_old_diagnostic_and_revokes_old_ticket(protocol):
    client, helper, _auth, issuer, session = protocol
    issuer.issue.side_effect = SessionError("SDK_TIMEOUT", reason="timeout")
    ticket = connect_helper(client)
    response = upload(client, ticket)
    assert_failure(client, response, code="SDK_TIMEOUT", stage="server_issuance", reason="timeout")
    old_attempt = helper.attempt
    new = client.post("/api/session/helper/enable")
    assert new.status_code == 200 and helper.attempt > old_attempt
    assert new.json()["helper"]["error"] is None
    assert new.json()["helper"]["diagnostic"] is None
    assert client.get("/api/helper/result", headers={"Authorization": "Bearer " + ticket}).status_code == 401
    assert session.seed() is None


def test_seed_rejection_is_available_from_receipt_without_repeating_upload(protocol):
    client, _helper, _auth, issuer, session = protocol
    ticket = connect_helper(client)
    headers = {"Authorization": "Bearer " + ticket}
    response = client.post("/api/helper/session", json={}, headers=headers)
    assert response.status_code == 400
    assert response.json()["diagnostic"]["stage"] == "seed_validation"
    receipt = client.get("/api/helper/result", headers=headers)
    assert receipt.status_code == 400
    assert receipt.json()["detail"] == response.json()["detail"]
    assert receipt.json()["diagnostic"] == response.json()["diagnostic"]
    issuer.issue.assert_not_awaited()
    assert session.seed() is None


@pytest.mark.asyncio
async def test_renewal_worker_exposes_unexpected_failure_without_secret_or_worker_exit(controller):
    control, session, _clock, issuer = controller
    await control.accept(seed(), authorized=lambda: True)
    failed = asyncio.Event()
    secret = "private renewal bearer credential"
    issuer.issue.side_effect = RuntimeError(secret)
    control.on_change = lambda: failed.set() if control.status()["renewal_error"] else None
    control.request_renewal()
    control.start()
    try:
        await asyncio.wait_for(failed.wait(), 1)
        status = control.status()
        assert status["renewal_error"] == "HELPER_FAILED"
        assert status["renewal_diagnostic"]["stage"] == "renewal_issuance"
        assert status["renewal_diagnostic"]["reason"] == "unexpected_exception"
        assert secret not in json.dumps(status)
        assert control._task is not None and not control._task.done()
        assert session.status()["state"] == "ready"
    finally:
        await control.stop()


@pytest.mark.asyncio
async def test_cancelled_admission_cannot_install_or_restore_stale_diagnostic(controller):
    from src.auth.helper_connection import HelperConnections

    control, session, clock, issuer = controller
    helper = HelperConnections(control, clock=lambda: clock[0])
    helper.enable(lambda: True)
    token = helper.connect()["connection"]
    entered = asyncio.Event()

    async def wait(_seed, *, initial=False):
        entered.set()
        await asyncio.Event().wait()

    issuer.issue.side_effect = wait
    accepting = asyncio.create_task(helper.accept(token, seed().to_dict()))
    await asyncio.wait_for(entered.wait(), 1)
    await helper.cancel()
    with pytest.raises(asyncio.CancelledError):
        await accepting
    helper.enable(lambda: True)
    assert helper.status()["diagnostic"] is None and helper.status()["error"] is None
    assert session.seed() is None and not session.path.exists()
