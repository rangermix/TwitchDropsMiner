"""Desktop fallback capabilities share the current validation and logout boundary."""

import asyncio
import json
from unittest.mock import AsyncMock

import pytest

from src.auth.helper_connection import HelperConnections
from src.auth.session_bundle import SessionError
from tests.test_session_controller import controller, seed  # noqa: F401


@pytest.fixture
def helper(controller):
    control, session, clock, issuer = controller
    connections = HelperConnections(control, clock=lambda: clock[0])
    control.on_change = connections.session_changed
    return connections, control, session, clock, issuer


def connect(helper):
    helper.enable(lambda: True)
    return helper.connect()["connection"]


def test_disabled_by_default_window_accepts_one_helper_and_hides_ticket(helper):
    h, _, session, clock, _ = helper
    assert h.status()["state"] == "disabled"
    with pytest.raises(SessionError, match="HELPER_DISABLED"):
        h.connect()
    assert h.enable(lambda: True) is None
    assert h.status()["state"] == "waiting"
    ticket = h.connect()
    assert ticket["connection"] not in json.dumps(h.status())
    assert ticket["connection"] not in repr(h.__dict__)
    with pytest.raises(SessionError, match="BUSY"):
        h.connect()
    clock[0] += 601
    with pytest.raises(SessionError, match="CONNECTION"):
        h.result(ticket["connection"])
    assert h.status()["state"] == "error"
    assert not session.path.exists()


@pytest.mark.parametrize("event", ["expiry", "revocation"])
def test_window_closes_before_a_helper_connects(helper, event):
    h, _, _, clock, _ = helper
    allowed = [True]
    h.enable(lambda: allowed[0])
    if event == "expiry":
        clock[0] += h.CONNECTION_SECONDS
    else:
        allowed[0] = False
    with pytest.raises(SessionError, match="HELPER_DISABLED"):
        h.connect()
    assert h.status()["state"] == "error"


@pytest.mark.asyncio
async def test_accept_reuses_controller_and_receipt_does_not_replay(helper):
    h, control, session, _, issuer = helper
    token = connect(h)
    result = await h.accept(token, seed().to_dict())
    assert result["success"] and result["allow_helper_connection"] is False
    assert result["session"]["user_id"] == 42
    assert h.state == "complete" and not h.selected
    assert h.result(token) == result
    with pytest.raises(SessionError, match="BUSY"):
        await h.accept(token, seed().to_dict())
    assert issuer.issue.await_count == 1
    assert session._transport.request.await_count == 4
    saved = json.loads(session.path.read_text())
    assert saved["version"] == 3 and not saved["logged_out"]
    assert "helper_receipt" not in saved and token not in session.path.read_text()
    await control.renew_once()
    assert h.result(token) == result
    await h.cancel()
    with pytest.raises(SessionError, match="CONNECTION"):
        h.result(token)


@pytest.mark.asyncio
async def test_cancel_and_new_enable_revoke_old_ticket(helper):
    h, _, _, _, _ = helper
    token = connect(h)
    old_attempt = h.attempt
    await h.cancel()
    h.enable(lambda: True)
    assert h.attempt > old_attempt
    with pytest.raises(SessionError, match="CONNECTION"):
        h.result(token)
    h.connect()
    await h.stop()
    with pytest.raises(SessionError, match="HELPER_DISABLED"):
        h.enable(lambda: True)


@pytest.mark.asyncio
async def test_receipt_expiry_does_not_end_successful_login(helper):
    h, _, session, clock, _ = helper
    token = connect(h)
    await h.accept(token, seed().to_dict())
    clock[0] += 601
    assert h.status()["state"] == "disabled" and not h.selected
    assert session.status()["state"] == "ready" and session.seed() is not None
    with pytest.raises(SessionError, match="CONNECTION"):
        h.result(token)


@pytest.mark.asyncio
@pytest.mark.parametrize("event", ["cancel", "expire", "dashboard_revoked", "logout"])
async def test_inflight_verification_cannot_commit_after_revocation(helper, event):
    h, control, session, clock, issuer = helper
    permission = [True]
    h.enable(lambda: permission[0])
    token = h.connect()["connection"]
    entered, release, cancelled = asyncio.Event(), asyncio.Event(), asyncio.Event()
    original = issuer.issue.side_effect

    async def resistant(previous, *, initial=False):
        entered.set()
        try:
            await release.wait()
        except asyncio.CancelledError:
            cancelled.set()
            await release.wait()
        return await original(previous, initial=initial)

    issuer.issue.side_effect = resistant
    task = asyncio.create_task(h.accept(token, seed().to_dict()))
    await entered.wait()
    ending = None
    if event == "cancel":
        ending = asyncio.create_task(h.cancel())
    elif event == "logout":
        ending = asyncio.create_task(control.logout(AsyncMock()))
    elif event == "expire":
        clock[0] += 601
    else:
        permission[0] = False
    if ending:
        await cancelled.wait()
        assert not ending.done()
    release.set()
    if ending:
        await ending
    with pytest.raises(SessionError):
        await task
    assert session.seed() is None and session.status()["generation"] == 0


@pytest.mark.asyncio
async def test_competing_submission_cannot_replace_first_account(helper):
    h, _, session, _, issuer = helper
    token = connect(h)
    entered, release = asyncio.Event(), asyncio.Event()
    original = issuer.issue.side_effect

    async def issue(previous, *, initial=False):
        entered.set()
        await release.wait()
        return await original(previous, initial=initial)

    issuer.issue.side_effect = issue
    task = asyncio.create_task(h.accept(token, seed().to_dict()))
    await entered.wait()
    with pytest.raises(SessionError, match="BUSY"):
        await h.accept(token, seed(token="oauth-17").to_dict())
    release.set()
    await task
    assert session.status()["user_id"] == 42 and issuer.issue.await_count == 1


@pytest.mark.asyncio
async def test_another_login_invalidates_pending_helper(helper):
    h, control, _, _, issuer = helper
    token = connect(h)
    await control.accept(seed(), authorized=lambda: True)
    assert h.state == "disabled"
    with pytest.raises(SessionError, match="CONNECTION"):
        await h.accept(token, seed(token="oauth-17").to_dict())
    assert issuer.issue.await_count == 1


@pytest.mark.asyncio
async def test_invalid_or_failed_upload_preserves_credentials_and_redacts(helper):
    h, _, session, _, issuer = helper
    token = connect(h)
    with pytest.raises(SessionError):
        await h.accept("x" * 43, {"private": "secret"})
    issuer.issue.assert_not_awaited()
    issuer.issue.side_effect = OSError("private credential")
    with pytest.raises(SessionError, match="HELPER_FAILED"):
        await h.accept(token, seed().to_dict())
    assert not session.path.exists()
    assert h.status()["error"] == "HELPER_FAILED"
    assert "private" not in json.dumps(h.status())
