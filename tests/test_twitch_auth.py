"""Device-login and saved-session regressions; no live Twitch requests."""

from contextlib import asynccontextmanager
from http.cookies import SimpleCookie
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import aiohttp
import pytest

from src.auth import _AuthState
from src.config import ClientType
from src.core.client import Twitch


class OAuthServer:
    def __init__(self, jar):
        self.jar = jar
        self.device_requests = 0
        self.token_requests = 0
        self.validations = []

    @asynccontextmanager
    async def request(self, method, url, **kwargs):
        status = 200
        data = {}
        if method == "GET" and url in (
            ClientType.SMARTBOX.CLIENT_URL,
            ClientType.ANDROID_APP.CLIENT_URL,
        ):
            self.jar.update_cookies({"unique_id": "test-device"}, url)
        elif str(url).endswith("/device"):
            self.device_requests += 1
            # Twitch's observed rejection reproduces issue #109 on the old default.
            if kwargs["data"]["client_id"] == ClientType.ANDROID_APP.CLIENT_ID:
                status, data = 400, {"status": 400, "message": "invalid client"}
            else:
                assert kwargs["data"] == {"client_id": ClientType.SMARTBOX.CLIENT_ID, "scopes": ""}
                assert kwargs["headers"]["Client-Id"] == ClientType.SMARTBOX.CLIENT_ID
                assert kwargs["headers"]["Origin"] == str(ClientType.SMARTBOX.CLIENT_URL)
                data = {
                    "device_code": "test-device-code",
                    "user_code": "TESTCODE",
                    "interval": 0,
                    "expires_in": 1800,
                    "verification_uri": "https://www.twitch.tv/activate",
                }
        elif str(url).endswith("/token"):
            self.token_requests += 1
            assert kwargs["data"] == {
                "client_id": ClientType.SMARTBOX.CLIENT_ID,
                "device_code": "test-device-code",
                "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
            }
            assert kwargs["headers"]["Client-Id"] == ClientType.SMARTBOX.CLIENT_ID
            if self.token_requests == 1:
                status, data = 400, {"message": "authorization_pending"}
            else:
                data = {"access_token": "smartbox-test-token"}
        elif str(url).endswith("/validate"):
            token = kwargs["headers"]["Authorization"]
            self.validations.append(token)
            if token == "OAuth expired-test-token":
                status, data = 401, {"message": "invalid access token"}
            else:
                data = {
                    "client_id": (
                        ClientType.ANDROID_APP.CLIENT_ID
                        if token == "OAuth android-test-token"
                        else ClientType.SMARTBOX.CLIENT_ID
                    ),
                    "user_id": "12345",
                }
        else:
            raise AssertionError(f"Unexpected request: {method} {url}")
        yield SimpleNamespace(
            status=status,
            json=AsyncMock(return_value=data),
            text=AsyncMock(return_value="<html></html>"),
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "saved_session", ["fresh", "android_host", "android_domain", "expired", "smartbox"]
)
async def test_device_login_migrates_and_restores_sessions(tmp_path, monkeypatch, saved_session):
    cookie_path = tmp_path / "cookies.jar"
    monkeypatch.setattr("src.auth.auth_state.COOKIES_PATH", cookie_path)
    monkeypatch.setattr("src.core.client.DATA_DIR", tmp_path)
    client = Twitch(MagicMock())
    client.gui = SimpleNamespace(
        login=SimpleNamespace(update=MagicMock(), ask_enter_code=AsyncMock())
    )
    jar = aiohttp.CookieJar()
    if saved_session.startswith("android"):
        cookies = SimpleCookie({"auth-token": "android-test-token"})
        if saved_session == "android_domain":
            cookies["auth-token"]["domain"] = ".twitch.tv"
        jar.update_cookies(cookies, ClientType.ANDROID_APP.CLIENT_URL)
    elif saved_session != "fresh":
        token = "expired-test-token" if saved_session == "expired" else "smartbox-test-token"
        jar.update_cookies({"auth-token": token}, ClientType.SMARTBOX.CLIENT_URL)
    server = OAuthServer(jar)
    client.get_session = AsyncMock(return_value=SimpleNamespace(cookie_jar=jar))
    client.request = server.request

    auth = await client._auth_state.validate()

    assert auth.user_id == 12345
    assert auth.access_token == "smartbox-test-token"
    assert auth._logged_in.is_set()
    assert server.device_requests == (0 if saved_session == "smartbox" else 1)
    assert server.token_requests == (0 if saved_session == "smartbox" else 2)
    assert client.gui.login.ask_enter_code.await_count == server.device_requests
    headers = auth.headers(user_agent=client._client_type.USER_AGENT, gql=True)
    assert headers["Client-Id"] == ClientType.SMARTBOX.CLIENT_ID
    assert headers["Authorization"] == "OAuth smartbox-test-token"
    client._ensure_api_clients()
    assert client._http_client._client_type is client._client_type
    assert client._gql_client._client_type is client._client_type

    # A new process must reuse the saved Smart TV token without another prompt.
    restored_jar = aiohttp.CookieJar()
    restored_jar.load(cookie_path)
    restored_server = OAuthServer(restored_jar)
    client.get_session = AsyncMock(return_value=SimpleNamespace(cookie_jar=restored_jar))
    client.request = restored_server.request
    restored = await _AuthState(client).validate()
    assert restored.user_id == 12345
    assert restored.access_token == auth.access_token
    assert restored_server.device_requests == 0
    assert restored_server.validations == ["OAuth smartbox-test-token"]
