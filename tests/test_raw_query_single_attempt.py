"""A GQLRawQuery with single_attempt set is sent once, like a GQLOperation."""

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import aiohttp
import pytest

from src.api.gql_client import GQLClient
from src.api.http_client import HTTPClient
from src.config import ClientType, GQLRawQuery, State
from src.exceptions import GQLException, GQLServerError, RequestException


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


def browser_client(reply):
    browser = SimpleNamespace(gql=AsyncMock(return_value=reply))
    auth = SimpleNamespace(validate=AsyncMock(return_value=SimpleNamespace(
        browser_active=True, _twitch=SimpleNamespace(_browser=browser),
    )))
    return GQLClient(MagicMock(), auth, ClientType.WEB)


@pytest.mark.asyncio
@pytest.mark.parametrize(("reply", "server_side"), [
    # a server-side error: the mutation may have run
    ({"errors": [{"message": "service timeout"}]}, True),
    ({"errors": [{"message": "context deadline exceeded", "path": ["makePrediction"]}], "data": None}, True),
    ({"errors": [{"message": "service unavailable"}]}, True),
    ({"errors": [{"message": "service error"}]}, True),
    ({"errors": [{"message": "server error", "path": ["makePrediction"]}]}, True),
    ({"errors": [{"message": "failed integrity check"}, {"message": "service timeout"}]}, True),  # mixed
    # no readable error message
    ({"errors": [{"path": ["x"]}]}, True),
    ({"errors": [{"message": ""}]}, True),
    ({"errors": [{"message": None}]}, True),
    ({"errors": ["service timeout"]}, True),
    ({"errors": "service timeout"}, True),
    ({"error": True}, True),
    ([OK, "not a row"], True),
    # a named refusal
    ({"errors": [{"message": "failed integrity check"}]}, False),
    ({"errors": [{"message": "PersistedQueryNotFound"}]}, False),
    # case and outer spaces do not hide a server-side message; an HTTP-style 5xx is server-side too
    ({"errors": [{"message": "Service Timeout"}]}, True),
    ({"errors": [{"message": " service timeout "}]}, True),
    ({"errors": [{"message": "internal server error"}]}, True),
    ({"error": "Internal Server Error", "status": 500, "message": "x"}, True),
    ({"error": "Service Unavailable", "status": 503, "message": "x"}, True),
    ({"error": "   ", "status": 400, "message": "x"}, True),
    ({"errors": [{"message": "service timeout!"}]}, False),  # near-miss of a server-side message
    ({"errors": [{"message": "service timeouts"}]}, False),
    ({"error": "Bad Request", "status": 400, "message": "x"}, False),
    ({"error": "Internal Server Error", "status": "500", "message": "x"}, False),  # status not a number
    ({"error": "Unauthorized", "status": 401, "message": "invalid oauth token"}, False),
    ([OK, {"errors": [{"message": "failed integrity check"}]}], False),
])
async def test_single_attempt_error_reply_splits_server_side_errors_from_refusals(reply, server_side):
    with pytest.raises(GQLException, match="not confirmed") as raised:
        await browser_client(reply).request(op())
    assert isinstance(raised.value, GQLServerError) is server_side


@pytest.mark.asyncio
@pytest.mark.parametrize("reply", [OK, {"data": {"x": None}, "errors": []}, {"data": {"x": 1}, "error": None}])
async def test_single_attempt_reply_without_errors_is_returned(reply):
    assert await browser_client(reply).request(op()) == reply


@pytest.mark.asyncio
async def test_nested_error_in_the_reply_is_returned_not_raised():
    # a refused share carries data.shareViewerMilestone.error; only a top-level error is "not confirmed"
    reply = {"data": {"shareViewerMilestone": {"milestone": None, "error": {"code": "NOT_ALLOWED"}}}}
    browser = SimpleNamespace(gql=AsyncMock(return_value=reply))
    auth = SimpleNamespace(validate=AsyncMock(return_value=SimpleNamespace(
        browser_active=True, _twitch=SimpleNamespace(_browser=browser),
    )))
    assert await GQLClient(MagicMock(), auth, ClientType.WEB).request(op()) == reply
