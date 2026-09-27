"""Production GQL dispatch retries only unambiguous imported-session reads."""

import asyncio
from collections import deque
from contextlib import asynccontextmanager

import pytest
from aiohttp import web

from src.auth.browser_session import BrowserSession
from src.auth.session_bundle import SessionError
from src.config import GQL_OPERATIONS, ClientType
from src.exceptions import ExitRequest
from tests.test_helper_lifecycle import miner
from tests.test_imported_session import bundle_data, catalog


class TwitchPeer:
    def __init__(self):
        self.armed = False
        self.user_id = "42"
        self.responses = deque()
        self.calls = []

    async def validate(self, request):
        return web.json_response({"client_id": ClientType.WEB.CLIENT_ID, "user_id": self.user_id})

    async def gql(self, request):
        body = await request.json()
        if not self.armed:
            return web.json_response(catalog())
        self.calls.append(body)
        response = self.responses.popleft()
        if isinstance(response, int):
            return web.Response(status=response, text="private upstream failure")
        return web.json_response(response)


@asynccontextmanager
async def running_miner(tmp_path, monkeypatch):
    peer = TwitchPeer()
    app = web.Application()
    app.router.add_get("/validate", peer.validate)
    app.router.add_post("/gql", peer.gql)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    address = f"http://127.0.0.1:{site._server.sockets[0].getsockname()[1]}"
    monkeypatch.setattr(BrowserSession, "VALIDATE_URL", address + "/validate")
    monkeypatch.setattr(BrowserSession, "GQL_URL", address + "/gql")
    client = miner(tmp_path, monkeypatch)
    clock = [1000]
    client._browser._clock = lambda: clock[0]
    try:
        await client._browser.install(bundle_data())
        await client.get_auth()
        peer.armed = True
        yield client, peer, clock
    finally:
        await client._browser.close()
        limiter = client._gql_client._qgl_limiter if client._gql_client else None
        if limiter and limiter._reset_task:
            limiter._reset_task.cancel()
            await asyncio.gather(limiter._reset_task, return_exceptions=True)
        await runner.cleanup()


def immediate_delays(session, monkeypatch):
    delays = []

    async def wait(delay):
        assert not session._lock.locked(), "Retry delay blocks renewal and account changes"
        delays.append(delay)

    monkeypatch.setattr(session, "_wait_retry", wait, raising=False)
    return delays


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", [
    GQL_OPERATIONS["Inventory"],
    GQL_OPERATIONS["CurrentDrop"].with_variables({"channelID": "123"}),
])
async def test_production_gql_recovers_from_503_without_rejecting_session(tmp_path, monkeypatch, operation):
    async with running_miner(tmp_path, monkeypatch) as (client, peer, _clock):
        success = {"data": {"currentUser": {"id": "42"}}}
        peer.responses.extend([503, success])
        delays = immediate_delays(client._browser, monkeypatch)
        assert await client.gql_request(operation) == success
        assert peer.calls == [operation, operation]
        assert delays == [1]
        assert client._browser.status()["state"] == "ready"
        assert client._browser.status()["generation"] == 1


@pytest.mark.asyncio
async def test_production_gql_retries_transport_timeout(tmp_path, monkeypatch):
    async with running_miner(tmp_path, monkeypatch) as (client, peer, _clock):
        peer.responses.append({"data": {"currentUser": {"id": "42"}}})
        immediate_delays(client._browser, monkeypatch)
        http = client._browser._transport._http
        request = http.request
        attempts = []

        def timeout_once(*args, **kwargs):
            attempts.append(kwargs["json"])
            if len(attempts) == 1:
                raise TimeoutError("private transport details")
            return request(*args, **kwargs)

        monkeypatch.setattr(http, "request", timeout_once)
        assert await client.gql_request(GQL_OPERATIONS["Inventory"]) == {"data": {"currentUser": {"id": "42"}}}
        assert len(attempts) == 2
        assert client._browser.status()["state"] == "ready"


@pytest.mark.asyncio
async def test_transient_retry_exhaustion_is_bounded_and_redacted(tmp_path, monkeypatch):
    async with running_miner(tmp_path, monkeypatch) as (client, peer, _clock):
        peer.responses.extend([503, 502, 504, {"data": {"tooLate": True}}])
        delays = immediate_delays(client._browser, monkeypatch)
        with pytest.raises(SessionError, match="REQUEST") as error:
            await client.gql_request(GQL_OPERATIONS["Inventory"])
        assert "private" not in str(error.value)
        assert len(peer.calls) == 3
        assert delays == [1, 2]
        assert len(peer.responses) == 1
        assert client._browser.status()["state"] == "ready"


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", [
    GQL_OPERATIONS["ClaimDrop"].with_variables({"input": {"dropInstanceID": "claim"}}),
    GQL_OPERATIONS["NotificationsDelete"],
    {"operationName": "Inventory"},
    {"operationName": "Unknown", "extensions": GQL_OPERATIONS["Inventory"]["extensions"]},
    {**GQL_OPERATIONS["Inventory"], "query": "mutation { pretendRead }"},
    {**GQL_OPERATIONS["Inventory"], "extensions": {"persistedQuery": {"version": 1, "sha256Hash": "unknown"}}},
    {"query": "query { currentUser { id } }"},
    [GQL_OPERATIONS["Inventory"], GQL_OPERATIONS["NotificationsDelete"]],
])
async def test_ambiguous_mutations_raw_and_unknown_operations_are_not_replayed(tmp_path, monkeypatch, operation):
    async with running_miner(tmp_path, monkeypatch) as (client, peer, _clock):
        peer.responses.extend([503, {"data": {"mustNotReplay": True}}])
        delays = immediate_delays(client._browser, monkeypatch)
        with pytest.raises(SessionError, match="REQUEST"):
            await client.gql_request(operation)
        assert peer.calls == [operation]
        assert delays == []


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [302, 400, 404, 429])
async def test_nontransient_http_responses_are_not_replayed(tmp_path, monkeypatch, status):
    async with running_miner(tmp_path, monkeypatch) as (client, peer, _clock):
        peer.responses.extend([status, {"data": {"mustNotReplay": True}}])
        delays = immediate_delays(client._browser, monkeypatch)
        with pytest.raises(SessionError, match="REQUEST"):
            await client.gql_request(GQL_OPERATIONS["Inventory"])
        assert len(peer.calls) == 1
        assert delays == []


@pytest.mark.asyncio
async def test_partial_batch_auth_then_transient_failure_never_repeats_successful_mutation(tmp_path, monkeypatch):
    async with running_miner(tmp_path, monkeypatch) as (client, peer, clock):
        claim = GQL_OPERATIONS["ClaimDrop"].with_variables({"input": {"dropInstanceID": "claim"}})
        inventory = GQL_OPERATIONS["Inventory"]
        claimed, read = {"data": {"claim": "done"}}, {"data": {"inventory": "ok"}}
        peer.responses.extend([[claimed, {"errors": [{"message": "failed integrity check"}]}], 503, [read]])
        delays = immediate_delays(client._browser, monkeypatch)
        waiting = asyncio.Event()

        async def pending(value):
            if value:
                waiting.set()

        client.gui.login.import_pending = pending
        task = asyncio.create_task(client.gql_request([claim, inventory]))
        try:
            await asyncio.wait_for(waiting.wait(), 1)
            clock[0] = 1100
            peer.armed = False
            await client._browser.install(bundle_data(1100, "replacement-integrity"))
            peer.armed = True
            assert await task == [claimed, read]
            assert peer.calls == [[claim, inventory], [inventory], [inventory]]
            assert delays == [1]
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)


def observe_retry_wait(session, monkeypatch):
    waiting = asyncio.Event()
    original = session._updated.wait

    async def wait():
        assert not session._lock.locked()
        waiting.set()
        return await original()

    monkeypatch.setattr(session._updated, "wait", wait)
    return waiting


@pytest.mark.asyncio
@pytest.mark.parametrize("cancel", [False, True])
async def test_retry_wait_is_interruptible_and_sends_nothing_after_stop(tmp_path, monkeypatch, cancel):
    async with running_miner(tmp_path, monkeypatch) as (client, peer, _clock):
        peer.responses.extend([503, {"data": {"mustNotReplay": True}}])
        waiting = observe_retry_wait(client._browser, monkeypatch)
        task = asyncio.create_task(client.gql_request(GQL_OPERATIONS["Inventory"]))
        try:
            await asyncio.wait_for(waiting.wait(), 1)
            if cancel:
                task.cancel()
            else:
                client._browser.request_stop()
            with pytest.raises(asyncio.CancelledError if cancel else ExitRequest):
                await asyncio.wait_for(task, 1)
            assert len(peer.calls) == 1
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
async def test_account_replacement_during_retry_does_not_resume_old_account_work(tmp_path, monkeypatch):
    async with running_miner(tmp_path, monkeypatch) as (client, peer, clock):
        peer.responses.extend([503, {"data": {"mustNotReplay": True}}])
        waiting = observe_retry_wait(client._browser, monkeypatch)
        task = asyncio.create_task(client.gql_request(GQL_OPERATIONS["Inventory"]))
        try:
            await asyncio.wait_for(waiting.wait(), 1)
            clock[0] = 1100
            peer.armed, peer.user_id = False, "43"
            await client._browser.install(bundle_data(1100, "new-integrity", "other-account"), replace=True)
            client._auth_state.clear()
            await client.get_auth()
            peer.armed = True
            with pytest.raises(SessionError, match="STALE"):
                await asyncio.wait_for(task, 1)
            assert len(peer.calls) == 1
            assert client._browser.status()["user_id"] == 43
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
async def test_same_account_renewal_can_finish_during_retry_wait(tmp_path, monkeypatch):
    async with running_miner(tmp_path, monkeypatch) as (client, peer, clock):
        success = {"data": {"currentUser": {"id": "42"}}}
        peer.responses.extend([503, success])
        waiting = observe_retry_wait(client._browser, monkeypatch)
        task = asyncio.create_task(client.gql_request(GQL_OPERATIONS["Inventory"]))
        try:
            await asyncio.wait_for(waiting.wait(), 1)
            assert client._browser.status()["state"] == "ready"
            clock[0] = 1100
            peer.armed = False
            await asyncio.wait_for(client._browser.install(bundle_data(1100, "renewed-integrity", "renewed-token")), 1)
            peer.armed = True
            assert await asyncio.wait_for(task, 1) == success
            assert client._browser.status()["generation"] == 2
            assert client._auth_state.access_token == "renewed-token"
            assert peer.calls == [GQL_OPERATIONS["Inventory"], GQL_OPERATIONS["Inventory"]]
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
async def test_definitively_rejected_mutation_is_not_replayed_after_later_transport_failure(tmp_path, monkeypatch):
    async with running_miner(tmp_path, monkeypatch) as (client, peer, clock):
        claim = GQL_OPERATIONS["ClaimDrop"].with_variables({"input": {"dropInstanceID": "claim"}})
        peer.responses.extend([{"errors": [{"message": "failed integrity check"}]}, 503, {"data": {"mustNotReplay": True}}])
        delays = immediate_delays(client._browser, monkeypatch)
        waiting = asyncio.Event()

        async def pending(value):
            if value:
                waiting.set()

        client.gui.login.import_pending = pending
        task = asyncio.create_task(client.gql_request(claim))
        try:
            await asyncio.wait_for(waiting.wait(), 1)
            clock[0] = 1100
            peer.armed = False
            await client._browser.install(bundle_data(1100, "replacement-integrity"))
            peer.armed = True
            with pytest.raises(SessionError, match="REQUEST"):
                await task
            assert peer.calls == [claim, claim]
            assert delays == []
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
