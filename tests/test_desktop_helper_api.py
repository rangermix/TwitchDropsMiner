"""Dashboard-enabled connection windows and narrowly scoped native requests."""

import asyncio
import json
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.auth.helper_connection import HelperConnections
from src.web.auth import AuthMiddleware, WebAuth
from src.web.helper_api import HelperAPI
from src.web.session_api import SessionAPI
from tests.test_session_controller import controller, seed  # noqa: F401


@pytest.fixture
def protocol(tmp_path, controller):
    control, session, clock, issuer = controller
    helper = HelperConnections(control, clock=lambda: clock[0])
    control.on_change = helper.session_changed
    auth = WebAuth(tmp_path / "dashboard.json")
    browser = SimpleNamespace(status=lambda: {"state": "idle", "attempt": 1, "error": None},
                              cancel=AsyncMock(), request_login=lambda: None)

    async def enable(authorized):
        await helper.cancel()
        await browser.cancel()
        return helper.enable(authorized)

    async def cancel():
        await helper.cancel()

    miner = SimpleNamespace(helper=helper, session_controller=control, login_browser=browser,
                            _auth_state=SimpleNamespace(_logged_in=asyncio.Event()),
                            enable_helper=enable, cancel_helper=cancel)
    app = FastAPI()
    app.include_router(SessionAPI(auth, lambda: miner).router)
    app.include_router(HelperAPI(auth, lambda: miner).router)
    app.add_middleware(AuthMiddleware, auth=auth)
    return TestClient(app, headers={"X-TDM-Request": "1"}), helper, auth, issuer, session


def connect_helper(client):
    enabled = client.post("/api/session/helper/enable")
    assert enabled.status_code == 200
    assert "helper_code" not in enabled.json()
    connected = client.post("/api/helper/connect", json={})
    assert connected.status_code == 200
    return connected.json()["connection"]


def test_native_helper_uses_enabled_window_without_dashboard_password_or_code(protocol):
    client, helper, auth, issuer, session = protocol
    auth.password_hash = "configured"
    auth.sessions[auth.digest("dashboard-session")] = time.time() + 60
    assert client.post("/api/session/helper/enable").status_code == 401
    assert client.post("/api/helper/connect", json={}).status_code == 403
    client.cookies.set(auth.COOKIE, "dashboard-session")
    response = client.post("/api/session/helper/enable")
    assert response.status_code == 200 and "helper_code" not in response.json()
    assert response.headers["cache-control"] == "no-store"
    client.cookies.clear()
    result = client.post("/api/helper/connect", json={})
    assert result.status_code == 200
    token = result.json()["connection"]
    assert result.headers["cache-control"] == "no-store"
    client.cookies.set(auth.COOKIE, "dashboard-session")
    status = client.get("/api/session")
    assert token not in status.text and "helper_code" not in status.json()
    client.cookies.clear()
    response = client.post("/api/helper/session", json=seed().to_dict(), headers={"Authorization": "Bearer " + token})
    assert response.status_code == 200 and response.json()["success"]
    assert response.headers["cache-control"] == "no-store"
    for secret in (token, "private-sdk-cookie", "oauth-42", "initial-integrity"):
        assert secret not in response.text and secret not in json.dumps(helper.status())
    receipt = client.get("/api/helper/result", headers={"Authorization": "Bearer " + token})
    assert receipt.json() == response.json()
    assert client.post("/api/helper/session", json=seed().to_dict(), headers={"Authorization": "Bearer " + token}).status_code == 429
    assert issuer.issue.await_count == 1 and session.status()["generation"] == 1
    assert client.post("/api/session/helper/cancel").status_code == 401


@pytest.mark.parametrize("endpoint,method", [("connect", "post"), ("session", "post"), ("result", "get")])
def test_native_routes_reject_foreign_origin_and_missing_request_header(protocol, endpoint, method):
    client, _, _, issuer, _ = protocol
    token = connect_helper(client)
    path = "/api/helper/" + endpoint
    request = getattr(client, method)
    headers = {"Authorization": "Bearer " + token}
    for extra in ({"Origin": "https://evil.test"}, {"Sec-Fetch-Site": "cross-site"}, {"X-TDM-Request": "0"}):
        assert request(path, headers={**headers, **extra}).status_code == 403
    issuer.issue.assert_not_awaited()


def test_admission_disabled_single_use_bounded_and_redacted(protocol):
    client, _, _, issuer, _ = protocol
    assert client.post("/api/helper/connect", json={}).status_code == 403
    token = connect_helper(client)
    assert client.post("/api/helper/connect", json={}).status_code == 429
    headers = {"Authorization": "Bearer " + token}
    assert client.get("/api/helper/result").status_code == 401
    assert client.post("/api/helper/session", json=seed().to_dict()).status_code == 401
    assert client.get("/api/helper/result", headers={"Authorization": "Bearer " + "a" * 43}).status_code == 401
    oversized = client.post("/api/helper/session", content=b"x" * 65537, headers=headers)
    assert oversized.status_code == 413
    invalid = client.post("/api/helper/session", json={"secret": "private"}, headers=headers)
    assert invalid.status_code == 400 and "private" not in invalid.text
    assert client.post("/api/session/helper/cancel").status_code == 200
    assert client.post("/api/helper/session", json=seed().to_dict(), headers=headers).status_code == 401
    issuer.issue.assert_not_awaited()


def test_dashboard_revocation_closes_an_enabled_window(protocol):
    client, _, auth, issuer, _ = protocol
    auth.password_hash = "configured"
    auth.sessions[auth.digest("dashboard-session")] = time.time() + 60
    client.cookies.set(auth.COOKIE, "dashboard-session")
    assert client.post("/api/session/helper/enable").status_code == 200
    auth.sessions.clear()
    assert client.post("/api/helper/connect", json={}).status_code == 403
    issuer.issue.assert_not_awaited()


def test_stale_embedded_retry_preserves_helper_window_and_connected_ticket(protocol):
    client, helper, _, issuer, _ = protocol
    assert client.post("/api/session/helper/enable").status_code == 200
    response = client.post("/api/session/retry")
    assert response.status_code == 409 and response.json()["detail"] == "session_helper_state"
    assert helper.status()["state"] == "waiting"
    connected = client.post("/api/helper/connect", json={})
    assert connected.status_code == 200
    token = connected.json()["connection"]
    assert client.post("/api/session/retry").status_code == 409
    assert helper.status()["state"] == "connected"
    response = client.get("/api/helper/result", headers={"Authorization": "Bearer " + token})
    assert response.status_code == 200 and response.json() == {"state": "pending"}
    issuer.issue.assert_not_awaited()


def test_admission_actions_keep_dashboard_csrf_checks(protocol):
    client, _, _, _, _ = protocol
    for action in ("enable", "cancel"):
        path = "/api/session/helper/" + action
        assert client.post(path, headers={"X-TDM-Request": "0"}).status_code == 403
        assert client.post(path, headers={"Origin": "https://evil.test"}).status_code == 403


@pytest.mark.parametrize("body", ["[", "[]", '{"private": "secret"}'])
def test_malformed_admission_does_not_consume_window(protocol, body):
    client, helper, _, issuer, _ = protocol
    assert client.post("/api/session/helper/enable").status_code == 200
    rejected = client.post("/api/helper/connect", content=body)
    assert rejected.status_code == 400 and "secret" not in rejected.text
    assert helper.status()["state"] == "waiting"
    assert client.post("/api/helper/connect", json={}).status_code == 200
    issuer.issue.assert_not_awaited()
