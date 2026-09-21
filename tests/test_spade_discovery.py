"""Exercise discovery and watch submission together with the Smart TV client."""

import base64
import json
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.config import ClientType
from src.models.channel import Channel, Stream


@pytest.mark.asyncio
@pytest.mark.parametrize("inline_beacon", [True, False])
async def test_smartbox_watch_discovers_beacon_on_public_channel_page(inline_beacon):
    beacon = "https://beacon.twitch.tv/track"
    settings = "https://assets.twitch.tv/config/settings." + "a" * 32 + ".js"
    requests = []
    events = []

    @asynccontextmanager
    async def request(method, url, **kwargs):
        requests.append((method, str(url)))
        text, status = "", 200
        if str(url) == "https://android.tv.twitch.tv/example_channel":
            # The Smart TV application shell has neither supported discovery field.
            text = '<script src="/assets/index.js"></script>'
        elif str(url) == "https://www.twitch.tv/example_channel":
            text = (
                '{"beacon_url": "' + beacon + '"}'
                if inline_beacon
                else f'<script src="{settings}"></script>'
            )
        elif str(url) == settings:
            text = '{"beaconUrl": "' + beacon + '"}'
        elif str(url) == beacon and method == "POST":
            events.extend(json.loads(base64.b64decode(kwargs["data"]["data"])))
            status = 204
        else:
            raise AssertionError(f"Unexpected request: {method} {url}")
        yield SimpleNamespace(status=status, text=AsyncMock(return_value=text))

    client = SimpleNamespace(
        _client_type=ClientType.SMARTBOX,
        request=request,
        _auth_state=SimpleNamespace(user_id=12345),
        gui=SimpleNamespace(channels=MagicMock()),
    )
    channel = Channel(client, id=67890, login="example_channel")
    channel._stream = Stream(channel, id=24680, game=None, viewers=1, title="Test")

    assert await channel.send_watch() is True
    assert channel.url == "https://www.twitch.tv/example_channel"
    assert requests[0] == ("GET", str(channel.url))
    assert requests[-1] == ("POST", beacon)
    assert events[0]["event"] == "minute-watched"
    assert events[0]["properties"]["user_id"] == 12345
    assert events[0]["properties"]["channel_id"] == "67890"

    requests.clear()
    assert await channel.send_watch() is True
    assert requests == [("POST", beacon)]
