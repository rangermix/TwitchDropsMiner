"""The dashboard must not keep showing a watched channel after the miner goes idle."""

import asyncio
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.config import State
from src.models import channel as channel_module
from src.models.channel import Channel
from src.web.managers.channels import ChannelListManager
from tests.test_session_lifecycle import miner


def _manager():
    return ChannelListManager(MagicMock(emit=AsyncMock()))


@pytest.mark.asyncio
async def test_get_channels_reflects_cleared_watching():
    manager = _manager()
    channel = Channel(MagicMock(), id=7, login="streamer")
    manager.set_watching(channel)
    manager.display(channel, add=True)
    assert manager.get_channels()[0]["watching"] is True

    manager.clear_watching()
    assert manager.get_channels()[0]["watching"] is False

    manager.set_watching(channel)
    assert manager.get_channels()[0]["watching"] is True
    await asyncio.sleep(0)


@pytest.mark.asyncio
@pytest.mark.parametrize("probe_pending", [False, True])
async def test_idle_without_wanted_games_clears_dashboard_channels(
    tmp_path, monkeypatch, probe_pending
):
    client = miner(tmp_path, monkeypatch)
    client.gui.channels = manager = _manager()
    client.settings = MagicMock(games_to_watch=[], priority_list_only=True)
    client.is_mining_time = lambda: True
    client._ensure_api_clients = MagicMock()
    client.get_auth = AsyncMock(return_value=SimpleNamespace(user_id=42))
    client.websocket.start = AsyncMock()
    client.websocket.add_topics = MagicMock()
    client.websocket.remove_topics = MagicMock()
    client._watch_service.watch_loop = AsyncMock()
    client._mining_schedule_loop = AsyncMock()
    client.fetch_inventory = AsyncMock()
    client.inventory = []

    channel = Channel(client, id=7, login="streamer", acl_based=True)
    client.channels[channel.id] = channel
    manager.set_watching(channel)
    manager.display(channel, add=True)

    probe = None
    finish_probe = asyncio.Event()
    if probe_pending:
        probe_started = asyncio.Event()
        monkeypatch.setattr(channel_module, "ONLINE_DELAY", timedelta(seconds=0))

        async def stream_lookup(self):
            probe_started.set()
            await finish_probe.wait()
            return None

        monkeypatch.setattr(Channel, "get_stream", stream_lookup)
        channel.check_online()
        probe = channel._pending_stream_up
        assert probe is not None
        await asyncio.wait_for(probe_started.wait(), timeout=1)

    run = asyncio.create_task(client._run())
    try:
        for _ in range(200):
            if (
                client.fetch_inventory.await_count
                and client._state is State.IDLE
                and not client._state_change.is_set()
            ):
                break
            await asyncio.sleep(0.005)
        assert client._state is State.IDLE
        assert client.wanted_games == []
        assert manager.get_channels() == []
        if probe is not None:
            finish_probe.set()
            await asyncio.wait_for(asyncio.gather(probe, return_exceptions=True), timeout=1)
            assert probe.cancelled()
            assert manager.get_channels() == []
    finally:
        finish_probe.set()
        run.cancel()
        await asyncio.gather(run, return_exceptions=True)
        if probe is not None:
            await asyncio.gather(probe, return_exceptions=True)
