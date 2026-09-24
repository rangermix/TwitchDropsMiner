"""Exercise session import through the production dashboard guards."""

import asyncio
import importlib
from collections import deque
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from src.auth.imported_session import ImportedSession
from tests.test_imported_session import bundle_data, transport


web = importlib.import_module("src.web.app")


@pytest.fixture
def api(tmp_path, monkeypatch):
    service = ImportedSession(tmp_path / "session.json", clock=lambda: 1000, transport=transport())
    monkeypatch.setattr(web, "twitch_client", SimpleNamespace(_browser=service))
    auth = web.web_auth
    for name, value in {"path": tmp_path / "auth.json", "password_hash": "",
                        "sessions": {}, "lock": asyncio.Lock(), "attempts": deque()}.items():
        monkeypatch.setattr(auth, name, value)
    with TestClient(web.socket_app, headers={"X-TDM-Request": "1"}) as browser:
        yield browser, service


def authorize(browser):
    response = browser.post("/api/auth/settings", json={
        "action": "enable", "current_password": "", "password": "test import password",
        "confirm_password": "test import password",
    })
    assert response.status_code == 200


def test_import_requires_enabled_authenticated_dashboard(api):
    browser, service = api
    assert browser.get("/api/session").json()["authentication_required"] is True
    assert browser.post("/api/session/import", json=bundle_data()).status_code == 401
    authorize(browser)
    browser.cookies.clear()
    assert browser.post("/api/session/import", json=bundle_data()).status_code == 401
    assert not service.path.exists()


def test_manual_import_runs_validation_and_returns_only_status(api):
    browser, service = api
    authorize(browser)
    response = browser.post("/api/session/import", json=bundle_data())
    assert response.status_code == 200
    assert response.json()["session"]["state"] == "ready"
    assert "test-token" not in response.text and "test-integrity" not in response.text
    status = browser.get("/api/session")
    assert status.json()["session"]["generation"] == 1
    assert status.headers["Cache-Control"] == "no-store"
    assert service.path.exists()


@pytest.mark.parametrize("kind", ["origin", "write_header", "large", "malformed"])
def test_import_guards_and_errors_do_not_echo_submitted_secrets(api, kind):
    browser, service = api
    authorize(browser)
    options = {"json": bundle_data()}
    expected = 403
    if kind == "origin":
        options["headers"] = {"Origin": "https://evil.test"}
    elif kind == "write_header":
        options["headers"] = {"X-TDM-Request": "0"}
    elif kind == "large":
        options = {"content": '"secret-payload' + "x" * 65536}
        expected = 413
    else:
        options = {"content": '{"headers":"secret-payload"}'}
        expected = 400
    response = browser.post("/api/session/import", **options)
    assert response.status_code == expected
    assert "secret-payload" not in response.text
    assert not service.path.exists()
