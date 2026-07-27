import base64
import json
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from src.exceptions import RequestException
from src.models.channel import Channel, Stream


def _decode_spade_payload(payload):
    return json.loads(base64.b64decode(payload["data"]).decode("utf8"))


class _FakeResponse:
    def __init__(self, status: int):
        self.status = status


class _FakeRequestCM:
    def __init__(self, response: _FakeResponse):
        self._response = response

    async def __aenter__(self):
        return self._response

    async def __aexit__(self, exc_type, exc, tb):
        return False


def _make_stream(twitch) -> Stream:
    channel = MagicMock(spec=Channel)
    channel.id = 67890
    channel._login = "example_channel"
    channel._twitch = twitch
    return Stream(
        channel,
        id=24680,
        game={"id": "13579", "name": "Example Game"},
        viewers=100,
        title="Example Stream",
    )


class TestSpadeWatchEvents(unittest.IsolatedAsyncioTestCase):
    def test_spade_payload_contains_minute_watched_event(self):
        twitch = MagicMock()
        twitch._auth_state.user_id = "12345"
        stream = _make_stream(twitch)

        events = _decode_spade_payload(stream._spade_payload)

        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["event"], "minute-watched")
        properties = events[0]["properties"]
        self.assertEqual(properties["broadcast_id"], "24680")
        self.assertEqual(properties["channel_id"], "67890")
        self.assertEqual(properties["channel"], "example_channel")
        self.assertEqual(properties["game"], "Example Game")
        self.assertEqual(properties["game_id"], "13579")
        self.assertEqual(properties["location"], "channel")
        self.assertEqual(properties["player"], "site")
        self.assertIs(properties["is_live"], True)
        self.assertEqual(properties["minutes_logged"], 1)
        self.assertEqual(properties["user_id"], 12345)
        self.assertIsInstance(properties["user_id"], int)
        self.assertRegex(properties["client_time"], r"^\d{4}-\d{2}-\d{2}T.*Z$")

    def test_stream_spade_payload_is_not_cached(self):
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

        first_payload = stream._spade_payload
        second_payload = stream._spade_payload

        self.assertIsNot(first_payload, second_payload)
        first_payload["data"] = "mutated"

        self.assertNotEqual(second_payload["data"], "mutated")

    async def test_send_watch_posts_to_spade_url_and_returns_true_for_204(self):
        twitch = MagicMock()
        twitch.gui.channels = MagicMock()
        twitch._auth_state.user_id = "12345"
        twitch.request = MagicMock(return_value=_FakeRequestCM(_FakeResponse(204)))
        channel = Channel(twitch, id=67890, login="example_channel")
        channel._spade_url = "https://spade.twitch.tv/"
        channel._stream = Stream(
            channel,
            id=24680,
            game={"id": "13579", "name": "Example Game"},
            viewers=100,
            title="Example Stream",
        )

        result = await channel.send_watch()

        self.assertTrue(result)
        twitch.request.assert_called_once_with(
            "POST", channel._spade_url, data=channel._stream._spade_payload
        )

    async def test_send_watch_fetches_spade_url_when_missing(self):
        twitch = MagicMock()
        twitch.gui.channels = MagicMock()
        twitch._auth_state.user_id = "12345"
        twitch.request = MagicMock(return_value=_FakeRequestCM(_FakeResponse(204)))
        channel = Channel(twitch, id=67890, login="example_channel")
        channel._stream = Stream(
            channel,
            id=24680,
            game={"id": "13579", "name": "Example Game"},
            viewers=100,
            title="Example Stream",
        )
        with patch.object(
            Channel, "get_spade_url", AsyncMock(return_value="https://spade.twitch.tv/fetched")
        ) as mock_get_spade_url:
            result = await channel.send_watch()

        self.assertTrue(result)
        mock_get_spade_url.assert_awaited_once()
        self.assertEqual(channel._spade_url, "https://spade.twitch.tv/fetched")

    async def test_send_watch_returns_false_when_spade_url_fetch_fails(self):
        from src.exceptions import MinerException

        twitch = MagicMock()
        twitch.gui.channels = MagicMock()
        twitch._auth_state.user_id = "12345"
        twitch.request = MagicMock(return_value=_FakeRequestCM(_FakeResponse(204)))
        channel = Channel(twitch, id=67890, login="example_channel")
        channel._stream = Stream(
            channel,
            id=24680,
            game={"id": "13579", "name": "Example Game"},
            viewers=100,
            title="Example Stream",
        )

        with patch.object(Channel, "get_spade_url", AsyncMock(side_effect=MinerException("fail"))):
            self.assertFalse(await channel.send_watch())
    async def test_send_watch_returns_false_for_non_204_status(self):
        twitch = MagicMock()
        twitch.gui.channels = MagicMock()
        twitch._auth_state.user_id = "12345"
        twitch.request = MagicMock(return_value=_FakeRequestCM(_FakeResponse(400)))
        channel = Channel(twitch, id=67890, login="example_channel")
        channel._spade_url = "https://spade.twitch.tv/"
        channel._stream = Stream(
            channel,
            id=24680,
            game={"id": "13579", "name": "Example Game"},
            viewers=100,
            title="Example Stream",
        )

        self.assertFalse(await channel.send_watch())

    async def test_send_watch_returns_false_without_stream(self):
        twitch = MagicMock()
        twitch.gui.channels = MagicMock()
        channel = Channel(twitch, id=67890, login="example_channel")

        self.assertFalse(await channel.send_watch())

    async def test_send_watch_returns_false_when_request_fails(self):
        twitch = MagicMock()
        twitch.gui.channels = MagicMock()
        twitch._auth_state.user_id = "12345"
        twitch.request = MagicMock(side_effect=RequestException())
        channel = Channel(twitch, id=67890, login="example_channel")
        channel._spade_url = "https://spade.twitch.tv/"
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
