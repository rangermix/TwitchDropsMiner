"""Watch success requires HLS requests; telemetry retains its minute cadence."""

from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.models.channel import Channel, Stream
from src.utils.async_helpers import AwaitableValue


def make_channel(status=204):
    posts = []

    @asynccontextmanager
    async def request(method, url, **kwargs):
        posts.append(method)
        yield SimpleNamespace(status=status)

    selected = AwaitableValue()
    twitch = SimpleNamespace(
        gui=SimpleNamespace(channels=MagicMock()),
        request=request,
        _auth_state=SimpleNamespace(user_id=42),
        watching_channel=selected,
    )
    channel = Channel(twitch, id=123, login='example')
    channel._stream = Stream(channel, id=456, game=None, viewers=1, title='Test')
    channel._spade_url = 'https://beacon.twitch.tv/track'
    selected.set(channel)
    return channel, posts


@pytest.mark.asyncio
async def test_telemetry_success_does_not_mask_failed_playlist_watch():
    channel, posts = make_channel()
    with patch.object(Channel, '_send_watch_playlist', AsyncMock(return_value=False)) as playlist:
        assert await channel.send_watch() is False
    playlist.assert_awaited_once()


@pytest.mark.asyncio
async def test_playlist_polling_does_not_accelerate_minute_telemetry():
    channel, posts = make_channel()
    clock = [0.0]
    with (
        patch.object(Channel, '_send_watch_playlist', AsyncMock(return_value=True)) as playlist,
        patch('src.models.channel.monotonic', side_effect=lambda: clock[0], create=True),
    ):
        assert await channel.send_watch()
        clock[0] = 10
        assert await channel.send_watch()
        clock[0] = 59
        assert await channel.send_watch()
    assert playlist.await_count == 3
    assert posts == ['POST', 'POST']


@pytest.mark.asyncio
async def test_failed_telemetry_is_auxiliary_and_keeps_its_minute_cadence():
    channel, posts = make_channel(status=500)
    clock = [0.0]
    with (
        patch.object(Channel, '_send_watch_playlist', AsyncMock(return_value=True)) as playlist,
        patch('src.models.channel.monotonic', side_effect=lambda: clock[0]),
    ):
        assert await channel.send_watch()
        clock[0] = 10
        assert await channel.send_watch()
        clock[0] = 59
        assert await channel.send_watch()
    assert playlist.await_count == 3
    assert posts == ['POST', 'POST']
