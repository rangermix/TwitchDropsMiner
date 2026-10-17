"""Dashboard destinations use the channel login even with a localized display name."""

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.models.channel import Channel
from src.web.managers.channels import ChannelListManager


@pytest.mark.asyncio
@pytest.mark.parametrize("batch", [False, True])
async def test_dashboard_channel_identity_uses_canonical_login(batch):
    broadcaster = MagicMock(emit=AsyncMock())
    manager = ChannelListManager(broadcaster)
    channel = Channel(MagicMock(), id=7, login="some_streamer", display_name="配信者")
    if batch:
        manager.batch_update([channel])
    else:
        manager.display(channel, add=True)
    await asyncio.sleep(0)
    payload = manager.get_channels()[0]
    assert payload["name"] == "配信者"
    assert payload["login"] == "some_streamer"
    assert payload["url"] == "https://www.twitch.tv/some_streamer"
    emitted = broadcaster.emit.await_args.args[1]
    assert (emitted["channels"][0] if batch else emitted) == payload
