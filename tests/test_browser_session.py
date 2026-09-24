"""Browser transport contracts with isolated credentials and no live Twitch traffic."""

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.auth.browser_session import BrowserConfig, BrowserSession
from src.config import ClientType
from src.exceptions import LoginException


def configured(tmp_path):
    return BrowserSession(
        BrowserConfig("http://browser:4444", "http://localhost:7900/vnc.html"),
        tmp_path / "browser-session.json",
    )


def request_log(token="test-token", url="https://gql.twitch.tv/gql", **extra):
    return {
        "message": json.dumps(
            {
                "message": {
                    "method": "Network.requestWillBeSent",
                    "params": {
                        "request": {
                            "url": url,
                            "method": "POST",
                            "headers": {
                                "Client-ID": ClientType.WEB.CLIENT_ID,
                                "Authorization": f"OAuth {token}",
                                "X-Device-Id": "device",
                                "Client-Integrity": "secret-integrity",
                                "Cookie": "must-not-copy",
                                **extra,
                            },
                        }
                    },
                }
            }
        )
    }


def test_browser_configuration_requires_both_urls_and_safe_viewer(monkeypatch):
    monkeypatch.delenv("TDM_BROWSER_URL", raising=False)
    monkeypatch.delenv("TDM_BROWSER_VIEWER_URL", raising=False)
    assert BrowserConfig.from_env() is None
    monkeypatch.setenv("TDM_BROWSER_URL", "http://browser:4444")
    with pytest.raises(LoginException, match="BROWSER_CONFIG"):
        BrowserConfig.from_env()
    for url in (
        "javascript:alert(1)",
        "https://user:secret@example.com",
        "http://:secret@example.com",
        "//example.com",
    ):
        monkeypatch.setenv("TDM_BROWSER_VIEWER_URL", url)
        with pytest.raises(LoginException, match="BROWSER_CONFIG"):
            BrowserConfig.from_env()

    monkeypatch.setenv("TDM_BROWSER_VIEWER_URL", "http://localhost:7900")
    monkeypatch.setenv("TDM_BROWSER_URL", "http://:secret@browser:4444")
    with pytest.raises(LoginException, match="BROWSER_CONFIG"):
        BrowserConfig.from_env()


@pytest.mark.asyncio
async def test_only_matching_twitch_request_context_is_kept(tmp_path):
    browser = configured(tmp_path)
    browser._session_id = "test-session"
    browser._command = AsyncMock(
        return_value=[
            request_log(url="https://evil.test/gql"),
            request_log(token="other-account"),
            request_log(),
        ]
    )
    await browser._refresh_headers("test-token")
    assert browser._headers["authorization"] == "OAuth test-token"
    assert browser._headers["client-integrity"] == "secret-integrity"
    assert "cookie" not in browser._headers
    assert "secret-integrity" not in repr(browser)


@pytest.mark.asyncio
async def test_browser_gql_refuses_changed_account_before_sending(tmp_path):
    browser = configured(tmp_path)
    browser._token = "old-account"
    browser._cookies = AsyncMock(return_value={"auth-token": "new-account"})
    browser._fetch = AsyncMock()
    with pytest.raises(LoginException, match="BROWSER_SESSION_CHANGED"):
        await browser.gql({"query": "mutation { claim }"})
    browser._fetch.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "data",
    [
        [
            {"data": {"currentUser": {"inventory": {}}}},
            {"data": {"currentUser": {"dropCampaigns": None}}},
        ],
        [{"data": {"currentUser": None}}, {"data": {"currentUser": {"dropCampaigns": []}}}],
        [{"errors": [{"message": "invalid integrity"}]}],
    ],
)
async def test_login_never_succeeds_with_unusable_catalog(tmp_path, data):
    browser = configured(tmp_path)
    browser.start = AsyncMock()
    browser.close = AsyncMock()
    browser._cookies = AsyncMock(return_value={"auth-token": "test-token"})
    browser._refresh_headers = AsyncMock()
    browser._headers = {"authorization": "OAuth test-token", "x-device-id": "device"}
    browser._fetch = AsyncMock(
        return_value={"client_id": ClientType.WEB.CLIENT_ID, "user_id": "42"}
    )
    browser.gql = AsyncMock(return_value=data)
    browser._command = AsyncMock(return_value="Real browser UA")
    login = SimpleNamespace(browser_pending=AsyncMock())
    with pytest.raises(LoginException, match="BROWSER_CATALOG"):
        await browser.authenticate(login)
    assert login.browser_pending.await_args.args == (None,)
    browser.close.assert_awaited_once()


@pytest.mark.asyncio
async def test_cancelled_interactive_login_clears_prompt_and_closes_browser(tmp_path):
    browser = configured(tmp_path)
    browser.start = AsyncMock()
    browser.close = AsyncMock()
    browser._cookies = AsyncMock(side_effect=asyncio.CancelledError)
    login = SimpleNamespace(browser_pending=AsyncMock())
    with pytest.raises(asyncio.CancelledError):
        await browser.authenticate(login)
    browser.close.assert_awaited_once()
    assert login.browser_pending.await_args.args == (None,)


@pytest.mark.asyncio
async def test_saved_driver_session_is_reused_without_new_browser(tmp_path):
    browser = configured(tmp_path)
    browser.state_path.write_text(
        json.dumps({"session_id": "existing", "endpoint": browser.config.endpoint})
    )
    browser._command = AsyncMock(return_value="https://www.twitch.tv/drops/campaigns")
    await browser.start()
    assert browser._session_id == "existing"
    assert not any(call.args[1] == "/session" for call in browser._command.await_args_list)


@pytest.mark.asyncio
async def test_driver_error_never_exposes_remote_payload(tmp_path):
    from aiohttp import web

    async def error(request):
        return web.json_response(
            {"value": {"error": "unknown error", "message": "secret-token"}}, status=500
        )

    app = web.Application()
    app.router.add_post("/session", error)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]
    browser = BrowserSession(
        BrowserConfig(f"http://127.0.0.1:{port}", "http://localhost:7900"), tmp_path / "state"
    )
    try:
        with pytest.raises(LoginException, match="BROWSER_DRIVER") as failure:
            await browser.start()
        assert "secret-token" not in str(failure.value)
    finally:
        await browser.close()
        await runner.cleanup()


@pytest.mark.asyncio
async def test_dashboard_close_interrupts_pending_login(tmp_path, monkeypatch):
    from unittest.mock import MagicMock
    from src.core.client import Twitch
    from src.exceptions import ExitRequest

    monkeypatch.setattr("src.core.client.DATA_DIR", tmp_path)
    client = Twitch(MagicMock())
    browser = configured(tmp_path)
    client._browser = browser
    browser.start = AsyncMock()
    browser.close = AsyncMock()
    waiting = asyncio.Event()

    async def no_cookie():
        waiting.set()
        await asyncio.Event().wait()

    browser._cookies = no_cookie
    login = SimpleNamespace(browser_pending=AsyncMock())
    task = asyncio.create_task(browser.authenticate(login))
    await waiting.wait()
    client.close()
    with pytest.raises(ExitRequest):
        await asyncio.wait_for(task, 1)
    browser.close.assert_awaited_once()
    assert login.browser_pending.await_args.args == (None,)


@pytest.mark.asyncio
async def test_browser_cannot_replace_known_account(tmp_path, monkeypatch):
    from unittest.mock import MagicMock
    from src.auth.browser_session import BrowserIdentity
    from src.core.client import Twitch

    monkeypatch.setattr("src.core.client.DATA_DIR", tmp_path)
    client = Twitch(MagicMock())
    client.gui = SimpleNamespace(login=SimpleNamespace(update=MagicMock()))
    client._browser = SimpleNamespace(
        authenticate=AsyncMock(return_value=BrowserIdentity(42, "new-token", "device", "Chrome")),
        close=AsyncMock(),
    )
    with pytest.raises(LoginException, match="BROWSER_ACCOUNT_MISMATCH"):
        await client._auth_state._browser_login(expected_user_id=12345)
    assert not client._auth_state._logged_in.is_set()
    client.gui.login.update.assert_not_called()
    client._browser.close.assert_awaited_once()


@pytest.mark.asyncio
async def test_websocket_sends_credential_without_logging_it(caplog):
    from unittest.mock import MagicMock
    from src.websocket.websocket import Websocket

    socket = Websocket(MagicMock(), 0)
    transport = SimpleNamespace(send_json=AsyncMock())
    socket._ws.set(transport)
    with caplog.at_level("DEBUG", logger="TwitchDrops.websocket"):
        await socket.send(
            {"type": "LISTEN", "data": {"topics": [], "auth_token": "private-browser-token"}}
        )
    assert transport.send_json.await_args.args[0]["data"]["auth_token"] == "private-browser-token"
    assert "private-browser-token" not in caplog.text


@pytest.mark.asyncio
@pytest.mark.parametrize("double_stop", [False, True])
async def test_cancel_during_creation_deletes_allocated_browser(tmp_path, double_stop):
    from src.exceptions import ExitRequest
    browser = configured(tmp_path)
    allocated = asyncio.Event()
    respond = asyncio.Event()
    deleted = []

    async def command(method, path, payload=None):
        if method == "POST" and path == "/session":
            allocated.set()
            await respond.wait()
            return {"sessionId": "allocated-before-cancel"}
        if method == "DELETE":
            deleted.append(path)
        return None

    browser._command = command
    login = SimpleNamespace(browser_pending=AsyncMock())
    task = asyncio.create_task(browser.authenticate(login))
    await allocated.wait()
    if double_stop:
        browser.request_stop()
        await asyncio.sleep(0)
        browser.request_stop()
    else:
        task.cancel()
    await asyncio.sleep(0)
    respond.set()
    with pytest.raises(ExitRequest if double_stop else asyncio.CancelledError):
        await task
    assert deleted == ["/session/allocated-before-cancel"]
    assert not browser.state_path.exists()


@pytest.mark.asyncio
async def test_login_checks_identity_and_real_catalog_before_success(tmp_path):
    browser = configured(tmp_path)
    browser._session_id = "test-session"
    browser.start = AsyncMock()
    browser._cookies = AsyncMock(return_value={"auth-token": "test-token"})
    browser._refresh_headers = AsyncMock()
    browser._headers = {
        "authorization": "OAuth test-token",
        "x-device-id": "device",
        "client-id": ClientType.WEB.CLIENT_ID,
    }
    browser._fetch = AsyncMock(
        side_effect=[
            {"client_id": ClientType.WEB.CLIENT_ID, "user_id": "42"},
            [
                {
                    "data": {
                        "currentUser": {
                            "inventory": {"gameEventDrops": [], "dropCampaignsInProgress": []}
                        }
                    }
                },
                {"data": {"currentUser": {"dropCampaigns": []}}},
            ],
        ]
    )
    browser._command = AsyncMock(return_value="Real Chrome UA")
    login = SimpleNamespace(browser_pending=AsyncMock())
    identity = await browser.authenticate(login)
    assert identity.user_id == 42
    assert identity.user_agent == "Real Chrome UA"
    assert identity.token == "test-token"
    assert "test-token" not in repr(identity)
    assert browser._fetch.await_count == 2
    assert browser._fetch.await_args_list[1].args[0] == browser.GQL_URL
    assert login.browser_pending.await_args.args == (None,)


@pytest.mark.asyncio
async def test_non_twitch_page_cannot_supply_cookies(tmp_path):
    browser = configured(tmp_path)
    browser._session_id = "test-session"
    browser._command = AsyncMock(return_value="about:blank")
    assert await browser._cookies() == {}
    assert browser._command.await_count == 1
