"""retry=False on GQLClient and HTTPClient: a share is sent once; retryable replies, 5xx and connection errors raise instead of resending."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import aiohttp
import pytest

from src.api.gql_client import GQLClient
from src.api.http_client import HTTPClient
from src.config import ClientType, GQLRawQuery, State
from src.core.client import Twitch
from src.exceptions import GQLException, RequestException


OK = {"data": {"x": 1}}
RETRYABLE = ["service timeout", "service unavailable", "context deadline exceeded", "service error", "PersistedQueryNotFound"]


def client(monkeypatch, replies):
    monkeypatch.setattr("src.api.gql_client.asyncio.sleep", AsyncMock())
    browser = SimpleNamespace(gql=AsyncMock(side_effect=list(replies)))
    auth = SimpleNamespace(validate=AsyncMock(return_value=SimpleNamespace(
        browser_active=True, _twitch=SimpleNamespace(_browser=browser),
    )))
    return GQLClient(MagicMock(), auth, ClientType.WEB), browser


def error(message):
    return {"errors": [{"message": message}], "extensions": {"operationName": "Op"}}


@pytest.mark.asyncio
@pytest.mark.parametrize("message", RETRYABLE)
@pytest.mark.parametrize("batch", [False, True])
async def test_retry_false_sends_once_and_raises(monkeypatch, message, batch):
    reply = error(message)
    gql, browser = client(monkeypatch, [[OK, reply] if batch else reply, OK, OK])
    op = GQLRawQuery("query Op { x }", operation_name="Op")
    with pytest.raises(GQLException):
        await gql.request([op, op] if batch else op, retry=False)
    assert browser.gql.call_count == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("message", RETRYABLE)
async def test_retry_default_is_unchanged(monkeypatch, message):
    # service error / PersistedQueryNotFound: one retry; timeouts: until an answer comes
    gql, browser = client(monkeypatch, [error(message), OK, OK])
    assert await gql.request(GQLRawQuery("query Op { x }")) == OK
    assert browser.gql.call_count == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("message", ["service timeout", "service unavailable", "context deadline exceeded"])
async def test_timeouts_still_retry_without_limit_by_default(monkeypatch, message):
    gql, browser = client(monkeypatch, [error(message)] * 4 + [OK])
    assert await gql.request(GQLRawQuery("query Op { x }")) == OK
    assert browser.gql.call_count == 5


@pytest.mark.asyncio
async def test_service_error_still_retries_only_once_by_default(monkeypatch):
    gql, browser = client(monkeypatch, [error("service error")] * 2 + [OK])
    with pytest.raises(GQLException):
        await gql.request(GQLRawQuery("query Op { x }"))
    assert browser.gql.call_count == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("kwargs", [{}, {"retry": True}, {"retry": False}])
async def test_twitch_gql_request_passes_retry_through(kwargs):
    inner = SimpleNamespace(request=AsyncMock(return_value=OK))
    tw = SimpleNamespace(_ensure_api_clients=lambda: None, _gql_client=inner)
    op = GQLRawQuery("query { x }")
    assert await Twitch.gql_request(tw, op, **kwargs) == OK
    inner.request.assert_awaited_once_with(op, retry=kwargs.get("retry", True))


def http_client(monkeypatch, outcomes):
    """A real HTTPClient whose session answers each request with the next outcome (a status or an exception)."""
    monkeypatch.setattr("src.api.http_client.asyncio.sleep", AsyncMock())

    def response(status):
        return SimpleNamespace(status=status, read=AsyncMock(return_value=b"{}"), json=AsyncMock(return_value=OK),
                               release=MagicMock())

    session = SimpleNamespace(
        timeout=aiohttp.ClientTimeout(total=1),
        request=AsyncMock(side_effect=[o if isinstance(o, BaseException) else response(o) for o in outcomes]),
    )
    http = HTTPClient(SimpleNamespace(proxy=""), MagicMock(), SimpleNamespace(_state=State.IDLE), ClientType.WEB)
    http.get_session = AsyncMock(return_value=session)
    return http, session


FAILURES = [500, 503, TimeoutError(), aiohttp.ClientConnectionError(), aiohttp.ClientPayloadError()]


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", FAILURES, ids=str)
async def test_http_retry_false_does_not_resend_a_post(monkeypatch, failure):
    # a share POST that timed out or got a 5xx may already have been applied by Twitch
    http, session = http_client(monkeypatch, [failure, 200])
    with pytest.raises((RequestException, aiohttp.ClientError, TimeoutError)):
        async with http.request("POST", "https://gql.twitch.tv/gql", retry=False, json={}):
            pass
    assert session.request.call_count == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", FAILURES, ids=str)
async def test_http_default_still_resends(monkeypatch, failure):
    http, session = http_client(monkeypatch, [failure, failure, 200])
    async with http.request("POST", "https://gql.twitch.tv/gql", json={}) as response:
        assert response.status == 200
    assert session.request.call_count == 3


@pytest.mark.asyncio
@pytest.mark.parametrize(("kwargs", "sends"), [({}, 2), ({"retry": False}, 1)])
async def test_gql_client_passes_retry_to_the_http_client(monkeypatch, kwargs, sends):
    http, session = http_client(monkeypatch, [TimeoutError(), 200])
    auth = SimpleNamespace(validate=AsyncMock(return_value=SimpleNamespace(
        browser_active=False, headers=lambda **_: {},
    )))
    gql = GQLClient(http, auth, ClientType.WEB)
    if sends == 1:
        with pytest.raises(TimeoutError):
            await gql.request(GQLRawQuery("mutation Op { x }"), **kwargs)
    else:
        assert await gql.request(GQLRawQuery("mutation Op { x }"), **kwargs) == OK
    assert session.request.call_count == sends


@pytest.mark.asyncio
@pytest.mark.parametrize("message", ["service error", "PersistedQueryNotFound"])
@pytest.mark.parametrize("op, logged", [
    (GQLRawQuery("query Op { x }", operation_name="Named"), "Named"),
    (GQLRawQuery("query { x }"), "unknown"),
])
async def test_single_retry_reply_without_extensions_is_retried_once(monkeypatch, caplog, message, op, logged):
    # a reply without "extensions" raised KeyError in the log line instead of being retried
    gql, browser = client(monkeypatch, [{"errors": [{"message": message}]}, OK])
    assert await gql.request(op) == OK
    assert browser.gql.call_count == 2
    assert f"Retrying a {message} for {logged}" in caplog.text


@pytest.mark.asyncio
async def test_single_retry_log_names_the_batch_entry_that_failed(monkeypatch, caplog):
    gql, browser = client(monkeypatch, [[OK, {"errors": [{"message": "service error"}], "extensions": []}], [OK, OK]])
    ops = [GQLRawQuery("query A { x }", operation_name="A"), GQLRawQuery("query B { x }", operation_name="B")]
    assert await gql.request(ops) == [OK, OK]
    assert browser.gql.call_count == 2
    assert "Retrying a service error for B" in caplog.text
