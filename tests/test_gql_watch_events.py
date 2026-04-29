import base64
import gzip
import json
import unittest
from unittest.mock import AsyncMock, MagicMock

from src.config.constants import GQLQuery
from src.exceptions import RequestException
from src.models.channel import Channel, Stream


def _decode_gql_events(operation: GQLQuery):
    encoded = operation["variables"]["input"]["data"]
    return json.loads(gzip.decompress(base64.b64decode(encoded)).decode("utf8"))


class TestGQLWatchEvents(unittest.IsolatedAsyncioTestCase):
    def test_gql_query_wraps_gzip_base64_payload(self):
        event_payload = [{"event": "minute-watched", "properties": {"channel": "test"}}]
        compressed = base64.b64encode(gzip.compress(json.dumps(event_payload).encode("utf8"))).decode(
            "utf8"
        )

        operation = GQLQuery("mutation Example { ok }", compressed)

        self.assertEqual(operation["query"], "mutation Example { ok }")
        self.assertEqual(operation["variables"]["input"]["repository"], "twilight")
        self.assertEqual(operation["variables"]["input"]["encoding"], "GZIP_B64")
        self.assertEqual(_decode_gql_events(operation), event_payload)

    def test_stream_gql_payload_contains_minute_watched_event(self):
        twitch = MagicMock()
        twitch._auth_state.user_id = 12345
        channel = MagicMock(spec=Channel)
        channel.id = 67890
        channel._login = "example_channel"
        channel._twitch = twitch
        stream = Stream(
            channel,
            id=24680,
            game={"id": "13579", "name": "Example Game"},
            viewers=100,
            title="Example Stream",
        )

        operation = stream._gql_payload
        events = _decode_gql_events(operation)

        self.assertIn("mutation SendEvents", operation["query"])
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["event"], "minute-watched")
        properties = events[0]["properties"]
        self.assertEqual(properties["broadcast_id"], "24680")
        self.assertEqual(properties["channel_id"], "67890")
        self.assertEqual(properties["channel"], "example_channel")
        self.assertEqual(properties["game"], "Example Game")
        self.assertEqual(properties["game_id"], "13579")
        self.assertEqual(properties["minutes_logged"], 1)
        self.assertEqual(properties["user_id"], 12345)
        self.assertRegex(properties["client_time"], r"^\d{4}-\d{2}-\d{2}T.*Z$")

    async def test_send_watch_uses_gql_and_returns_true_for_204(self):
        twitch = MagicMock()
        twitch.gui.channels = MagicMock()
        twitch._auth_state.user_id = 12345
        twitch.gql_request = AsyncMock(return_value={"data": {"sendSpadeEvents": {"statusCode": 204}}})
        channel = Channel(twitch, id=67890, login="example_channel")
        channel._stream = Stream(
            channel,
            id=24680,
            game={"id": "13579", "name": "Example Game"},
            viewers=100,
            title="Example Stream",
        )

        result = await channel.send_watch()

        self.assertTrue(result)
        twitch.gql_request.assert_awaited_once()

    async def test_send_watch_returns_false_without_stream(self):
        twitch = MagicMock()
        twitch.gui.channels = MagicMock()
        channel = Channel(twitch, id=67890, login="example_channel")

        self.assertFalse(await channel.send_watch())

    async def test_send_watch_returns_false_when_gql_request_fails(self):
        twitch = MagicMock()
        twitch.gui.channels = MagicMock()
        twitch._auth_state.user_id = 12345
        twitch.gql_request = AsyncMock(side_effect=RequestException())
        channel = Channel(twitch, id=67890, login="example_channel")
        channel._stream = Stream(
            channel,
            id=24680,
            game={"id": "13579", "name": "Example Game"},
            viewers=100,
            title="Example Stream",
        )

        self.assertFalse(await channel.send_watch())


if __name__ == "__main__":
    unittest.main()
