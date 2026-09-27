"""Real task ownership boundaries drain old-account work before replacement."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.models.channel import Channel
from src.websocket.websocket import Websocket
from tests.test_helper_lifecycle import miner


@pytest.mark.asyncio
@pytest.mark.parametrize("stage", ["campaigns", "pictures", "channels"])
async def test_cancelled_fanout_drains_children_and_releases_provider_lock(tmp_path, monkeypatch, stage):
    client = miner(tmp_path, monkeypatch)
    entered, cleaned = asyncio.Event(), asyncio.Event()
    children = []

    async def blocked(*args):
        children.append(asyncio.current_task())
        async with client._browser._lock:
            entered.set()
            try:
                await asyncio.Event().wait()
            finally:
                await asyncio.sleep(0)
                cleaned.set()

    service = client._inventory_service
    if stage == "channels":
        channel = SimpleNamespace(stream_gql={"query": "test"}, id=1)
        client.gql_request = blocked
        operation = client._channel_service.bulk_check_online([channel])
    else:
        data = {"id": "one", "status": "ACTIVE", "game": {"id": "game"}}
        client.gql_request = AsyncMock(side_effect=[
            {"data": {"currentUser": {"inventory": {"dropCampaignsInProgress": [], "gameEventDrops": []}}}},
            {"data": {"currentUser": {"dropCampaigns": [data]}}},
        ])
        if stage == "campaigns":
            service.fetch_campaigns = blocked
        else:
            service.fetch_campaigns = AsyncMock(return_value={"one": data})
            monkeypatch.setattr("src.services.inventory_service.DropsCampaign", lambda *args: SimpleNamespace(
                id="one", active=True, upcoming=False, ends_at=1, eligible=True,
                drops=[], can_earn_within=lambda *_: False,
            ))
            client.gui.inv.add_campaign = blocked
        operation = service.fetch_inventory()
    parent = asyncio.create_task(operation)
    await asyncio.wait_for(entered.wait(), 1)
    try:
        parent.cancel()
        with pytest.raises(asyncio.CancelledError):
            await parent
        assert cleaned.is_set()
        assert all(task.done() for task in children)
        assert not client._browser._lock.locked()
    finally:
        for task in children:
            task.cancel()
        await asyncio.gather(*children, return_exceptions=True)


@pytest.mark.asyncio
async def test_socket_stop_drains_topic_callbacks_before_new_account(tmp_path, monkeypatch):
    client = miner(tmp_path, monkeypatch)
    client._auth_state.user_id = 17
    socket = Websocket(client.websocket, 0)
    entered, release, cleaned = asyncio.Event(), asyncio.Event(), asyncio.Event()
    observed = []
    callbacks = []

    async def callback(data):
        callbacks.append(asyncio.current_task())
        entered.set()
        try:
            await release.wait()
            observed.append(client._auth_state.user_id)
        finally:
            await asyncio.sleep(0)
            cleaned.set()

    socket.topics["account.17"] = callback
    socket._handle_message({"data": {"topic": "account.17", "message": "{}"}})
    await entered.wait()
    try:
        await socket.stop(remove=True)
        client._auth_state.user_id = 42
        release.set()
        await asyncio.gather(*callbacks, return_exceptions=True)
        assert cleaned.is_set()
        assert observed == []
        assert socket.topics == {}
        # Late transport delivery after stop must not start another callback.
        socket.topics["account.17"] = callback
        socket._handle_message({"data": {"topic": "account.17", "message": "{}"}})
        await asyncio.sleep(0)
        assert len(callbacks) == 1
    finally:
        for task in callbacks:
            task.cancel()
        await asyncio.gather(*callbacks, return_exceptions=True)


@pytest.mark.asyncio
async def test_authentication_change_drains_inflight_channel_online_check(tmp_path, monkeypatch):
    client = miner(tmp_path, monkeypatch)
    client.websocket.stop = AsyncMock()
    entered, cleaned = asyncio.Event(), asyncio.Event()
    channel = Channel(client, id=17, login="old-channel")
    client.channels[channel.id] = channel

    async def online_delay(self):
        # Production clears the pending display marker before its network call.
        self._pending_stream_up = None
        entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            cleaned.set()

    monkeypatch.setattr(Channel, "_online_delay", online_delay)
    channel.check_online()
    task = channel._pending_stream_up
    await entered.wait()
    try:
        async with client.authentication_change():
            assert cleaned.is_set() and task.done()
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
