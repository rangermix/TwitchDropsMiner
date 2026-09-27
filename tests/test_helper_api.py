"""Exercise the helper gate through the production outer HTTP middleware."""

import asyncio
import importlib
import json
from collections import deque
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient

from src.auth.helper_connection import HelperConnections
from src.auth.imported_session import ImportedSession, SessionTransport
from src.auth.server_seed import SDKCookie, ServerSeed
from src.auth.session_bundle import SessionBundle, SessionError
from src.config import ClientType
from src.web.managers.settings import SettingsManager
from tests.test_helper_connection import seed
from tests.test_imported_session import catalog


web = importlib.import_module("src.web.app")


@pytest.fixture
def api(tmp_path, monkeypatch):
    transport = SessionTransport()

    async def request(method, url, *, headers, body=None):
        return {"client_id": ClientType.WEB.CLIENT_ID, "user_id": "42"} if method == "GET" else catalog()

    transport.request = AsyncMock(side_effect=request)
    session = ImportedSession(tmp_path / "session.json", transport=transport, clock=lambda: 1000)
    data = seed().bundle.to_dict()
    data["headers"]["client-integrity"] = "renewed-private-integrity"
    data.update(captured_at=1001, expires_at=5000)
    issuer = SimpleNamespace(issue=AsyncMock(return_value=ServerSeed(
        SessionBundle.from_dict(data, now=1000), SDKCookie("renewed-private-sdk", 90000),
    )))
    settings = SimpleNamespace(allow_helper_connection=True, save=MagicMock())
    helper = HelperConnections(session, settings, issuer=issuer, clock=lambda: 1000)
    monkeypatch.setattr(web, "twitch_client", SimpleNamespace(_browser=session, helper=helper))
    auth = web.web_auth
    for name, value in {"path": tmp_path / "auth.json", "password_hash": "",
                        "sessions": {}, "lock": asyncio.Lock(), "attempts": deque()}.items():
        monkeypatch.setattr(auth, name, value)
    with TestClient(web.socket_app, headers={"X-TDM-Request": "1"}) as client:
        yield client, helper, settings


def enable_dashboard_auth(client):
    assert client.post("/api/auth/settings", json={
        "action": "enable", "current_password": "", "password": "only dashboard password",
        "confirm_password": "only dashboard password",
    }).status_code == 200
    client.cookies.clear()


@pytest.mark.parametrize("dashboard_protected", [False, True])
def test_helper_admission_and_acceptance_do_not_need_dashboard_password(api, dashboard_protected):
    client, helper, settings = api
    if dashboard_protected:
        enable_dashboard_auth(client)
        assert client.get("/api/settings").status_code == 401
    response = client.post("/api/helper/connect", json={})
    assert response.status_code == 200
    token = response.json()["connection"]
    response = client.post("/api/helper/session", headers={"Authorization": "Bearer " + token}, json=seed().to_dict())
    assert response.status_code == 200
    assert response.json()["success"] is True
    assert response.json()["allow_helper_connection"] is False
    assert response.headers["Cache-Control"] == "no-store"
    assert settings.allow_helper_connection is False
    assert client.post("/api/helper/connect", json={}).status_code == 403
    assert client.post("/api/helper/session", headers={"Authorization": "Bearer " + token}, json=seed().to_dict()).status_code == 403
    receipt = client.get("/api/helper/result", headers={"Authorization": "Bearer " + token})
    assert receipt.status_code == 200 and receipt.json() == response.json()
    for secret in ("oauth-42", "private-sdk-cookie", "renewed-private-integrity", "renewed-private-sdk"):
        assert secret not in response.text + receipt.text
    if dashboard_protected:
        assert client.get("/api/settings").status_code == 401


@pytest.mark.parametrize("path", ["/api/session/import", "/api/session/pair", "/api/session/revoke", "/api/session/renew", "/api/login", "/api/oauth/confirm"])
def test_old_login_and_credential_mutation_routes_are_removed(api, path):
    client, helper, settings = api
    assert client.post(path, json={}).status_code == 404


@pytest.mark.parametrize("kind", ["origin", "fetch_metadata", "write_header", "large", "malformed", "no_connection"])
def test_helper_guards_reject_without_echoing_credentials(api, kind):
    client, helper, settings = api
    token = helper.connect()["connection"]
    headers = {"Authorization": "Bearer " + token}
    options = {"json": seed().to_dict(), "headers": headers}
    expected = 403
    if kind == "origin":
        headers["Origin"] = "https://foreign.test"
    elif kind == "fetch_metadata":
        headers["Sec-Fetch-Site"] = "cross-site"
    elif kind == "write_header":
        headers["X-TDM-Request"] = "0"
    elif kind == "large":
        options = {"content": "secret-payload" + "x" * 65536, "headers": headers}
        expected = 413
    elif kind == "malformed":
        options = {"content": '{"secret-payload":', "headers": headers}
        expected = 400
    else:
        headers.clear()
        expected = 401
    response = client.post("/api/helper/session", **options)
    assert response.status_code == expected
    for secret in ("secret-payload", "oauth-42", "private-sdk-cookie"):
        assert secret not in response.text
    assert helper.allowed and helper.session.seed() is None


def test_helper_settings_control_is_strict_persistent_and_revokes_open_tickets(api):
    client, helper, settings = api
    manager = SettingsManager(MagicMock(emit=AsyncMock()), settings, MagicMock(), on_helper_change=helper.set_allowed)
    ticket = helper.connect()

    async def update():
        return manager.update_settings({"allow_helper_connection": False})

    response = asyncio.run(update())
    assert response["allow_helper_connection"] is False
    assert not helper.allowed
    restored = ImportedSession(helper.session.path, clock=lambda: 1000)
    assert restored.helper_allowed is False
    assert "private" not in json.dumps(restored.status())
    with pytest.raises(ValueError):
        asyncio.run(manager_update(manager, {"allow_helper_connection": "false"}))
    with pytest.raises(SessionError, match="CONNECTION"):
        helper.result(ticket["connection"])


async def manager_update(manager, data):
    return manager.update_settings(data)
