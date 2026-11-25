"""The SDK browser authenticates only to its configured proxy."""

import asyncio
from traceback import format_exception
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.auth.server_renewal import BrowserProxy, SDKExchange
from src.auth.session_bundle import SessionError


PROXY_ORIGIN = "https://proxy.test:8443"
PROXY_SETTING = "https://synthetic%40user:synthetic%3Apass@PROXY.TEST:8443"
PROXY_USER = "synthetic@user"
PROXY_PASSWORD = "synthetic:pass"


@pytest.mark.parametrize("setting", [None, ""])
def test_empty_proxy_setting_uses_direct_connection(setting):
    assert BrowserProxy.from_setting(setting) is None


@pytest.mark.parametrize("setting, origin, username, password", [
    ("http://proxy.test:8080", "http://proxy.test:8080", None, ""),
    ("http://[2001:db8::1]:8080", "http://[2001:db8::1]:8080", None, ""),
    ("https://proxy.test:443/", "https://proxy.test", None, ""),
    (PROXY_SETTING, PROXY_ORIGIN, PROXY_USER, PROXY_PASSWORD),
])
def test_proxy_parses_single_endpoint_and_redacts_credentials(setting, origin, username, password):
    proxy = BrowserProxy.from_setting(setting)
    assert proxy is not None
    assert (proxy.origin, proxy.username, proxy.password) == (origin, username, password)
    if username is not None:
        for secret in ("synthetic%40user", "synthetic%3Apass", username, password):
            assert secret not in repr(proxy)
            assert secret not in proxy.origin


@pytest.mark.parametrize("setting", [
    "proxy.test:8080",
    "socks5://proxy.test:1080",
    "http://",
    "http://proxy.test:65536",
    "http://proxy.test/path",
    "http://proxy.test?key=value",
    "http://proxy.test#fragment",
    "http://proxy.test:8080,http://fallback.test:8080",
    "http://proxy.test:8080;http://fallback.test:8080",
    PROXY_SETTING + "/path",
    PROXY_SETTING.replace(":8443", ":invalid"),
])
def test_malformed_proxy_setting_fails_closed_without_echoing_url(setting):
    with pytest.raises(SessionError) as raised:
        BrowserProxy.from_setting(setting)

    assert raised.value.code == "CONFIG"
    error = "".join(format_exception(raised.value))
    assert setting not in error
    for secret in ("synthetic%40user", "synthetic%3Apass", PROXY_USER, PROXY_PASSWORD):
        assert secret not in error


def auth_event(*, source="Proxy", origin=PROXY_ORIGIN, scheme="basic", request_id="request-1"):
    return {
        "method": "Fetch.authRequired",
        "params": {
            "requestId": request_id,
            "request": {"url": "https://gql.twitch.tv/integrity", "method": "POST"},
            "authChallenge": {"source": source, "origin": origin, "scheme": scheme, "realm": "test"},
        },
    }


async def run_exchange(*events, setting=PROXY_SETTING):
    protocol = SimpleNamespace(events=asyncio.Queue(), command=AsyncMock())
    for event in (*events, None):
        protocol.events.put_nowait(event)
    with pytest.raises(SessionError) as raised:
        await asyncio.wait_for(SDKExchange(proxy=BrowserProxy.from_setting(setting)).run(protocol), 2)
    assert raised.value.code == "BROWSER_PROTOCOL"
    return [call.args for call in protocol.command.await_args_list]


@pytest.mark.asyncio
async def test_sdk_submits_credentials_to_matching_basic_proxy_only():
    assert await run_exchange(auth_event(scheme="Basic")) == [
        ("Fetch.continueWithAuth", {
            "requestId": "request-1",
            "authChallengeResponse": {
                "response": "ProvideCredentials",
                "username": PROXY_USER,
                "password": PROXY_PASSWORD,
            },
        }),
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize("challenge", [
    {"source": "Server"},
    {"origin": "https://different.test:8443"},
    {"origin": "https://proxy.test:8444"},
    {"origin": "http://proxy.test:8443"},
    {"scheme": "digest"},
    {"source": None},
])
async def test_sdk_cancels_unexpected_challenge_without_credentials(challenge):
    assert await run_exchange(auth_event(**challenge)) == [
        ("Fetch.continueWithAuth", {
            "requestId": "request-1", "authChallengeResponse": {"response": "CancelAuth"},
        }),
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize("setting", [None, PROXY_ORIGIN])
async def test_sdk_cancels_challenge_when_no_proxy_credentials_configured(setting):
    assert await run_exchange(auth_event(), setting=setting) == [
        ("Fetch.continueWithAuth", {
            "requestId": "request-1", "authChallengeResponse": {"response": "CancelAuth"},
        }),
    ]


@pytest.mark.asyncio
async def test_sdk_will_not_retry_credentials_after_same_request_challenge():
    assert await run_exchange(auth_event(), auth_event()) == [
        ("Fetch.continueWithAuth", {
            "requestId": "request-1",
            "authChallengeResponse": {
                "response": "ProvideCredentials",
                "username": PROXY_USER,
                "password": PROXY_PASSWORD,
            },
        }),
        ("Fetch.continueWithAuth", {
            "requestId": "request-1", "authChallengeResponse": {"response": "CancelAuth"},
        }),
    ]
