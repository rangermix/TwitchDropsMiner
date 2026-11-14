"""A watched channel that ended without a stream-down event must still be replaced."""

import asyncio
import logging
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import aiohttp
import pytest

from src.config import State
from src.exceptions import ExitRequest, MinerException
from src.i18n import _
from src.models.channel import Channel, Stream
from src.models.game import Game
from src.services.watch_service import WatchService
from src.utils.async_helpers import AwaitableValue
from tests.test_session_lifecycle import miner


GAME = {"id": "1", "name": "Test Game"}
OFFLINE = {"data": {"user": {"id": "1", "displayName": "Watched", "stream": None}}}
LIVE = {"data": {"user": {
    "id": "1", "displayName": "Watched",
    "stream": {"id": "1", "viewersCount": 5},
    "broadcastSettings": {"game": GAME, "title": "Live"},
}}}


def _channel(client, channel_id):
    channel = Channel(client, id=channel_id, login=f"channel{channel_id}")
    channel._stream = Stream(channel, id=channel_id, game=GAME, viewers=100, title="Live")
    return channel


def _reloading_miner(tmp_path, monkeypatch, *, discovered, stream_status):
    """Run CHANNELS_FETCH with a watched channel that discovery may no longer return."""
    client = miner(tmp_path, monkeypatch)
    client._state = State.CHANNELS_FETCH
    client._inventory_loaded = True
    client._ensure_api_clients = MagicMock()
    client.request_inventory_refresh = MagicMock()
    client.get_auth = AsyncMock(return_value=SimpleNamespace(user_id=42))
    client.websocket.start = AsyncMock()
    client.websocket.remove_topics = MagicMock()
    client.websocket.add_topics = MagicMock()
    client._watch_service.watch_loop = AsyncMock()
    client.get_active_campaign = MagicMock(return_value=None)
    client.gui.channels.get_selection.return_value = None
    client._channel_service.filter_banned_channels = AsyncMock(
        side_effect=lambda channels, retained_channels: list(channels)
    )
    game = Game(GAME)
    client.wanted_games = [game]
    client.inventory = [SimpleNamespace(
        game=game, can_earn_within=lambda stamp: True, allowed_channels=[],
        can_earn=lambda channel: True,
    )]
    client.gql_request = AsyncMock(side_effect=stream_status)
    client.get_live_streams = AsyncMock(return_value=[_channel(client, i) for i in discovered])
    watched = _channel(client, 1)
    client.watching_channel.set(watched)
    original_watch = client.watch

    def finish_after_selection(channel, *, update_status=True):
        original_watch(channel, update_status=update_status)
        asyncio.get_running_loop().call_soon(client.close)

    client.watch = finish_after_selection
    return client, watched


async def _run(client):
    try:
        await asyncio.wait_for(client._run(), 1)
    finally:
        if client._watching_task is not None:
            client._watching_task.cancel()


@pytest.mark.asyncio
async def test_reload_replaces_watched_channel_that_ended_unnoticed(tmp_path, monkeypatch):
    client, watched = _reloading_miner(
        tmp_path, monkeypatch, discovered=[2], stream_status=[OFFLINE],
    )
    client.print = MagicMock()
    await _run(client)
    assert not watched.online
    assert client.watching_channel.get_with_default(None).id == 2
    assert set(client.channels) == {2}
    client.print.assert_any_call(_.t["status"]["goes_offline"].format(channel=watched.name))


@pytest.mark.asyncio
@pytest.mark.parametrize("acl", [False, True])
async def test_reload_does_not_restore_a_discovered_channel_confirmed_offline(
    tmp_path, monkeypatch, acl,
):
    client, watched = _reloading_miner(
        tmp_path, monkeypatch, discovered=[1, 2], stream_status=[OFFLINE],
    )
    found = client.get_live_streams.return_value[0]
    found.acl_based = acl
    await _run(client)

    assert not watched.online
    assert not found.online
    assert client.watching_channel.get_with_default(None).id == 2
    assert set(client.channels) == ({1, 2} if acl else {2})
    if acl:
        assert client.channels[1] is found


@pytest.mark.asyncio
async def test_reload_keeps_tracking_live_channel_outside_directory_results(tmp_path, monkeypatch):
    client, watched = _reloading_miner(
        tmp_path, monkeypatch, discovered=[2], stream_status=[LIVE],
    )
    await _run(client)
    assert client.watching_channel.get_with_default(None) is watched
    assert client.channels[1] is watched
    # Without its topics a later stream-down event could never arrive.
    topics = {str(topic) for topic in client.websocket.add_topics.call_args.args[0]}
    assert {"video-playback-by-id.1", "broadcast-settings-update.1"} <= topics


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", [
    MinerException("failed"), aiohttp.ClientError(), TimeoutError(), KeyError("data"),
    TypeError(), IndexError(), ValueError(),
])
async def test_failed_reload_recheck_keeps_known_state(tmp_path, monkeypatch, failure):
    client, watched = _reloading_miner(
        tmp_path, monkeypatch, discovered=[2], stream_status=failure,
    )
    await _run(client)
    assert watched.online
    assert client.watching_channel.get_with_default(None) is watched
    assert client.channels[1] is watched


@pytest.mark.asyncio
async def test_discovered_channel_replaces_watched_object(tmp_path, monkeypatch):
    client, watched = _reloading_miner(
        tmp_path, monkeypatch, discovered=[1, 2], stream_status=[LIVE],
    )
    await _run(client)
    assert client.channels[1] is not watched
    assert client.watching_channel.get_with_default(None) is client.channels[1]
    assert client._manual_target_channel is None


@pytest.mark.asyncio
async def test_fresh_stream_updates_discovered_channel_without_losing_acl(
    tmp_path, monkeypatch,
):
    client, _watched = _reloading_miner(
        tmp_path, monkeypatch, discovered=[1, 2], stream_status=[LIVE],
    )
    found = client.get_live_streams.return_value[0]
    found.acl_based = True
    found._stream.title = "Old directory title"
    found._stream.drops_enabled = False
    await client._channel_service.refresh_watched_channel({found})

    assert found.acl_based
    assert found._stream.title == "Live"
    assert found._stream.channel is found
    assert not found.drops_enabled


@pytest.mark.asyncio
async def test_fresh_category_wins_even_for_the_same_broadcast(tmp_path, monkeypatch):
    other_game = {"id": "2", "name": "Other Game"}
    reply = {"data": {"user": {
        **LIVE["data"]["user"], "broadcastSettings": {"game": other_game, "title": "Live"},
    }}}
    client, watched = _reloading_miner(
        tmp_path, monkeypatch, discovered=[1, 2], stream_status=[reply],
    )
    campaign = client.inventory[0]
    campaign.can_earn = lambda channel: channel.game == campaign.game
    found = client.get_live_streams.return_value[0]

    await _run(client)

    assert found.game == watched.game == Game(other_game)
    assert client.channels[1] is found
    assert client.watching_channel.get_with_default(None).id == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("user", [
    None, {"displayName": "Watched", "stream": None},
])
async def test_reload_preserves_state_on_errored_null_response(tmp_path, monkeypatch, user):
    error = {"message": "server error", "path": ["user"]}
    client, watched = _reloading_miner(
        tmp_path, monkeypatch, discovered=[2],
        stream_status=[{"data": {"user": user}, "errors": [error]}],
    )
    await _run(client)

    assert watched.online
    assert client.watching_channel.get_with_default(None) is watched


@pytest.mark.asyncio
async def test_discovery_keeps_candidate_if_watched_check_is_unknown(tmp_path, monkeypatch):
    client, watched = _reloading_miner(
        tmp_path, monkeypatch, discovered=[1, 2], stream_status=TimeoutError(),
    )
    found = client.get_live_streams.return_value[0]
    found._stream.title = "Directory title"
    await _run(client)

    assert watched.online
    assert client.watching_channel.get_with_default(None) is found
    assert found._stream.title == "Directory title"


@pytest.mark.asyncio
async def test_manual_target_follows_the_tracked_channel(tmp_path, monkeypatch):
    client, watched = _reloading_miner(
        tmp_path, monkeypatch, discovered=[1, 2], stream_status=[LIVE],
    )
    client._manual_target_channel = watched
    client._manual_target_game = watched.game
    await _run(client)
    assert client._manual_target_channel is client.channels[1]


@pytest.mark.asyncio
@pytest.mark.parametrize("watching", [True, False])
async def test_other_manual_target_follows_its_tracked_channel(tmp_path, monkeypatch, watching):
    client, watched = _reloading_miner(
        tmp_path, monkeypatch, discovered=[1, 2], stream_status=[LIVE],
    )
    if not watching:
        client.watching_channel.clear()
    client._manual_target_channel = target = _channel(client, 2)
    client._manual_target_game = watched.game
    await _run(client)
    assert client._manual_target_channel is client.channels[2]
    assert client._manual_target_channel is not target


@pytest.mark.asyncio
async def test_manual_target_outside_tracked_channels_is_kept(tmp_path, monkeypatch):
    client, watched = _reloading_miner(
        tmp_path, monkeypatch, discovered=[1, 2], stream_status=[LIVE],
    )
    client._manual_target_channel = target = _channel(client, 5)
    client._manual_target_game = watched.game
    await _run(client)
    assert client._manual_target_channel is target


@pytest.mark.asyncio
async def test_ended_manual_mode_switches_to_another_wanted_channel(tmp_path, monkeypatch):
    client, watched = _reloading_miner(
        tmp_path, monkeypatch, discovered=[2], stream_status=[OFFLINE],
    )
    client._manual_target_channel = watched
    client._manual_target_game = Game({"id": "2", "name": "Other Game"})
    await _run(client)
    assert not client.is_manual_mode()
    assert client.watching_channel.get_with_default(None) is client.channels[2]


@pytest.mark.asyncio
async def test_selected_channel_is_not_overridden_by_auto_selection(tmp_path, monkeypatch):
    client, _watched = _reloading_miner(tmp_path, monkeypatch, discovered=[2, 3], stream_status=[])
    client.watching_channel.clear()
    selected = client.get_live_streams.return_value[1]
    selected.viewers = 1
    client.gui.channels.get_selection.return_value = selected
    await _run(client)
    assert client.watching_channel.get_with_default(None) is selected
    assert not client.is_manual_mode()


@pytest.mark.asyncio
async def test_failed_check_logs_only_the_exception_class(tmp_path, monkeypatch, caplog):
    client = miner(tmp_path, monkeypatch)
    client.gql_request = AsyncMock(side_effect=KeyError("remote text"))
    with caplog.at_level(logging.WARNING, logger="TwitchDrops"):
        await _channel(client, 1).refresh_stream()
    assert "KeyError" in caplog.text
    assert "remote text" not in caplog.text


@pytest.mark.asyncio
async def test_state_change_errors_are_not_reported_as_failed_checks(tmp_path, monkeypatch):
    client = miner(tmp_path, monkeypatch)
    client.gql_request = AsyncMock(return_value=OFFLINE)
    client.on_channel_update = MagicMock(side_effect=TypeError)
    with pytest.raises(TypeError):
        await _channel(client, 1).refresh_stream()


@pytest.mark.asyncio
@pytest.mark.parametrize("check", ["refresh_stream", "_online_delay"])
async def test_stalled_stream_check_is_bounded_and_keeps_known_state(
    tmp_path, monkeypatch, check,
):
    client = miner(tmp_path, monkeypatch)
    monkeypatch.setattr("src.models.channel.ONLINE_DELAY", timedelta(0))
    monkeypatch.setattr("src.models.channel.STREAM_CHECK_TIMEOUT", 0.01)
    cancelled = []

    async def stall(ops):
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            cancelled.append(ops)
            raise

    client.gql_request = stall
    watched = _channel(client, 1)
    await asyncio.wait_for(getattr(watched, check)(), 1)
    assert cancelled
    assert watched.online


@pytest.mark.asyncio
async def test_offline_event_during_reload_invalidates_live_reply(tmp_path, monkeypatch):
    client = miner(tmp_path, monkeypatch)
    watched = _channel(client, 1)
    candidate = _channel(client, 1)
    client.watching_channel.set(watched)
    candidates = {candidate}
    entered, release = asyncio.Event(), asyncio.Event()

    async def old_reply(_operation):
        entered.set()
        await release.wait()
        return LIVE

    client.gql_request = old_reply
    async with asyncio.TaskGroup() as tasks:
        tasks.create_task(client._channel_service.refresh_watched_channel(candidates))
        await asyncio.wait_for(entered.wait(), 1)
        watched.set_offline()
        release.set()

    assert not watched.online
    assert not candidates


@pytest.mark.asyncio
async def test_newer_lookup_wins_when_overlapping_checks_finish_out_of_order(
    tmp_path, monkeypatch,
):
    client = miner(tmp_path, monkeypatch)
    watched = _channel(client, 1)
    entered, release = asyncio.Event(), asyncio.Event()

    async def old_reply(_operation):
        entered.set()
        await release.wait()
        return LIVE

    client.gql_request = old_reply
    async with asyncio.TaskGroup() as tasks:
        tasks.create_task(watched.refresh_stream())
        await asyncio.wait_for(entered.wait(), 1)
        client.gql_request = AsyncMock(return_value=OFFLINE)
        await watched.refresh_stream()
        release.set()

    assert not watched.online


@pytest.mark.asyncio
async def test_reload_offline_is_applied_while_newer_delayed_check_waits(tmp_path, monkeypatch):
    client = miner(tmp_path, monkeypatch)
    monkeypatch.setattr("src.models.channel.ONLINE_DELAY", timedelta(0))
    watched = _channel(client, 1)
    client.watching_channel.set(watched)
    candidates = {_channel(client, 1)}
    reload_entered, reload_release = asyncio.Event(), asyncio.Event()
    delayed_entered, delayed_cancelled = asyncio.Event(), asyncio.Event()

    async def reload_reply(_operation):
        reload_entered.set()
        await reload_release.wait()
        return OFFLINE

    async def delayed_reply(_operation):
        delayed_entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            delayed_cancelled.set()

    client.gql_request = reload_reply
    async with asyncio.TaskGroup() as tasks:
        tasks.create_task(client._channel_service.refresh_watched_channel(candidates))
        await asyncio.wait_for(reload_entered.wait(), 1)
        client.gql_request = delayed_reply
        watched.check_online()
        check, = client._channel_tasks
        try:
            await asyncio.wait_for(delayed_entered.wait(), 1)
            reload_release.set()
            await asyncio.wait_for(delayed_cancelled.wait(), 1)
        finally:
            if not check.done():
                check.cancel()
            await asyncio.gather(check, return_exceptions=True)

    assert not watched.online
    assert not candidates


@pytest.mark.asyncio
async def test_retiring_topics_cancels_the_inflight_online_check(tmp_path, monkeypatch):
    client = miner(tmp_path, monkeypatch)
    monkeypatch.setattr("src.models.channel.ONLINE_DELAY", timedelta(0))
    watched = _channel(client, 1)
    entered, cancelled = asyncio.Event(), asyncio.Event()

    async def blocked_reply(_operation):
        entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    client.gql_request = AsyncMock(side_effect=blocked_reply)
    watched.check_online()
    check, = client._channel_tasks
    try:
        await asyncio.wait_for(entered.wait(), 1)
        watched.check_online()
        assert client._channel_tasks == {check}
        assert client.gql_request.await_count == 1
        client._remove_channel_topics([watched])
        result, = await asyncio.wait_for(asyncio.gather(check, return_exceptions=True), 1)
        assert isinstance(result, asyncio.CancelledError)
        assert cancelled.is_set()
    finally:
        for task in client._channel_tasks:
            task.cancel()
        await asyncio.gather(*client._channel_tasks, return_exceptions=True)


@pytest.mark.asyncio
async def test_reload_recheck_propagates_exit(tmp_path, monkeypatch):
    client, _watched = _reloading_miner(
        tmp_path, monkeypatch, discovered=[2], stream_status=ExitRequest(),
    )
    with pytest.raises(ExitRequest):
        await client._channel_service.refresh_watched_channel(set())


@pytest.mark.asyncio
@pytest.mark.parametrize("succeeded,current,checked", [
    (False, True, True), (True, True, False), (False, False, False),
])
async def test_only_failed_watch_of_current_stream_rechecks_channel(succeeded, current, checked):
    channel = MagicMock(online=True, banned=False)
    channel._watch_current.return_value = current
    channel.send_watch = AsyncMock(return_value=succeeded)
    selected = AwaitableValue()
    selected.set(channel)
    service = WatchService(SimpleNamespace(
        is_mining_time=lambda: True, watching_channel=selected,
    ))
    service.watch_sleep = AsyncMock(side_effect=[None, asyncio.CancelledError])
    with pytest.raises(asyncio.CancelledError):
        await service.watch_loop()
    assert channel.check_online.called is checked


@pytest.mark.asyncio
async def test_failed_watch_detects_offline_channel_without_stream_down(tmp_path, monkeypatch):
    client = miner(tmp_path, monkeypatch)
    monkeypatch.setattr("src.models.channel.ONLINE_DELAY", timedelta(0))
    monkeypatch.setattr(Channel, "send_watch", AsyncMock(return_value=False))
    client.gql_request = AsyncMock(return_value=OFFLINE)
    watched = _channel(client, 1)
    client.watching_channel.set(watched)
    service = client._watch_service
    service.watch_sleep = lambda delay: asyncio.sleep(0)
    task = asyncio.create_task(service.watch_loop())
    try:
        async with asyncio.timeout(1):
            while client.watching_channel.get_with_default(None) is not None:
                await asyncio.sleep(0)
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
    assert not watched.online
    assert client._state is State.CHANNEL_SWITCH
