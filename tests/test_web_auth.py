"""Exercise the production HTTP/Socket.IO guards without using real miner data."""

import asyncio
import importlib
import json
import time
from collections import deque
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from starlette.requests import Request
from starlette.websockets import WebSocketDisconnect

from src.i18n.translator import GUIAuth, _
from src.web.auth import AuthSocketServer, WebAuth


web = importlib.import_module("src.web.app")
PASSWORD = "test password only"
HEADERS = {"X-TDM-Request": "1"}


@pytest.fixture(scope="module")
def hashed():
    return WebAuth.hash_password(PASSWORD)


@pytest.fixture
def client(tmp_path, monkeypatch):
    auth = web.web_auth
    for name, value in {"path": tmp_path / "web_auth.json", "password_hash": "",
                        "sessions": {}, "lock": asyncio.Lock(), "attempts": deque()}.items():
        monkeypatch.setattr(auth, name, value)
    monkeypatch.setattr(web.sio, "tokens", {})
    monkeypatch.setattr(web.sio, "expirations", {})
    monkeypatch.setattr(web, "gui_manager", None)
    monkeypatch.setattr(web, "twitch_client", None)
    with TestClient(web.socket_app, headers=HEADERS) as browser:
        yield browser


@pytest.fixture
def protected(client, hashed):
    web.web_auth.save(hashed, {})
    return client


def login(client, remember=False, password=PASSWORD):
    return client.post("/api/auth/login", json={"password": password, "remember": remember})


def configure(client, action="enable", current="", password=PASSWORD, confirm=None):
    return client.post("/api/auth/settings", json={"action": action, "current_password": current,
                       "password": password, "confirm_password": password if confirm is None else confirm})


class TestDashboardAuth:
    def test_disabled_by_default_and_login_redirects(self, client):
        assert client.get("/").status_code == 200
        assert client.get("/login", follow_redirects=False).headers["location"] == "/"
        assert not client.get("/api/auth/status").json()["enabled"]
        assert not web.web_auth.path.exists()

    def test_enable_cookie_and_private_storage(self, client):
        response = configure(client)
        assert response.status_code == 200
        cookie = response.headers["set-cookie"].lower()
        assert "httponly" in cookie and "samesite=strict" in cookie
        assert "max-age" not in cookie and "expires" not in cookie
        stored = web.web_auth.path.read_text()
        token = client.cookies.get(WebAuth.COOKIE)
        assert PASSWORD not in stored and token not in stored
        assert web.web_auth.authenticated(token)
        assert WebAuth(web.web_auth.path).authenticated(token)
        assert client.get("/").status_code == 200

    def test_dashboard_redirect_and_all_protected_routes(self, protected):
        response = protected.get("/", follow_redirects=False)
        assert response.status_code == 303 and response.headers["location"] == "/login"
        assert 'id="web-auth-login"' in protected.get("/login").text
        assert protected.get("/healthz").json() == {"status": "ok"}
        for path, operations in web.app.openapi()["paths"].items():
            if not path.startswith("/api/") or path in ("/api/auth/status", "/api/auth/login"):
                continue
            for method in operations:
                response = protected.request(method, path)
                assert response.status_code == 401, (method, path)
        for path in ("/docs", "/openapi.json", "/static/app.js", "/socket.io/?EIO=4&transport=polling"):
            assert protected.get(path).status_code == 401, path
        for path in ("/static/auth.js", "/static/auth.css", "/static/styles.css", "/api/auth/status"):
            assert protected.get(path).status_code == 200, path

    def test_wrong_password_and_cookie_tampering(self, protected):
        assert login(protected, password="wrong").status_code == 401
        assert not protected.cookies
        assert login(protected).status_code == 200
        token = protected.cookies.get(WebAuth.COOKIE)
        protected.cookies.clear()
        protected.cookies.set(WebAuth.COOKIE, token + "tampered")
        assert protected.get("/api/settings").status_code == 401

    @pytest.mark.parametrize("remember", [False, True])
    def test_remember_cookie_and_fixed_expiry(self, protected, remember, monkeypatch):
        now = time.time()
        monkeypatch.setattr("src.web.auth.time.time", lambda: now)
        response = login(protected, remember=remember)
        assert response.status_code == 200
        cookie = response.headers["set-cookie"].lower()
        assert ("max-age=2592000" in cookie) is remember
        token = protected.cookies.get(WebAuth.COOKIE)
        assert web.web_auth.sessions[WebAuth.digest(token)] == now + 2592000
        assert WebAuth(web.web_auth.path).authenticated(token)
        monkeypatch.setattr("src.web.auth.time.time", lambda: now + 2592000)
        assert protected.get("/api/settings").status_code == 401

    def test_https_cookie_and_untrusted_forwarded_headers(self, protected):
        with TestClient(web.socket_app, base_url="https://testserver", headers=HEADERS) as secure:
            assert "Secure" in login(secure).headers["set-cookie"]
        response = protected.post("/api/auth/login", json={"password": PASSWORD},
                                  headers={"X-Forwarded-Proto": "https", "X-Forwarded-Host": "evil.test"})
        assert "Secure" not in response.headers["set-cookie"]

    def test_logout_revokes_only_current_session(self, protected):
        login(protected)
        first = protected.cookies.get(WebAuth.COOKIE)
        protected.cookies.clear()
        login(protected)
        second = protected.cookies.get(WebAuth.COOKIE)
        response = protected.post("/api/auth/logout")
        assert response.status_code == 200 and "Max-Age=0" in response.headers["set-cookie"]
        assert web.web_auth.authenticated(first)
        assert not web.web_auth.authenticated(second)
        assert not WebAuth(web.web_auth.path).authenticated(second)

    def test_change_requires_current_password_and_revokes_other_sessions(self, protected):
        login(protected)
        old_token = protected.cookies.get(WebAuth.COOKIE)
        assert configure(protected, "change", "wrong").status_code == 401
        response = configure(protected, "change", PASSWORD, "replacement password")
        assert response.status_code == 200
        assert not web.web_auth.authenticated(old_token)
        assert web.web_auth.authenticated(protected.cookies.get(WebAuth.COOKIE))
        assert len(web.web_auth.sessions) == 1
        protected.cookies.clear()
        assert login(protected).status_code == 401
        assert login(protected, password="replacement password").status_code == 200

    def test_disable_requires_password_clears_credentials_and_old_tokens_stay_revoked(self, protected):
        login(protected)
        old_token = protected.cookies.get(WebAuth.COOKIE)
        assert configure(protected, "disable", "wrong").status_code == 401
        assert web.web_auth.enabled
        assert configure(protected, "disable", PASSWORD).status_code == 200
        assert json.loads(web.web_auth.path.read_text()) == {"version": 1, "password_hash": "", "sessions": {}}
        assert protected.get("/").status_code == 200
        assert configure(protected).status_code == 200
        assert not web.web_auth.authenticated(old_token)

    @pytest.mark.parametrize("password,confirm", [("short", "short"), ("x" * 1025, "x" * 1025),
                                                 (PASSWORD, "mismatch")])
    def test_invalid_setup_never_enables(self, client, password, confirm):
        assert configure(client, password=password, confirm=confirm).status_code == 400
        assert not web.web_auth.enabled and not web.web_auth.path.exists()

    def test_atomic_failure_preserves_running_and_persisted_policy(self, protected, monkeypatch):
        login(protected)
        before = web.web_auth.path.read_bytes()
        token = protected.cookies.get(WebAuth.COOKIE)
        monkeypatch.setattr("src.web.auth.os.replace", MagicMock(side_effect=OSError("disk full")))
        with pytest.raises(OSError, match="disk full"):
            configure(protected, "disable", PASSWORD)
        assert web.web_auth.path.read_bytes() == before
        assert web.web_auth.authenticated(token) and web.web_auth.enabled
        assert not list(web.web_auth.path.parent.glob(".web-auth-*"))

    @pytest.mark.parametrize("headers", [{"Origin": "https://evil.test"}, {"Origin": "null"},
        {"Sec-Fetch-Site": "cross-site"}, {"X-TDM-Request": ""},
        {"Origin": "https://evil.test", "X-Forwarded-Host": "evil.test", "X-Forwarded-Proto": "https"}])
    def test_csrf_blocked_even_before_setup(self, client, headers):
        response = client.post("/api/auth/settings", headers=headers,
            json={"action": "enable", "password": PASSWORD, "confirm_password": PASSWORD})
        assert response.status_code == 403 and not web.web_auth.enabled

    def test_same_origin_setup(self, client):
        response = client.post("/api/auth/settings", headers={"Origin": "http://testserver"},
            json={"action": "enable", "password": PASSWORD, "confirm_password": PASSWORD})
        assert response.status_code == 200

    def test_rate_limit_and_recovery(self, protected, monkeypatch):
        now = time.monotonic()
        monkeypatch.setattr("src.web.auth.time.monotonic", lambda: now)
        for _attempt in range(5):
            assert login(protected, password="wrong").status_code == 401
        response = login(protected)
        assert response.status_code == 429 and response.headers["retry-after"] == "60"
        monkeypatch.setattr("src.web.auth.time.monotonic", lambda: now + 61)
        assert login(protected).status_code == 200

    def test_no_secret_echo_or_cache(self, protected):
        response = protected.post("/api/auth/login", json={"password": {"secret": PASSWORD}})
        assert response.status_code == 422 and PASSWORD not in response.text
        assert "no-store" in response.headers["cache-control"]
        assert "sessions" not in protected.get("/api/auth/status").json()
        assert protected.post("/api/auth/login", content=b"x" * 16385).status_code == 413
        assert protected.post("/api/auth/login", content=b"{bad json").status_code == 422

    def test_auth_credentials_cannot_be_modified_via_normal_settings(self, protected, monkeypatch):
        login(protected)
        gui = SimpleNamespace(settings=MagicMock())
        gui.settings.update_settings.return_value = {}
        monkeypatch.setattr(web, "gui_manager", gui)
        assert protected.post("/api/settings", json={"password_hash": "", "web_auth": False}).status_code == 200
        gui.settings.update_settings.assert_called_once_with({})
        assert web.web_auth.enabled

    def test_unauthenticated_websocket_upgrade_rejected(self, protected):
        with pytest.raises(WebSocketDisconnect) as error, protected.websocket_connect(
            "/socket.io/?EIO=4&transport=websocket"
        ):
            pass
        assert error.value.code == 1008

    def test_cross_origin_socket_polling_rejected(self, client):
        assert client.get("/socket.io/?EIO=4&transport=polling", headers={"Origin": "https://evil.test"}).status_code == 403

    def test_live_anonymous_socket_is_disconnected_when_protection_is_enabled(self, client):
        with client.websocket_connect("/socket.io/?EIO=4&transport=websocket", headers={"Upgrade": "websocket"}) as connection:
            assert connection.receive_text().startswith("0")
            connection.send_text("40")
            assert connection.receive_text().startswith("40")
            assert configure(client).status_code == 200
            assert connection.receive_text() == "41"
            assert not web.sio.tokens
        with client.websocket_connect("/socket.io/?EIO=4&transport=websocket", headers={"Upgrade": "websocket"}) as connection:
            assert connection.receive_text().startswith("0")
            connection.send_text("40")
            assert connection.receive_text().startswith("40")
            assert web.sio.tokens
            assert client.post("/api/auth/logout").status_code == 200
            assert connection.receive_text() == "41"
            assert not web.sio.tokens

    def test_second_enable_cannot_replace_password(self, protected):
        login(protected)
        assert configure(protected).status_code == 409


class TestAuthStorageAndSockets:
    def test_corrupt_storage_fails_closed(self, tmp_path):
        path = tmp_path / "web_auth.json"
        for content in ("{", '{}', '{"version":1,"password_hash":"broken","sessions":{}}',
                        '{"version":1,"password_hash":"","sessions":{"bad":42}}'):
            path.write_text(content)
            with pytest.raises((ValueError, KeyError)):
                WebAuth(path)

    def test_salts_differ(self):
        assert WebAuth.hash_password(PASSWORD) != WebAuth.hash_password(PASSWORD)

    def test_global_rate_limit_bounds_memory(self, tmp_path):
        auth = WebAuth(tmp_path / "auth.json")
        for index in range(30):
            auth.limit(Request({"type": "http", "client": (str(index), 80)}))
        with pytest.raises(HTTPException) as error:
            auth.limit(Request({"type": "http", "client": ("another-ip", 80)}))
        assert error.value.status_code == 429 and len(auth.attempts) == 30

    @pytest.mark.asyncio
    async def test_socket_connection_and_events_recheck_sessions(self, tmp_path, hashed, monkeypatch):
        auth = WebAuth(tmp_path / "auth.json")
        sio = AuthSocketServer(auth)
        scope = {"type": "websocket", "headers": []}
        assert sio.register("anonymous", scope)
        auth.save(hashed, {})
        assert not sio.register("blocked", scope)
        disconnect = AsyncMock(side_effect=lambda sid: sio.forget(sid))
        monkeypatch.setattr(sio, "disconnect", disconnect)
        await sio.prune()
        disconnect.assert_awaited_once_with("anonymous")
        token, sessions = auth.new_session()
        auth.save(hashed, sessions)
        scope["headers"] = [(b"cookie", f"{auth.COOKIE}={token}".encode())]
        assert sio.register("authenticated", scope)
        assert await sio.authorize("authenticated")
        auth.sessions.clear()
        await sio.emit("private_update", {"secret": "must not leak"})
        assert "authenticated" not in sio.tokens
        assert not sio.expirations

    @pytest.mark.asyncio
    async def test_socket_deadline_disconnects_without_more_traffic(self, tmp_path, hashed, monkeypatch):
        auth = WebAuth(tmp_path / "auth.json")
        auth.save(hashed, {auth.digest("token"): time.time() + .01})
        sio = AuthSocketServer(auth)
        disconnect = AsyncMock(side_effect=lambda sid: sio.forget(sid))
        monkeypatch.setattr(sio, "disconnect", disconnect)
        assert sio.register("idle", {"type": "websocket", "headers": [(b"cookie", b"tdm_session=token")]})
        await asyncio.sleep(.03)
        disconnect.assert_awaited_once_with("idle")

    @pytest.mark.asyncio
    async def test_provisional_socket_cannot_receive_private_broadcast(self, tmp_path, monkeypatch):
        sio = AuthSocketServer(WebAuth(tmp_path / "auth.json"))
        sid = await sio.manager.connect("unverified-transport", "/")

        async def disconnect(client_sid):
            await sio.manager.disconnect(client_sid, "/")

        rejected = AsyncMock(side_effect=disconnect)
        monkeypatch.setattr(sio, "disconnect", rejected)
        send = AsyncMock()
        monkeypatch.setattr(sio, "_send_eio_packet", send)
        await sio.emit("private_update", {"secret": "must not leak"})
        rejected.assert_awaited_once_with(sid)
        send.assert_not_awaited()

    def test_auth_translation_schema(self):
        assert set(_.t["gui"]["auth"]) == set(GUIAuth.__annotations__)
