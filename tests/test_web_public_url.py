"""Public-origin regressions with real HTTP/Socket.IO guards and isolated auth state."""

import asyncio
import importlib
import json
import os
import subprocess
import sys
from collections import deque
from contextlib import contextmanager

import pytest
from fastapi.testclient import TestClient
from starlette.requests import HTTPConnection
from starlette.websockets import WebSocketDisconnect
from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware

from src.web.auth import WebAuth
from src.web.origin import DashboardOrigin


web = importlib.import_module("src.web.app")
PUBLIC = "https://drops.example.com"
POLLING = "/socket.io/?EIO=4&transport=polling"
WEBSOCKET = "/socket.io/?EIO=4&transport=websocket"
PASSWORD = "isolated test password"
SETUP = {"action": "enable", "password": PASSWORD, "confirm_password": PASSWORD}


@pytest.fixture
def public_client(tmp_path, monkeypatch):
    auth = web.web_auth
    for name, value in {"path": tmp_path / "auth.json", "password_hash": "", "sessions": {},
                        "lock": asyncio.Lock(), "attempts": deque()}.items():
        monkeypatch.setattr(auth, name, value)
    monkeypatch.setattr(web.sio, "tokens", {})
    monkeypatch.setattr(web.sio, "expirations", {})
    monkeypatch.setattr(web, "gui_manager", None)
    monkeypatch.setattr(web, "twitch_client", None)

    @contextmanager
    def create(url=PUBLIC, base_url="http://backend:8080", origin=PUBLIC, trusted_hosts="127.0.0.1"):
        monkeypatch.setattr(auth, "origin", DashboardOrigin(url))
        proxy = ProxyHeadersMiddleware(web.socket_app, trusted_hosts=trusted_hosts)
        with TestClient(proxy, base_url=base_url, client=("172.19.0.9", 1234),
                        headers={"Origin": origin, "X-TDM-Request": "1"}) as browser:
            yield browser

    return create


@pytest.mark.parametrize("value,expected", [
    ("https://drops.example.com", PUBLIC),
    ("HTTPS://Drops.Example.Com:443/", PUBLIC),
    ("http://localhost:80/", "http://localhost"),
    ("https://drops.example.com:8443/", "https://drops.example.com:8443"),
    ("http://192.168.1.2:8080", "http://192.168.1.2:8080"),
    ("http://192.168.1.2.:8080/", "http://192.168.1.2:8080"),
    ("https://[2001:db8::1]:8443/", "https://[2001:db8::1]:8443"),
    ("https://[2001:0DB8:0:0:0:0:0:1]/", "https://[2001:db8::1]"),
    ("https://[::ffff:192.0.2.1]/", "https://[::ffff:c000:201]"),
    ("https://münich.example/", "https://xn--mnich-kva.example"),
])
def test_public_origin_normalization(value, expected):
    connection = HTTPConnection({"type": "http", "scheme": "http", "headers": []})
    policy = DashboardOrigin(value)
    assert policy.expected(connection) == expected
    assert policy.secure_cookie(connection) is expected.startswith("https://")


@pytest.mark.parametrize("value", [
    " ", "drops.example.com", "//drops.example.com", "https://", "https:///drops.example.com",
    "ftp://drops.example.com", "wss://drops.example.com", "https://*.example.com",
    "https://drops.example.com/path", "https://drops.example.com/.", "https://drops.example.com//",
    "https://drops.example.com?", "https://drops.example.com?foo=bar", "https://drops.example.com#",
    "https://drops.example.com#fragment", "https://user:secret@drops.example.com",
    "https://user@drops.example.com", "https://@drops.example.com", "https://drops.example.com:",
    "https://drops.example.com:0", "https://drops.example.com:65536", "https://drops.example.com:abc",
    "https://drops.example.com:١٢٣", "https://[::1]suffix", "https://[::1", "https://[not-ipv6]",
    "https://drops.example.com,https://other.example.com", "https://drops.example.com other.example.com",
    " https://drops.example.com", "https://drops.example.com\n", "https://drop\ts.example.com",
    "https://drops.example.com\x00", "https://drops.example.com\\evil", "https://drops%2eexample.com",
    "https://drops.example.com/%2e",
    "http://127.1", "http://0177.0.0.1", "http://0x7f.0.0.1", "http://2130706433",
    "http://1.2.3.256", "http://example.123", "http://0x7f000001", "http://1.2.3.0x",
    "https://a\u200cb.example", "https://a\u200db.example",
])
def test_invalid_public_url_fails_closed_without_echoing_value(value, tmp_path):
    with pytest.raises(ValueError, match="^PUBLIC_BASE_URL must be") as error:
        WebAuth(tmp_path / "auth.json", public_base_url=value)
    if value.strip():
        assert value not in str(error.value)
    assert "secret" not in str(error.value)
    assert not (tmp_path / "auth.json").exists()


@pytest.mark.parametrize("scheme,host", [("http", "localhost:8080"), ("https", "drops.example.com"),
                                        ("ws", "localhost:8080"), ("wss", "drops.example.com")])
def test_empty_public_url_preserves_request_derived_policy(scheme, host):
    connection = HTTPConnection({"type": "http", "scheme": scheme,
                                 "headers": [(b"host", host.encode())]})
    policy = DashboardOrigin("")
    secure = scheme in ("https", "wss")
    assert policy.expected(connection) == f"{'https' if secure else 'http'}://{host}"
    assert policy.secure_cookie(connection) is secure


@pytest.mark.parametrize("url,base,origin,secure", [
    (PUBLIC, "http://backend:8080", PUBLIC, True),
    ("http://drops.example.com", "http://backend:8080", "http://drops.example.com", False),
    ("", "https://drops.example.com", PUBLIC, True),
    ("", "http://backend:8080", "http://backend:8080", False),
])
def test_cookie_lifecycle_uses_public_scheme(public_client, url, base, origin, secure):
    with public_client(url, base, origin) as client:
        setup = client.post("/api/auth/settings", json=SETUP)
        assert setup.status_code == 200
        assert ("Secure" in setup.headers["set-cookie"]) is secure
        cookie = setup.headers["set-cookie"]
        assert "HttpOnly" in cookie and "SameSite=strict" in cookie and "Path=/" in cookie
        token = client.cookies.get(WebAuth.COOKIE)
        # A real proxy forwards a browser's HTTPS cookie to its HTTP backend.
        client.cookies.clear()
        client.headers["Cookie"] = f"{WebAuth.COOKIE}={token}"
        assert client.get("/api/settings").status_code != 401
        assert client.get(POLLING).status_code == 200
        websocket_url = base.replace("http", "ws", 1) + WEBSOCKET
        with client.websocket_connect(websocket_url, headers={"Upgrade": "websocket"}) as connection:
            assert connection.receive_text().startswith("0")
            connection.send_text("40")
            assert connection.receive_text().startswith("40")
            logout = client.post("/api/auth/logout")
            assert logout.status_code == 200
            assert "Max-Age=0" in logout.headers["set-cookie"]
            assert ("Secure" in logout.headers["set-cookie"]) is secure
            assert connection.receive_text() == "41"
        assert client.get(POLLING).status_code == 401
        client.headers.pop("Cookie")
        response = client.post("/api/auth/login", json={"password": PASSWORD, "remember": True})
        assert response.status_code == 200
        assert ("Secure" in response.headers["set-cookie"]) is secure
        assert "Max-Age=2592000" in response.headers["set-cookie"]


@pytest.mark.parametrize("headers", [
    {"Origin": "https://evil.example"}, {"Origin": "null"}, {"Origin": "http://drops.example.com"},
    {"Origin": "https://drops.example.com:8443"}, {"Origin": "http://backend:8080"},
    {"Origin": "https://evil.example", "Host": "evil.example", "X-Forwarded-Host": "evil.example",
     "X-Forwarded-Proto": "https"}, {"Sec-Fetch-Site": "cross-site"},
])
def test_public_url_keeps_http_and_socket_origin_guards(public_client, headers):
    with public_client() as client:
        assert client.post("/api/auth/settings", json=SETUP, headers=headers).status_code == 403
        assert not web.web_auth.enabled
        assert client.get(POLLING, headers=headers).status_code == 403
        with pytest.raises(WebSocketDisconnect) as error, client.websocket_connect(
            WEBSOCKET, headers={**headers, "Upgrade": "websocket"}
        ):
            pass
        assert error.value.code == 1008


def test_marker_remains_required_only_for_http_writes(public_client):
    with public_client() as client:
        client.headers.pop("X-TDM-Request")
        assert client.post("/api/auth/settings", json=SETUP).status_code == 403
        assert client.get(POLLING).status_code == 200
        with client.websocket_connect(WEBSOCKET, headers={"Upgrade": "websocket"}) as connection:
            assert connection.receive_text().startswith("0")
            connection.send_text("40")
            assert connection.receive_text().startswith("40")


def test_public_origin_does_not_bypass_authentication(public_client):
    with public_client() as client:
        assert client.post("/api/auth/settings", json=SETUP).status_code == 200
        client.cookies.clear()
        assert client.get(POLLING).status_code == 401
        with pytest.raises(WebSocketDisconnect) as error, client.websocket_connect(WEBSOCKET):
            pass
        assert error.value.code == 1008
        assert client.post("/api/auth/login", json={"password": "wrong"}).status_code == 401


def test_public_url_does_not_trust_forwarded_client_ips(public_client):
    with public_client() as client:
        assert client.post("/api/auth/settings", json=SETUP).status_code == 200
        web.web_auth.attempts.clear()
        for index in range(5):
            response = client.post("/api/auth/login", json={"password": "wrong"},
                                   headers={"X-Forwarded-For": f"192.0.2.{index}",
                                            "X-Forwarded-Proto": "http"})
            assert response.status_code == 401
        assert {peer for _, peer in web.web_auth.attempts} == {"172.19.0.9"}
        assert client.post("/api/auth/login", json={"password": PASSWORD},
                           headers={"X-Forwarded-For": "192.0.2.99"}).status_code == 429


def test_unset_public_url_still_requires_trusted_proxy_for_https_origin(public_client):
    with public_client(url="", base_url="http://drops.example.com") as client:
        assert client.get(POLLING, headers={"X-Forwarded-Proto": "https"}).status_code == 403
    with public_client(url="", base_url="http://drops.example.com", trusted_hosts="172.19.0.9") as client:
        assert client.get(POLLING, headers={"X-Forwarded-Proto": "https"}).status_code == 200


@pytest.mark.parametrize("public,serialized", [
    ("https://[::ffff:192.0.2.1]", "https://[::ffff:c000:201]"),
    ("http://192.0.2.1.", "http://192.0.2.1"),
])
def test_browser_serialized_ip_origins_can_connect_and_write(public_client, public, serialized):
    with public_client(url=public, origin=serialized) as client:
        assert client.get(POLLING).status_code == 200
        with client.websocket_connect(WEBSOCKET, headers={"Upgrade": "websocket"}) as connection:
            assert connection.receive_text().startswith("0")
        assert client.post("/api/auth/settings", json=SETUP).status_code == 200


def test_invalid_environment_stops_application_construction(tmp_path):
    script = """
import sys
from pathlib import Path
import src.config.paths
src.config.paths.DATA_DIR = Path(sys.argv[1])
try:
    import src.web.app
except ValueError as error:
    print(error)
    sys.exit(3)
"""
    result = subprocess.run([sys.executable, "-c", script, str(tmp_path)],
                            env={**os.environ, "PUBLIC_BASE_URL": "https://user:secret@example.com"},
                            capture_output=True, text=True, timeout=20)
    assert result.returncode == 3
    assert result.stdout.startswith("PUBLIC_BASE_URL must be")
    assert "secret" not in result.stdout + result.stderr


def test_public_url_environment_restores_production_socket_transports(tmp_path):
    """Issue #106: public HTTPS, internal HTTP/Host, and an untrusted proxy peer."""
    script = """
import importlib
import json
import sys
from pathlib import Path
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect
from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware
import src.config.paths
src.config.paths.DATA_DIR = Path(sys.argv[1])
web = importlib.import_module('src.web.app')
proxy = ProxyHeadersMiddleware(web.socket_app, trusted_hosts='127.0.0.1')
headers = {'Origin': 'https://drops.example.com', 'X-Forwarded-Proto': 'https'}
results = {}
with TestClient(proxy, base_url='http://backend:8080',
                client=('172.19.0.9', 1234), headers=headers) as client:
    results['polling'] = client.get('/socket.io/?EIO=4&transport=polling').status_code
    try:
        with client.websocket_connect('/socket.io/?EIO=4&transport=websocket',
                                      headers={'Upgrade': 'websocket'}) as connection:
            results['websocket'] = connection.receive_text().startswith('0')
            connection.send_text('40')
            results['namespace'] = connection.receive_text().startswith('40')
    except WebSocketDisconnect as error:
        results['websocket'] = error.code
print(json.dumps(results))
"""
    result = subprocess.run(
        [sys.executable, "-c", script, str(tmp_path)],
        env={**os.environ, "PUBLIC_BASE_URL": "https://drops.example.com"},
        capture_output=True, text=True, timeout=20, check=True,
    )
    assert json.loads(result.stdout) == {"polling": 200, "websocket": True, "namespace": True}
