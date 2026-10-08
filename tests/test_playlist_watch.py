"""Playlist watching requests media headers without downloading stream bodies."""

import asyncio
from collections import Counter, defaultdict, deque
from collections.abc import Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from urllib.parse import urlsplit

import pytest

from src.exceptions import RequestException
from src.models.channel import Channel, Stream
from src.utils.async_helpers import AwaitableValue


MEDIA = "https://video.example/live/playlist.m3u8"
USHER = "https://usher.ttvnw.net/api/channel/hls/fixture.m3u8"


def _playlist(*segments: str, sequence: int = 0) -> str:
    lines = ["#EXTM3U", "#EXT-X-TARGETDURATION:2", f"#EXT-X-MEDIA-SEQUENCE:{sequence}"]
    for segment in segments:
        lines.extend(("#EXTINF:2.0,", segment))
    return "\n".join(lines) + "\n"


@dataclass
class _Response:
    status: int = 200
    body: str = ""
    on_enter: Callable[[], None] | None = None
    pause: tuple[asyncio.Event, asyncio.Event] | None = None

    async def text(self, **kwargs) -> str:
        return self.body


class _Transport:
    """Script only metadata GETs and segment HEADs; unexpected media GETs fail."""

    def __init__(self):
        self.responses = defaultdict(deque)
        self.requests: list[tuple[str, str]] = []
        self.closed: list[tuple[str, str]] = []

    def add(self, method: str, url: str, *, error=None, **response):
        self.responses[(method, url)].append(error or _Response(**response))

    @property
    def heads(self) -> list[str]:
        return [url for method, url in self.requests if method == "HEAD"]

    @asynccontextmanager
    async def request(self, method, url, **kwargs):
        key = (method, str(url))
        self.requests.append(key)
        # Usher includes a synthetic playback token. Match its stable path here.
        route = key if key in self.responses else (
            method, urlsplit(str(url))._replace(query="").geturl(),
        )
        assert route in self.responses, f"Unexpected fixture request: {key}"
        responses = self.responses[route]
        response = responses.popleft() if len(responses) > 1 else responses[0]
        try:
            if isinstance(response, Exception):
                raise response
            if response.on_enter is not None:
                response.on_enter()
            if response.pause is not None:
                entered, release = response.pause
                entered.set()
                await release.wait()
            yield response
        finally:
            self.closed.append(key)


def _make_channel(transport: _Transport, *, master_body: str | None = None) -> Channel:
    twitch = SimpleNamespace(
        gui=SimpleNamespace(channels=MagicMock()),
        request=transport.request,
        gql_request=AsyncMock(return_value={
            "data": {"streamPlaybackAccessToken": {
                "value": "fixture-token", "signature": "fixture-signature",
            }},
        }),
        on_channel_update=MagicMock(),
        print=MagicMock(),
        watching_channel=AwaitableValue(),
    )
    channel = Channel(twitch, id=123, login="fixture")
    channel._stream = Stream(channel, id=456, game=None, viewers=1, title="Fixture")
    channel._stream._stream_url = MEDIA
    twitch.watching_channel.set(channel)
    if master_body is None:
        master_body = f"#EXTM3U\n#EXT-X-STREAM-INF:BANDWIDTH=64000\n{MEDIA}\n"
    transport.add("GET", USHER, body=master_body)
    return channel


def _channel_data(broadcast_id: int):
    return {
        "displayName": "Fixture",
        "stream": {"id": str(broadcast_id), "viewersCount": 1},
        "broadcastSettings": {"game": None, "title": "Updated fixture"},
    }


async def _refresh_channel(channel: Channel, broadcast_id: int, method: str):
    if method == "external":
        channel.external_update(_channel_data(broadcast_id), [])
    else:
        playback_token = channel._twitch.gql_request.return_value
        channel._twitch.gql_request.side_effect = lambda operation: (
            {"data": {"user": _channel_data(broadcast_id)}}
            if operation == channel.stream_gql else playback_token
        )
        assert await channel.update_stream() is True


@pytest.mark.asyncio
async def test_each_new_media_segment_is_headed_once_across_overlapping_polls():
    transport = _Transport()
    channel = _make_channel(transport)
    segments = [f"https://video.example/live/{index}.ts" for index in range(5)]
    transport.add("GET", MEDIA, body=_playlist(*segments[:3]))
    transport.add("GET", MEDIA, body=_playlist(*segments[:3]))
    transport.add("GET", MEDIA, body=_playlist(*segments[1:], sequence=1))
    for segment in segments:
        transport.add("HEAD", segment)

    assert await channel._send_watch_playlist() is True
    await channel._send_watch_playlist()
    assert await channel._send_watch_playlist() is True

    assert transport.heads == segments
    assert all(method == "HEAD" or url == MEDIA for method, url in transport.requests)


@pytest.mark.asyncio
async def test_identical_valid_playlist_remains_successful_without_duplicate_heads():
    transport = _Transport()
    channel = _make_channel(transport)
    segment = "https://video.example/live/one.ts"
    transport.add("GET", MEDIA, body=_playlist(segment))
    transport.add("HEAD", segment)

    assert await channel._send_watch_playlist() is True
    assert await channel._send_watch_playlist() is True

    assert transport.heads == [segment]


@pytest.mark.asyncio
@pytest.mark.parametrize("body", [
    "",
    "[]",
    "null",
    "true",
    "42",
    '"private-fixture-token"',
    '{"error":"private-fixture-token","url":"https://private.example/fixture"}',
    '[{"error":"private-fixture-token"}]',
    "<html>private-fixture-token</html>",
])
async def test_invalid_fresh_master_returns_no_url_without_offline_or_raw_logs(body, caplog):
    transport = _Transport()
    channel = _make_channel(transport, master_body=body)
    stream = channel._stream
    stream._stream_url = None

    assert await stream.get_stream_url() is None

    assert channel.online is True
    assert transport.heads == []
    channel._twitch.print.assert_not_called()
    assert "private-fixture-token" not in caplog.text


@pytest.mark.asyncio
async def test_fresh_master_resolves_relative_quality_uri_before_trailing_comments():
    transport = _Transport()
    body = (
        "#EXTM3U\n#EXT-X-STREAM-INF:BANDWIDTH=1280000\nhigh/playlist.m3u8\n"
        "#EXT-X-STREAM-INF:BANDWIDTH=64000\n../low/playlist.m3u8\n"
        "# trailing master comment\n"
    )
    channel = _make_channel(transport, master_body=body)
    stream = channel._stream
    stream._stream_url = None

    result = await stream.get_stream_url()

    assert str(result) == "https://usher.ttvnw.net/api/channel/low/playlist.m3u8"
    assert channel.online is True
    channel._twitch.print.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", [429, 503, RequestException()])
async def test_failed_segment_is_retried_without_repeating_successful_siblings(failure):
    transport = _Transport()
    channel = _make_channel(transport)
    first, failed, last = [f"https://video.example/live/{index}.ts" for index in range(3)]
    transport.add("GET", MEDIA, body=_playlist(first, failed, last))
    transport.add("HEAD", first)
    if isinstance(failure, Exception):
        transport.add("HEAD", failed, error=failure)
    else:
        transport.add("HEAD", failed, status=failure)
    transport.add("HEAD", failed)
    transport.add("HEAD", last)

    await channel._send_watch_playlist()
    await channel._send_watch_playlist()

    assert Counter(transport.heads) == Counter({first: 1, failed: 2, last: 1})


@pytest.mark.asyncio
async def test_relative_media_uris_and_trailing_tags_do_not_request_keys_or_maps():
    transport = _Transport()
    channel = _make_channel(transport)
    first = "https://video.example/live/one.ts"
    second = "https://video.example/two.ts?part=2"
    body = (
        '#EXTM3U\n#EXT-X-MAP:URI="init.mp4"\n'
        '#EXT-X-KEY:METHOD=AES-128,URI="key.bin"\n'
        '#EXT-X-DATERANGE:ID="ad-1",CLASS="twitch-stitched-ad"\n'
        '#EXTINF:2.0,\none.ts\n#EXT-X-DISCONTINUITY\n'
        '#EXTINF:2.0,\n../two.ts?part=2\n'
        '#EXT-X-ENDLIST\n# fixture trailing comment\n'
    )
    transport.add("GET", MEDIA, body=body)
    transport.add("HEAD", first)
    transport.add("HEAD", second)

    assert await channel._send_watch_playlist() is True

    assert transport.heads == [first, second]


@pytest.mark.asyncio
@pytest.mark.parametrize("body", [
    "",
    "#EXTM3U\n#EXT-X-ENDLIST\n",
    "#EXTM3U\n#EXT-X-TARGETDURATION:2\n",
    "[]",
    "null",
    "true",
    '{"error":"denied","url":"private-fixture-token"}',
    "<html>upstream error</html>",
    "#EXTM3U\n#EXTINF:2.0,\nfile:///private/fixture.ts\n",
])
async def test_invalid_or_empty_playlist_fails_without_segment_requests(body, caplog):
    transport = _Transport()
    channel = _make_channel(transport)
    transport.add("GET", MEDIA, body=body)

    assert await channel._send_watch_playlist() is False

    assert transport.heads == []
    assert "private-fixture-token" not in caplog.text


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [401, 403, 404])
async def test_expired_cached_playlist_is_reacquired_for_a_later_poll(status):
    transport = _Transport()
    channel = _make_channel(transport)
    expired = "https://video.example/expired/playlist.m3u8"
    channel._stream._stream_url = expired
    segment = "https://video.example/live/fresh.ts"
    transport.add("GET", expired, status=status)
    transport.add("GET", MEDIA, body=_playlist(segment))
    transport.add("HEAD", segment)

    await channel._send_watch_playlist()
    await channel._send_watch_playlist()

    assert transport.heads == [segment]
    assert transport.requests.count(("GET", expired)) == 1
    channel._twitch.gql_request.assert_awaited_once()
    assert channel.online is True


@pytest.mark.asyncio
@pytest.mark.parametrize("refresh", ["external", "async"])
async def test_same_broadcast_metadata_refresh_preserves_segment_deduplication(refresh):
    transport = _Transport()
    channel = _make_channel(transport)
    segments = ["https://video.example/live/one.ts", "https://video.example/live/two.ts"]
    transport.add("GET", MEDIA, body=_playlist(*segments))
    for segment in segments:
        transport.add("HEAD", segment)

    await channel._send_watch_playlist()
    await _refresh_channel(channel, 456, refresh)
    await channel._send_watch_playlist()

    assert transport.heads == segments


@pytest.mark.asyncio
@pytest.mark.parametrize("refresh", ["external", "async"])
async def test_different_broadcast_resets_segment_deduplication(refresh):
    transport = _Transport()
    channel = _make_channel(transport)
    segments = ["https://video.example/live/one.ts", "https://video.example/live/two.ts"]
    transport.add("GET", MEDIA, body=_playlist(*segments))
    for segment in segments:
        transport.add("HEAD", segment)

    await channel._send_watch_playlist()
    await _refresh_channel(channel, 457, refresh)
    await channel._send_watch_playlist()

    assert transport.heads == segments * 2


@pytest.mark.asyncio
async def test_many_rolling_playlists_do_not_repeat_overlapping_segments():
    transport = _Transport()
    channel = _make_channel(transport)
    segments = [f"https://video.example/live/{index}.ts" for index in range(258)]
    for start in range(256):
        transport.add("GET", MEDIA, body=_playlist(*segments[start:start + 3], sequence=start))
    for segment in segments:
        transport.add("HEAD", segment)

    for _ in range(256):
        assert await channel._send_watch_playlist() is True

    assert transport.heads == segments


@pytest.mark.asyncio
async def test_offline_channel_does_not_fetch_a_playlist():
    transport = _Transport()
    channel = _make_channel(transport)
    channel._stream = None

    assert await channel._send_watch_playlist() is False

    assert transport.requests == []


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["offline", "new_broadcast", "selected_channel"])
async def test_stream_change_during_playlist_request_prevents_stale_heads(change):
    transport = _Transport()
    channel = _make_channel(transport)
    segment = "https://video.example/live/stale.ts"

    def replace_stream():
        if change == "offline":
            channel.set_offline()
        elif change == "new_broadcast":
            channel.external_update(_channel_data(457), [])
        else:
            other = Channel(channel._twitch, id=124, login="replacement")
            channel._twitch.watching_channel.set(other)

    transport.add("GET", MEDIA, body=_playlist(segment), on_enter=replace_stream)

    assert await channel._send_watch_playlist() is False

    assert transport.heads == []


@pytest.mark.asyncio
async def test_channel_switch_during_segment_head_stops_the_remaining_batch():
    transport = _Transport()
    channel = _make_channel(transport)
    first, second = "https://video.example/live/one.ts", "https://video.example/live/two.ts"
    other = Channel(channel._twitch, id=124, login="replacement")
    transport.add("GET", MEDIA, body=_playlist(first, second))
    transport.add("HEAD", first, on_enter=lambda: channel._twitch.watching_channel.set(other))
    transport.add("HEAD", second)

    assert await channel._send_watch_playlist() is False

    assert transport.heads == [first]


@pytest.mark.asyncio
async def test_cancellation_during_segment_head_closes_request_and_propagates():
    transport = _Transport()
    channel = _make_channel(transport)
    entered, release = asyncio.Event(), asyncio.Event()
    segment = "https://video.example/live/paused.ts"
    transport.add("GET", MEDIA, body=_playlist(segment))
    transport.add("HEAD", segment, pause=(entered, release))
    task = asyncio.create_task(channel._send_watch_playlist())
    try:
        await asyncio.wait_for(entered.wait(), timeout=5)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    finally:
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)

    assert transport.heads == [segment]
    assert ("HEAD", segment) in transport.closed


@pytest.mark.asyncio
@pytest.mark.parametrize("later_sibling", [False, True])
async def test_blocked_head_has_network_budget_and_closes_without_raw_errors(
    later_sibling, monkeypatch, caplog,
):
    transport = _Transport()
    channel = _make_channel(transport)
    entered, release = asyncio.Event(), asyncio.Event()
    blocked = "https://video.example/live/blocked.ts?signature=private-fixture-token"
    later = "https://video.example/live/later.ts"
    segments = [blocked, later] if later_sibling else [blocked]
    transport.add("GET", MEDIA, body=_playlist(*segments))
    transport.add("HEAD", blocked, pause=(entered, release))
    # The old newest-only implementation reaches this sibling first. Wake the
    # assertion there too, so missing budgets fail without a test-side timeout.
    transport.add("HEAD", later, on_enter=entered.set)

    loop = asyncio.get_running_loop()
    original_call_at = loop.call_at
    scheduled = []

    def capture_timeout(when, callback, *args, context=None):
        handle = original_call_at(when, callback, *args, context=context)
        if isinstance(getattr(callback, "__self__", None), asyncio.Timeout):
            scheduled.append((when, callback, args, handle))
        return handle

    monkeypatch.setattr(loop, "call_at", capture_timeout)
    task = asyncio.create_task(channel._send_watch_playlist())
    try:
        # Only production code can install a deadline. No test-created wait_for
        # or wall-clock sleep can make the missing-budget assertion pass.
        await entered.wait()
        active = [deadline for deadline in scheduled if not deadline[3].cancelled()]
        assert active, "Playlist watching must bound blocked network requests"
        if later_sibling:
            assert len(active) >= 2, "A per-request budget must fit within the whole-poll budget"
            assert min(item[0] for item in active) < max(item[0] for item in active)
        _, callback, args, handle = min(active, key=lambda item: item[0])
        handle.cancel()
        callback(*args)
        result = await task
        if not later_sibling:
            assert result is False
    finally:
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)

    assert transport.heads == segments
    assert ("HEAD", blocked) in transport.closed
    channel._twitch.print.assert_not_called()
    assert "private-fixture-token" not in caplog.text


@pytest.mark.asyncio
@pytest.mark.parametrize('master_body', [
    _playlist('https://video.example/live/media.ts'),
    '#EXTM3U\n#EXT-X-STREAM-INF:BANDWIDTH=64000\nhttps://video.example/live/media.ts\n',
])
async def test_unexpected_master_cannot_cause_media_body_download(master_body):
    transport = _Transport()
    channel = _make_channel(transport)
    channel._stream._stream_url = None
    transport.responses[('GET', USHER)].clear()
    transport.add('GET', USHER, body=master_body)
    transport.add('GET', 'https://video.example/live/media.ts', body='fixture-media-body')

    assert await channel.send_watch() is False
    assert all(method != 'GET' or not url.endswith('.ts') for method, url in transport.requests)
