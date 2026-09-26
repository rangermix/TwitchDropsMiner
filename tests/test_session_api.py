"""Dashboard session status is sanitized and has no credential export route."""

import pytest

from tests.test_helper_api import api, enable_dashboard_auth  # noqa: F401
from tests.test_helper_connection import seed


def test_helper_status_is_available_without_mandatory_dashboard_auth(api):
    browser, helper, settings = api
    response = browser.get("/api/session")
    assert response.status_code == 200
    assert response.json()["allow_helper_connection"] is True
    assert response.headers["Cache-Control"] == "no-store"
    token = browser.post("/api/helper/connect", json={}).json()["connection"]
    headers = {"Authorization": "Bearer " + token}
    assert browser.post("/api/helper/session", json=seed().to_dict(), headers=headers).status_code == 200
    response = browser.get("/api/session")
    assert response.json()["session"]["generation"] == 1
    assert response.json()["allow_helper_connection"] is False
    for value in (token, "oauth-42", "private-sdk-cookie", "renewed-private-sdk", "renewed-private-integrity"):
        assert value not in response.text


def test_dashboard_password_still_protects_session_status(api):
    browser, helper, settings = api
    enable_dashboard_auth(browser)
    assert browser.get("/api/session").status_code == 401
    token = browser.post("/api/helper/connect", json={}).json()["connection"]
    assert browser.get("/api/session", headers={"Authorization": "Bearer " + token}).status_code == 401


@pytest.mark.parametrize("path", ["/api/session/export", "/api/helper/session", "/api/helper/seed"])
def test_no_route_exports_session_credentials(api, path):
    browser, helper, settings = api
    response = browser.get(path)
    assert response.status_code in (404, 405)
