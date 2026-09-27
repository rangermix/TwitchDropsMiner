"""Drive the actual local-export protocol against an isolated DevTools peer."""

import json
from contextlib import asynccontextmanager

import pytest
from aiohttp import web

from src.auth.session_bundle import SessionError
from src.auth.session_helper import BrowserExporter
from src.config import ClientType


@asynccontextmanager
async def devtools(*, matching=True, anonymous=False, fail_campaign=False, preflight=False, sdk_cookie=True, sdk_expiry=9000, integrity_expiry=4600, null_cookie_reply=False):
    closed = []
    commands = []
    address = []

    async def create(request):
        assert request.query_string == "about:blank"
        return web.json_response({"id": "owned-tab", "webSocketDebuggerUrl": address[0].replace("http:", "ws:") + "/devtools/page/owned-tab"})

    async def close(request):
        closed.append(request.match_info["target"])
        return web.Response(text="Target is closing")

    async def socket(request):
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        async for message in ws:
            command = message.json()
            method, params = command["method"], command.get("params", {})
            commands.append(method)
            result = {}
            if method == "Runtime.evaluate":
                result = {"result": {"value": "Test Chrome"}}
            elif method == "Network.getCookies":
                assert params == {"urls": ["https://k.twitchcdn.net/"]}
                result = {"cookies": [{
                    "name": "KP_UIDz-ssn", "value": "private-sdk-cookie", "expires": sdk_expiry,
                    "domain": "k.twitchcdn.net", "path": "/", "secure": True, "httpOnly": True,
                }] if sdk_cookie else []}
                if null_cookie_reply:
                    result = None
            elif method == "Network.getResponseBody":
                if params["requestId"] == "preflight":
                    await ws.send_json({"id": command["id"], "result": {"body": "", "base64Encoded": False}})
                    continue
                elif params["requestId"] == "issued":
                    body = {"token": "new-integrity", "expiration": integrity_expiry * 1000}
                elif fail_campaign:
                    body = [{"errors": [{"message": "failed integrity check"}]}]
                else:
                    body = [{"data": {"currentUser": {"dropCampaigns": [{"id": "1"}]}}}]
                result = {"body": json.dumps(body), "base64Encoded": False}
            await ws.send_json({"id": command["id"], "result": result})
            if method == "Page.navigate":
                headers = {
                    "authorization": "OAuth test-token", "client-id": ClientType.WEB.CLIENT_ID,
                    "client-integrity": "new-integrity" if matching else "unrelated-integrity",
                    "x-device-id": "test-device", "cookie": "must-not-export",
                }
                if anonymous:
                    headers.pop("authorization")
                events = [
                    ("Network.responseReceived", {"requestId": "issued", "response": {"url": "https://gql.twitch.tv/integrity", "status": 200}}),
                    ("Network.loadingFinished", {"requestId": "issued"}),
                    ("Network.requestWillBeSent", {"requestId": "catalog", "request": {"url": "https://gql.twitch.tv/gql", "headers": headers}}),
                    ("Network.responseReceived", {"requestId": "catalog", "response": {"url": "https://gql.twitch.tv/gql", "status": 200}}),
                    ("Network.loadingFinished", {"requestId": "catalog"}),
                ]
                if preflight:
                    events[:0] = [
                        ("Network.responseReceived", {"requestId": "preflight", "type": "Preflight", "response": {"url": "https://gql.twitch.tv/integrity", "status": 200}}),
                        ("Network.loadingFinished", {"requestId": "preflight"}),
                    ]
                for name, event in events:
                    await ws.send_json({"method": name, "params": event})
        return ws

    app = web.Application()
    app.router.add_put("/json/new", create)
    app.router.add_get("/json/close/{target}", close)
    app.router.add_get("/devtools/page/owned-tab", socket)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    address.append(f"http://127.0.0.1:{site._server.sockets[0].getsockname()[1]}")
    try:
        yield address[0], closed, commands
    finally:
        await runner.cleanup()


def test_export_browser_address_must_be_loopback_without_credentials():
    for address in (
        "http://remote.test:9222", "http://user:secret@localhost:9222", "http://localhost:9222/path",
        "http://127.1:9222", "http://localhost:9222?token=secret", "file:///tmp/browser",
    ):
        with pytest.raises(SessionError, match="BROWSER_ADDRESS") as error:
            BrowserExporter(address)
        assert "secret" not in str(error.value)


@pytest.mark.asyncio
async def test_export_correlates_actual_success_and_closes_only_its_tab():
    async with devtools() as (address, closed, commands):
        exporter = BrowserExporter(address, clock=lambda: 1000, timeout=1)
        bundle = await exporter.capture()
        assert bundle.headers["client-integrity"] == "new-integrity"
        assert bundle.headers["authorization"] == "OAuth test-token"
        assert "cookie" not in bundle.headers
        assert bundle.expires_at == 4600
        assert bundle.user_agent == "Test Chrome"
        assert closed == ["owned-tab"]
        assert "Browser.close" not in commands


@pytest.mark.asyncio
async def test_empty_cors_preflight_is_not_treated_as_an_integrity_issuance():
    async with devtools(preflight=True) as (address, closed, _commands):
        bundle = await BrowserExporter(address, clock=lambda: 1000, timeout=1).capture()
        assert bundle.headers["client-integrity"] == "new-integrity"
        assert closed == ["owned-tab"]


@pytest.mark.asyncio
async def test_server_seed_export_collects_only_scoped_cookie_and_closes_target():
    async with devtools() as (address, closed, commands):
        seed = await BrowserExporter(address, clock=lambda: 1000, timeout=1).capture_seed()
        assert seed.bundle.headers["client-integrity"] == "new-integrity"
        assert seed.cookie.value == "private-sdk-cookie"
        assert "cookie" not in seed.bundle.headers
        assert commands.count("Network.getCookies") == 1
        assert closed == ["owned-tab"]


@pytest.mark.asyncio
async def test_server_seed_export_missing_cookie_does_not_export_without_bootstrap(monkeypatch):
    from unittest.mock import AsyncMock

    # The isolated recovery service is unavailable in this peer. It must still fail
    # closed, close the ordinary target, and never return a cookie-less seed.
    monkeypatch.setattr("src.auth.imported_session.SessionTransport.validate", AsyncMock(side_effect=SessionError("SDK_COOKIE")))
    async with devtools(sdk_cookie=False) as (address, closed, _commands):
        with pytest.raises(SessionError, match="SDK_COOKIE"):
            await BrowserExporter(address, clock=lambda: 1000, timeout=1).capture_seed()
        assert closed == ["owned-tab"]


@pytest.mark.asyncio
async def test_server_seed_export_normalizes_malformed_cookie_reply_and_closes_target():
    async with devtools(null_cookie_reply=True) as (address, closed, _commands):
        with pytest.raises(SessionError, match="BROWSER_PROTOCOL"):
            await BrowserExporter(address, clock=lambda: 1000, timeout=1).capture_seed()
        assert closed == ["owned-tab"]


@pytest.mark.asyncio
@pytest.mark.parametrize("options", [{"matching": False}, {"anonymous": True}, {"fail_campaign": True}])
async def test_export_refuses_unmatched_anonymous_or_rejected_context(options):
    async with devtools(**options) as (address, closed, _commands):
        with pytest.raises(SessionError, match="CAPTURE_TIMEOUT"):
            await BrowserExporter(address, clock=lambda: 1000, timeout=.05).capture()
        assert closed == ["owned-tab"]
