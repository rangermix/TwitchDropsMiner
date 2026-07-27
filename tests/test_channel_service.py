import unittest
from unittest.mock import AsyncMock, MagicMock

from src.config import GQLOperation
from src.models.channel import Channel
from src.services.channel_service import ChannelService


class TestChannelService(unittest.IsolatedAsyncioTestCase):
    async def test_bulk_check_online_updates_channels_from_single_use_iterable(self) -> None:
        twitch = MagicMock()
        channel = MagicMock(spec=Channel)
        channel.id = 123
        channel.stream_gql = GQLOperation("GetStreamInfo", "hash")
        channel_data = {
            "id": "123",
            "stream": {"id": "456"},
            "broadcastSettings": {
                "game": {"id": "789", "name": "Example Game"},
                "title": "Example Stream",
            },
        }
        twitch.gql_request = AsyncMock(return_value=[{"data": {"user": channel_data}}])

        await ChannelService(twitch).bulk_check_online(iter([channel]))

        twitch.gql_request.assert_awaited_once_with([channel.stream_gql])
        channel.external_update.assert_called_once_with(channel_data, [])
