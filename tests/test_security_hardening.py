"""Regressions for the dashboard security audit: rebinding, leaks, limits and headers."""

import asyncio
import importlib
import logging
import re
from collections import deque
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import aiohttp
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from starlette.requests import HTTPConnection
from starlette.websockets import WebSocketDisconnect

from src.services.telegram_service import TelegramNotifier
from src.utils import PASSWORD_MASK as PROXY_PASSWORD_MASK
from src.utils import mask_url_password
from src.web.managers.settings import SettingsManager
from src.web.origin import DashboardOrigin


web = importlib.import_module("src.web.app")
REBOUND = "http://attacker.example:8080"
HEADERS = {"X-TDM-Request": "1"}


@pytest.fixture
def isolated(tmp_path, monkeypatch):
    auth = web.web_auth
    for name, value in {"path": tmp_path / "web_auth.json", "password_hash": "", "sessions": {},
                        "lock": asyncio.Lock(), "attempts": deque(),
                        "origin": DashboardOrigin()}.items():
        monkeypatch.setattr(auth, name, value)
    monkeypatch.setattr(web.helper_api, "attempts", deque())
    monkeypatch.setattr(web.sio, "tokens", {})
    monkeypatch.setattr(web.sio, "expirations", {})
    monkeypatch.setattr(web, "gui_manager", None)
    monkeypatch.setattr(web, "twitch_client", None)

    def create(base_url="http://192.168.1.20:8080"):
        origin = base_url.rstrip("/")
        return TestClient(web.socket_app, base_url=base_url,
                          headers={**HEADERS, "Origin": origin, "Sec-Fetch-Site": "same-origin"})

    return create


def host(value: str) -> HTTPConnection:
    return HTTPConnection({"type": "http", "headers": [(b"host", value.encode())] if value else []})


class TestHostAllowlist:
    @pytest.mark.parametrize("value", [
        "127.0.0.1:8080", "192.168.1.20", "[::1]:8080", "[2001:db8::1]", "localhost:8080",
        "LOCALHOST", "dash.localhost", "nas", "twitch-drops-miner:8080", "testserver",
        "nas.local", "miner.home.arpa", "miner.internal", "localhost.",
    ])
    def test_local_names_and_ip_literals_are_trusted(self, value):
        assert DashboardOrigin().trusted_host(host(value))

    @pytest.mark.parametrize("value", [
        "attacker.example:8080", "127.0.0.1.attacker.example", "attacker.example.", "nas.lan",
        "evil.example@127.0.0.1", "[not-an-ip]", "[::1", "", ".local", "nas..local",
    ])
    def test_public_or_malformed_names_are_rejected(self, value):
        assert not DashboardOrigin().trusted_host(host(value))

    def test_configured_names_are_trusted(self):
        origin = DashboardOrigin("https://drops.example.com", " Nas.Lan , miner.example.org. ")
        for value in ("drops.example.com", "nas.lan:8080", "miner.example.org"):
            assert origin.trusted_host(host(value))
        assert not origin.trusted_host(host("attacker.example"))

    @pytest.mark.parametrize("value", ["http://nas.lan", "nas.lan:8080", "nas lan", "*.example.com"])
    def test_invalid_allowed_hosts_stop_without_echo(self, value):
        with pytest.raises(ValueError) as error:
            DashboardOrigin(allowed_hosts=value)
        assert value not in str(error.value)

    def test_rebound_host_cannot_drive_the_api(self, isolated, monkeypatch):
        closed = MagicMock()
        monkeypatch.setattr(web, "twitch_client", SimpleNamespace(close=closed))
        with isolated(REBOUND) as client:
            for method, path in (("post", "/api/close"), ("get", "/api/settings"),
                                 ("post", "/api/helper/connect"), ("get", "/static/app.js"),
                                 ("get", "/socket.io/?EIO=4&transport=polling")):
                response = getattr(client, method)(path)
                assert response.status_code == 403
                assert response.json() == {"detail": "invalid_host"}
            with pytest.raises(WebSocketDisconnect), client.websocket_connect("/api/session/vnc"):
                pass
            # Liveness alone is harmless, so monitors may use any configured name.
            assert client.get("/healthz").json() == {"status": "ok"}
        closed.assert_not_called()

    def test_rejection_warning_is_rate_limited_not_one_shot(self, isolated, monkeypatch, caplog):
        monkeypatch.setattr(web.AuthMiddleware, "host_warned_at", float("-inf"))
        with caplog.at_level(logging.WARNING, logger="TwitchDrops"), isolated(REBOUND) as client:
            client.get("/api/settings")
            client.get("/api/settings")
            assert caplog.text.count("unrecognized host name") == 1
            # A minute later another rejection is logged again.
            web.AuthMiddleware.host_warned_at -= 61
            client.get("/api/settings")
        assert caplog.text.count("unrecognized host name") == 2


class TestRateLimitBuckets:
    def test_helper_connections_cannot_exhaust_password_logins(self, isolated):
        with isolated() as client:
            for _ in range(6):
                client.post("/api/helper/connect", json={})
            assert client.post("/api/helper/connect", json={}).status_code == 429
            web.web_auth.password_hash = web.web_auth.hash_password("isolated password")
            response = client.post("/api/auth/login", json={"password": "wrong password"})
            assert response.status_code == 401

    def test_each_bucket_keeps_its_own_ceiling(self):
        request = SimpleNamespace(client=SimpleNamespace(host="192.168.1.30"))
        other: deque = deque()
        for _ in range(5):
            web.web_auth.limit(request, other)
        with pytest.raises(HTTPException) as error:
            web.web_auth.limit(request, other)
        assert error.value.status_code == 429
        assert all(ip != "192.168.1.30" for _, ip in web.web_auth.attempts)


class TestProxySecret:
    def manager(self, proxy):
        return SettingsManager(MagicMock(emit=AsyncMock()), SimpleNamespace(proxy=proxy, save=MagicMock()),
                               MagicMock())

    @pytest.mark.parametrize("proxy,masked", [
        ("http://user:secret@proxy.lan:3128", f"http://user:{PROXY_PASSWORD_MASK}@proxy.lan:3128"),
        ("http://user@proxy.lan:3128", "http://user@proxy.lan:3128"),
        ("http://proxy.lan:3128", "http://proxy.lan:3128"),
        ("socks5://u:p:w@[::1]:1080", f"socks5://u:{PROXY_PASSWORD_MASK}@[::1]:1080"),
        ("http://u:p@[::1", PROXY_PASSWORD_MASK),
        ("", ""),
    ])
    def test_mask_hides_only_the_password(self, proxy, masked):
        assert mask_url_password(proxy) == masked

    @pytest.mark.asyncio
    async def test_settings_and_logs_never_expose_the_password(self):
        manager = self.manager("http://user:secret@proxy.lan:3128")
        manager._settings.__dict__.update(discord_webhook_url="", telegram_bot_token="")
        assert "secret" not in str(manager.get_settings())
        manager.update_settings({"proxy": "http://user:new-secret@proxy.lan:3128"})
        assert "new-secret" not in str(manager._console.print.call_args_list)

    @pytest.mark.parametrize("edited", [
        # Reusing the stored password for another host would leak it; saving the mask
        # itself would make aiohttp fail to encode the credential on every request.
        f"http://user:{PROXY_PASSWORD_MASK}@attacker.example:3128",
        f"http://user:{PROXY_PASSWORD_MASK}@proxy.lan:3129",
    ])
    def test_only_the_exact_mask_maps_back_to_the_stored_url(self, edited):
        stored = "http://user:secret@proxy.lan:3128"
        manager = self.manager(stored)
        assert manager.resolve_proxy(mask_url_password(stored)) == stored
        assert manager.resolve_proxy("http://user:new@proxy.lan:3129") == "http://user:new@proxy.lan:3129"
        with pytest.raises(ValueError):
            manager.resolve_proxy(edited)
        with pytest.raises(ValueError):
            manager.update_settings({"proxy": edited})
        assert manager._settings.proxy == stored

    def test_rejected_proxy_leaves_no_partial_update(self):
        stored = "http://user:secret@proxy.lan:3128"
        manager = SettingsManager(MagicMock(emit=AsyncMock()), SimpleNamespace(
            proxy=stored, games_to_watch=["Old"], dark_mode=False, save=MagicMock()), MagicMock())
        with pytest.raises(ValueError):
            manager.update_settings({"games_to_watch": ["A", "B"], "dark_mode": True,
                                     "proxy": f"http://user:{PROXY_PASSWORD_MASK}@proxy.lan:3129"})
        settings = manager._settings
        assert (settings.games_to_watch, settings.dark_mode, settings.proxy) == (["Old"], False, stored)
        manager._console.print.assert_not_called()
        settings.save.assert_not_called()

    @pytest.mark.asyncio
    async def test_websocket_connect_log_masks_the_proxy(self, caplog):
        from src.websocket.websocket import Websocket

        stored = "http://user:secret@proxy.lan:3128"
        session = SimpleNamespace(ws_connect=MagicMock(side_effect=RuntimeError))
        socket = Websocket.__new__(Websocket)
        socket._idx = 0
        socket._twitch = SimpleNamespace(settings=SimpleNamespace(proxy=stored),
                                         get_session=AsyncMock(return_value=session))
        with caplog.at_level(logging.INFO, logger="TwitchDrops.websocket"):
            async for _ in socket._backoff_connect("wss://example.invalid"):
                pass
        assert session.ws_connect.call_args.kwargs["proxy"] == stored
        assert "secret" not in caplog.text
        assert f"user:{PROXY_PASSWORD_MASK}@proxy.lan" in caplog.text

    def test_verify_proxy_uses_stored_url_and_hides_exception_text(self, isolated, monkeypatch):
        stored = "http://user:secret@proxy.lan:3128"
        monkeypatch.setattr(web, "gui_manager", SimpleNamespace(settings=self.manager(stored)))
        used = []

        def refuse(*_args, **kwargs):
            used.append(kwargs["proxy"])
            raise aiohttp.ClientConnectionError("Cannot connect to host 10.0.0.5:22")

        monkeypatch.setattr(aiohttp.ClientSession, "get", refuse)
        with isolated() as client:
            response = client.post("/api/settings/verify-proxy",
                                   json={"proxy": mask_url_password(stored)})
        assert used == [stored]
        assert response.json() == {"success": False, "message": "Connection failed"}


class TestTelegramSecret:
    TOKEN = "123456:secret-token"

    def test_test_endpoint_does_not_echo_exception_text(self, isolated):
        with patch.object(TelegramNotifier, "test_connection",
                          side_effect=RuntimeError(f"https://api.telegram.org/bot{self.TOKEN}/")), \
                isolated() as client:
            response = client.post("/api/settings/test-telegram",
                                   json={"telegram_bot_token": self.TOKEN, "telegram_chat_id": "1"})
        assert self.TOKEN not in response.text
        assert response.json()["success"] is False

    @pytest.mark.asyncio
    async def test_transport_errors_do_not_log_the_bot_url(self, caplog):
        url = f"https://api.telegram.org/bot{self.TOKEN}/sendMessage"
        with patch("aiohttp.ClientSession.post", side_effect=aiohttp.InvalidURL(url)), \
                caplog.at_level(logging.WARNING, logger="TwitchDrops"):
            assert await TelegramNotifier(self.TOKEN, "1").test_connection() is False
        assert self.TOKEN not in caplog.text
        assert "InvalidURL" in caplog.text


class TestResponseHeaders:
    def test_pages_and_api_carry_csp_and_nosniff(self, isolated):
        with isolated() as client:
            for path in ("/", "/login", "/api/auth/status"):
                headers = client.get(path).headers
                assert "frame-ancestors 'none'" in headers["content-security-policy"]
                assert "script-src 'self' https://cdn.socket.io;" in headers["content-security-policy"]
                assert headers["x-content-type-options"] == "nosniff"

    def test_static_files_carry_nosniff(self, isolated):
        with isolated() as client:
            assert client.get("/static/app.js").headers["x-content-type-options"] == "nosniff"

    def test_novnc_viewer_keeps_its_upstream_policy(self, isolated):
        with isolated() as client:
            headers = client.get("/session-viewer").headers
        assert "content-security-policy" not in headers
        assert headers["x-frame-options"] == "SAMEORIGIN"

    def test_every_external_script_is_pinned_by_integrity(self):
        page = (Path(__file__).resolve().parents[1] / "web" / "index.html").read_text(encoding="utf-8")
        external = re.findall(r"<script[^>]+src=\"https://[^>]+>", page)
        assert external
        assert all(re.search(r'integrity="sha384-[A-Za-z0-9+/=]{64}"', tag)
                   and 'crossorigin="anonymous"' in tag for tag in external)
