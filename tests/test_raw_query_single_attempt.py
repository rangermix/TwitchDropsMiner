"""A GQLRawQuery with single_attempt set is sent once, like a GQLOperation."""

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import aiohttp
import pytest

from src.api.gql_client import GQLClient
from src.api.http_client import HTTPClient
from src.config import ClientType, GQLRawQuery, State
from src.exceptions import GQLException, RequestException


OK = {"data": {"x": 1}}


def op(single_attempt=True):
    query = GQLRawQuery("mutation Op { x }")
    query.single_attempt = single_attempt
    return query


def test_single_attempt_is_not_part_of_the_json_body():
    assert json.loads(json.dumps(op())) == {"query": "mutation Op { x }"}


def http_gql(monkeypatch, outcomes):
    """A real GQLClient and HTTPClient whose session answers each request with the next outcome."""
    monkeypatch.setattr("src.api.http_client.asyncio.sleep", AsyncMock())
    monkeypatch.setattr("src.api.gql_client.asyncio.sleep", AsyncMock())

    def response(status):
        return SimpleNamespace(status=status, read=AsyncMock(return_value=b"{}"), json=AsyncMock(return_value=OK),
                               release=MagicMock())

    session = SimpleNamespace(
        timeout=aiohttp.ClientTimeout(total=1),
        request=AsyncMock(side_effect=[o if isinstance(o, BaseException) else response(o) for o in outcomes]),
    )
    http = HTTPClient(SimpleNamespace(proxy=""), MagicMock(), SimpleNamespace(_state=State.IDLE), ClientType.WEB)
    http.get_session = AsyncMock(return_value=session)
    auth = SimpleNamespace(validate=AsyncMock(return_value=SimpleNamespace(browser_active=False, headers=lambda **_: {})))
    return GQLClient(http, auth, ClientType.WEB), session


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", [502, 503, TimeoutError()], ids=str)
async def test_raw_query_with_single_attempt_is_sent_once_over_http(monkeypatch, failure):
    gql, session = http_gql(monkeypatch, [failure, 200])
    with pytest.raises(RequestException, match="Unconfirmed HTTP response"):
        await gql.request(op())
    assert session.request.call_count == 1
    assert session.request.call_args.kwargs["allow_redirects"] is False


@pytest.mark.asyncio
async def test_raw_query_without_single_attempt_is_still_resent(monkeypatch):
    gql, session = http_gql(monkeypatch, [503, 200])
    assert await gql.request(op(False)) == OK
    assert session.request.call_count == 2


@pytest.mark.asyncio
async def test_raw_query_with_single_attempt_in_a_batch_is_sent_once(monkeypatch):
    gql, session = http_gql(monkeypatch, [503, 200])
    with pytest.raises(RequestException):
        await gql.request([GQLRawQuery("query A { x }"), op()])
    assert session.request.call_count == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("message", ["service timeout", "service error"])
async def test_raw_query_with_single_attempt_does_not_retry_an_error_reply(monkeypatch, message):
    browser = SimpleNamespace(gql=AsyncMock(side_effect=[{"errors": [{"message": message}]}, OK]))
    auth = SimpleNamespace(validate=AsyncMock(return_value=SimpleNamespace(
        browser_active=True, _twitch=SimpleNamespace(_browser=browser),
    )))
    with pytest.raises(GQLException, match="not confirmed"):
        await GQLClient(MagicMock(), auth, ClientType.WEB).request(op())
    browser.gql.assert_awaited_once()
    assert browser.gql.call_args.kwargs == {"single_attempt": True}


@pytest.mark.asyncio
async def test_nested_error_in_the_reply_is_returned_not_raised():
    # a refused share carries data.shareViewerMilestone.error; only a top-level error is "not confirmed"
    reply = {"data": {"shareViewerMilestone": {"milestone": None, "error": {"code": "NOT_ALLOWED"}}}}
    browser = SimpleNamespace(gql=AsyncMock(return_value=reply))
    auth = SimpleNamespace(validate=AsyncMock(return_value=SimpleNamespace(
        browser_active=True, _twitch=SimpleNamespace(_browser=browser),
    )))
    assert await GQLClient(MagicMock(), auth, ClientType.WEB).request(op()) == reply
