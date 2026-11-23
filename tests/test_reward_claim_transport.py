"""A reward claim must submit the mutation only once, including downstream retries."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
import pytest_asyncio

from src.config import GQL_OPERATIONS
from src.utils import ExponentialBackoff
from tests.test_imported_session import bundle_data
from tests.test_imported_session_retry import running_miner
from tests.test_reward_campaigns import CLAIM_ID, _build, _drop
from tests.test_session_lifecycle import miner


SERVICE_TIMEOUT = {"errors": [{"message": "service timeout"}]}
SUCCESS = {"data": {"claimDropRewards": {"status": "ELIGIBLE_FOR_ALL"}}}
CLAIM = GQL_OPERATIONS["ClaimDrop"].with_variables({"input": {"dropInstanceID": CLAIM_ID}})


@pytest.fixture(autouse=True)
def immediate_backoff(monkeypatch):
    monkeypatch.setattr(ExponentialBackoff, "__next__", lambda self: 0.0)


def claimable_drop(client):
    return _drop(_build({"status": "CLAIMABLE", "grantCount": 1}, twitch=client))


def response(status, payload):
    return MagicMock(status=status, read=AsyncMock(return_value=b"{}"),
                     json=AsyncMock(return_value=payload))


@pytest_asyncio.fixture
async def legacy(tmp_path, monkeypatch):
    client = miner(tmp_path, monkeypatch)
    # A validated legacy identity. All outgoing requests end at the synthetic HTTP session.
    client._auth_state.access_token = "test-token"
    client._auth_state.user_id = 42
    client._auth_state.device_id = "test-device"
    client._ensure_api_clients()
    send = AsyncMock()
    client._http_client._session = SimpleNamespace(
        closed=False, timeout=SimpleNamespace(total=5), request=send,
    )
    try:
        yield client, send
    finally:
        reset = client._gql_client._qgl_limiter._reset_task
        if reset is not None:
            reset.cancel()
            await asyncio.gather(reset, return_exceptions=True)


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", [
    "gql_timeout", "http_503", "lost_reply", "redirect", "null_data", "partial_reply",
])
async def test_legacy_reward_claim_submits_once(legacy, failure):
    client, send = legacy
    first = {
        "gql_timeout": response(200, SERVICE_TIMEOUT),
        "http_503": response(503, {}),
        "lost_reply": TimeoutError(),
        "redirect": response(307, SUCCESS),
        "null_data": response(200, {"data": None}),
        "partial_reply": response(200, {**SUCCESS, **SERVICE_TIMEOUT}),
    }[failure]
    send.side_effect = [first, response(200, SUCCESS)]
    drop = claimable_drop(client)
    assert not await drop.claim()
    assert not await drop.claim()
    assert not drop.is_claimed and not client._inventory_refresh_pending
    assert [call.kwargs["json"] for call in send.await_args_list] == [CLAIM]
    assert send.await_args.kwargs["allow_redirects"] is False


@pytest.mark.asyncio
async def test_legacy_inventory_read_still_recovers_from_service_timeout(legacy):
    client, send = legacy
    expected = {"data": {"currentUser": {"inventory": {}}}}
    send.side_effect = [response(200, SERVICE_TIMEOUT), response(200, expected)]
    assert await client.gql_request(GQL_OPERATIONS["Inventory"]) == expected
    assert [call.kwargs["json"] for call in send.await_args_list] == [GQL_OPERATIONS["Inventory"]] * 2


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", [SERVICE_TIMEOUT, 503, 401, {"errors": [{"message": "failed integrity check"}]}],
                         ids=["gql_timeout", "http_503", "http_auth", "gql_auth"])
async def test_browser_reward_claim_submits_once(tmp_path, monkeypatch, failure):
    async with running_miner(tmp_path, monkeypatch) as (client, peer, clock):
        peer.responses.extend([failure, SUCCESS])

        async def renew_if_requested(pending):
            if pending:
                clock[0] += 10
                peer.armed = False
                try:
                    await client._browser.install(bundle_data(clock[0], "renewed-integrity"))
                finally:
                    peer.armed = True

        client.gui.login.import_pending = renew_if_requested
        drop = claimable_drop(client)
        assert not await asyncio.wait_for(drop.claim(), timeout=2)
        assert not await drop.claim()
        assert not drop.is_claimed and not client._inventory_refresh_pending
        assert peer.calls == [CLAIM]
