"""Dashboard actions, retired routes and a bounded, revocable VNC bridge."""

import asyncio
from collections import deque
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocket, WebSocketDisconnect

from src.web.auth import WebAuth
from src.web.origin import DashboardOrigin
from src.web.session_api import SessionAPI
from tests.test_web_auth import web


@pytest.fixture
def api(tmp_path, monkeypatch):
    auth = web.web_auth
    for name, value in {"path": tmp_path / "auth.json", "password_hash": "", "sessions": {},
                        "lock": asyncio.Lock(), "attempts": deque(), "origin": DashboardOrigin("")}.items():
        monkeypatch.setattr(auth, name, value)
    miner = SimpleNamespace(session_controller=SimpleNamespace(status=lambda: {
        "session": {"state": "waiting", "user_id": None, "generation": 0}, "renewal_available": False,
        "renewal_error": None, "renewal_requires_login": False}),
        login_browser=SimpleNamespace(status=lambda: {"state": "starting", "attempt": 1, "error": None},
                                      finish=AsyncMock(), retry=AsyncMock(), state="starting", desktop=None),
        logout=AsyncMock(), _auth_state=SimpleNamespace(_logged_in=MagicMock(is_set=lambda: False)))
    monkeypatch.setattr(web, "twitch_client", miner)
    with TestClient(web.socket_app, headers={"X-TDM-Request": "1"}) as client:
        yield client, miner


def enable_dashboard_auth(client):
    assert client.post("/api/auth/settings", json={"action": "enable", "password": "isolated password",
                       "confirm_password": "isolated password"}).status_code == 200
    client.cookies.clear()


def test_status_only_contains_public_state(api):
    client, miner = api
    response = client.get("/api/session")
    assert response.status_code == 200
    assert response.json()["browser"]["state"] == "starting"
    assert response.json()["logged_in"] is False
    assert response.headers["cache-control"] == "no-store"
    assert set(response.json()) == {"session", "renewal_available", "renewal_error", "renewal_requires_login", "browser", "logged_in"}


@pytest.mark.parametrize("action", ["finish", "retry", "logout"])
def test_actions_use_csrf_guard_and_dashboard_authorization(api, action):
    client, miner = api
    path = "/api/session/" + action
    client.headers.pop("X-TDM-Request")
    assert client.post(path).status_code == 403
    client.headers["X-TDM-Request"] = "1"
    assert client.post(path, headers={"Origin": "https://evil.test"}).status_code == 403
    assert client.post(path).status_code == 200
    (miner.logout if action == "logout" else getattr(miner.login_browser, action)).assert_awaited_once()
    enable_dashboard_auth(client)
    assert client.post(path).status_code == 401
    assert client.get("/api/session").status_code == 401


def test_logout_failure_is_sanitized(api):
    client, miner = api
    miner.logout.side_effect = OSError("private credential path")
    response = client.post("/api/session/logout")
    assert response.status_code == 503
    assert response.json() == {"detail": "session_logout_failed"}


@pytest.mark.parametrize("path", ["/api/helper/connect", "/api/helper/session", "/api/helper/result",
                                  "/api/session/export", "/api/session/seed", "/api/session/pair"])
def test_retired_helper_and_credential_routes_do_not_exist(api, path):
    client, miner = api
    assert client.get(path).status_code in (404, 405)
    assert client.post(path, json={}).status_code in (404, 405)


@pytest.mark.parametrize("origin", [None, "https://evil.test", "http://testserver"])
def test_viewer_is_rejected_without_current_interactive_browser(api, origin):
    client, miner = api
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect("/api/session/vnc", headers={"Origin": origin} if origin else {}):
            pass


@pytest.mark.asyncio
@pytest.mark.parametrize("ending", ["revoked", "attempt", "verifying", "text", "oversize"])
async def test_vnc_bridges_binary_frames_then_disconnects_and_drains(tmp_path, ending):
    received, closed = asyncio.Event(), asyncio.Event()
    input_bytes = []

    async def vnc(reader, writer):
        try:
            writer.write(b"RFB 003.008\n")
            await writer.drain()
            input_bytes.append(await reader.readexactly(3))
            received.set()
            await reader.read()
        finally:
            writer.close()
            await writer.wait_closed()
            closed.set()

    server = await asyncio.start_server(vnc, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    browser = SimpleNamespace(state="sign_in", attempt=1, desktop=SimpleNamespace(port=port))
    auth = WebAuth(tmp_path / "auth.json")
    allowed = [True]
    auth.allowed = lambda token: allowed[0]
    api = SessionAPI(auth, lambda: SimpleNamespace(login_browser=browser))
    queue, output = asyncio.Queue(), asyncio.Queue()

    async def send(message):
        await output.put(message)

    scope = {"type": "websocket", "path": "/api/session/vnc", "scheme": "ws", "query_string": b"",
             "server": ("testserver", 80), "client": ("local", 1),
             "headers": [(b"host", b"testserver"), (b"origin", b"http://testserver")]}
    websocket = WebSocket(scope, queue.get, send)
    await queue.put({"type": "websocket.connect"})
    task = asyncio.create_task(api.viewer(websocket))
    try:
        assert (await asyncio.wait_for(output.get(), 1))["type"] == "websocket.accept"
        assert (await asyncio.wait_for(output.get(), 1))["bytes"] == b"RFB 003.008\n"
        await queue.put({"type": "websocket.receive", "bytes": b"abc"})
        await asyncio.wait_for(received.wait(), 1)
        if ending == "revoked":
            allowed[0] = False
        elif ending == "attempt":
            browser.attempt += 1
        elif ending == "verifying":
            browser.state = "verifying"
        else:
            await queue.put({"type": "websocket.receive", **({"text": "invalid"} if ending == "text" else {"bytes": b"x" * 65537})})
        await asyncio.wait_for(task, 1)
        await asyncio.wait_for(closed.wait(), 1)
        assert input_bytes == [b"abc"]
        assert api._viewers == 0
        assert (await output.get())["code"] == 1008
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        server.close()
        await server.wait_closed()


@pytest.mark.asyncio
async def test_public_proxy_origin_is_required_even_when_dashboard_auth_is_disabled(tmp_path):
    auth = WebAuth(tmp_path / "auth.json", public_base_url="https://drops.example.com")
    api = SessionAPI(auth, lambda: None)
    calls = []
    for origin in (None, "http://backend:8080", "https://evil.example"):
        socket = SimpleNamespace(scope={"type": "websocket", "headers": []}, headers={"origin": origin}, close=AsyncMock())
        await api.viewer(socket)
        socket.close.assert_awaited_once_with(code=1008)
        calls.append(socket.close.await_count)
    assert calls == [1, 1, 1]
