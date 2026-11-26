"""Two channels at a time (watch_slots 2): idle watching in side mode next to drop mining."""

import asyncio
import logging
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

import src.services.idle_watch as idle_mod
from src.config import State
from src.core.client import Twitch
from src.services.message_handlers import MessageHandlerService
from src.utils.async_helpers import AwaitableValue
from tests.test_idle_watch import client_stub, fake, loop_service
from tests.test_playlist_watch import _make_channel, _playlist, _Transport


def chan(cid, login, *, drops=False):
    c = fake(cid, cid * 10, login=login)
    c.name, c.drops_enabled = login.upper(), drops
    c._stream = object()
    c.send_watch = AsyncMock(return_value=True)
    c._watch_current = MagicMock(return_value=True)
    c.check_online = MagicMock()
    return c


def side_service(live, *, drop=None, mining=True):
    """A loop_service with watch_slots 2 while drop mining watches drop (None: nothing yet)."""
    svc, tw = loop_service(live, mining=mining, idle=tuple(c.login for c in live))
    tw.settings.watch_slots = 2
    tw._changing_auth = False
    tw._watching = drop
    return svc, tw


async def settle(seconds=0.03):
    await asyncio.sleep(seconds)


async def stopped(svc):
    await asyncio.gather(svc.stop(), return_exceptions=True)


@pytest.fixture(autouse=True)
def fast(monkeypatch):
    monkeypatch.setattr(idle_mod, "SIDE_WATCH_SECONDS", 0.005)
    monkeypatch.setattr(idle_mod, "IDLE_TICK_SECONDS", 0.01)


# --- side mode watches in its own slot ---


@pytest.mark.asyncio
async def test_side_mode_watches_its_pick_in_the_second_slot_and_leaves_the_drop_watch_alone(caplog):
    a, drop = chan(1, "a"), chan(9, "d", drops=True)
    svc, tw = side_service([a], drop=drop)
    with caplog.at_level(logging.INFO, logger="TwitchDrops"):
        svc.enter(side=True)
        await settle()
    assert svc.active and svc.mode == "side"
    assert svc.side_channel is a and svc.watched is a
    assert a.send_watch.await_count >= 2
    assert all(c.kwargs == {"side": True} for c in a.send_watch.await_args_list)
    # the drop watch, its channel list entry, drop panel and status bar are untouched
    tw.watch.assert_not_called()
    tw.stop_watching.assert_not_called()
    tw.change_state.assert_not_called()
    tw.gui.channels.set_watching.assert_not_called()
    tw.gui.clear_drop.assert_not_called()
    tw.gui.status.update.assert_not_called()
    assert tw._watching is drop
    assert "Idle watching started in the second slot" in caplog.text
    await stopped(svc)
    assert svc.side_channel is None and svc._side_task is None
    tw.stop_watching.assert_not_called()


@pytest.mark.asyncio
async def test_drop_usable_drop_channel_and_drops_enabled_channels_are_never_side_picks():
    drop, usable, enabled, ok = chan(1, "d"), chan(2, "u"), chan(3, "e", drops=True), chan(4, "ok")
    svc, tw = side_service([drop, usable, enabled, ok], drop=drop)
    tw.can_watch.side_effect = lambda c: c is usable
    tw.settings.follow_raids = True
    svc.enter(side=True)
    await settle()
    assert svc.side_channel is ok
    for channel in (drop, usable, enabled):
        assert not svc.side_candidate(channel)
        assert not svc.may_act(channel, "follow_raids"), channel.login
        channel.send_watch.assert_not_called()
    assert svc.may_act(ok, "follow_raids")
    # without a candidate nothing is side watched, and the drop watch stays
    ok.drops_enabled = True
    svc.poke()
    await settle()
    assert svc.side_channel is None and svc.watched is None
    tw.stop_watching.assert_not_called()
    assert tw._watching is drop
    await stopped(svc)


def test_may_act_is_unchanged_in_main_mode():
    # the drop channel rule is side mode only: in main mode the watched channel is the idle pick
    a = chan(1, "a", drops=True)
    svc, tw = side_service([a], drop=a)
    tw.settings.follow_raids = True
    svc._task = MagicMock(done=lambda: False)
    assert svc.may_act(a, "follow_raids")
    svc.mode = "side"
    assert not svc.may_act(a, "follow_raids")


# --- the side watcher ---


def stop_offline(channel, tw):
    channel.online = False


def stop_banned(channel, tw):
    channel.banned = True


def stop_mining(channel, tw):
    tw.is_mining_time.side_effect = lambda: False


def stop_drops(channel, tw):
    channel.drops_enabled = True


@pytest.mark.asyncio
@pytest.mark.parametrize("end", [stop_offline, stop_banned, stop_mining, stop_drops])
async def test_side_watcher_stops_on_offline_ban_end_of_mining_time_or_drops(end):
    a = chan(1, "a")
    svc, tw = side_service([a])
    svc.poke = MagicMock()
    svc._side_start(a)
    await settle()
    task = svc._side_task
    calls = a.send_watch.await_count
    assert calls >= 2 and not task.done()
    end(a, tw)
    await settle()
    assert task.done() and not task.cancelled()
    assert svc.side_channel is None and svc._side_task is None
    svc.poke.assert_called()  # the loop picks again
    await settle()
    assert a.send_watch.await_count <= calls + 1


@pytest.mark.asyncio
async def test_a_failed_side_watch_of_the_current_stream_rechecks_the_channel():
    a = chan(1, "a")
    a.send_watch.return_value = False
    svc, _ = side_service([a])
    svc._side_start(a)
    await settle()
    a.check_online.assert_called()
    assert all(c.kwargs == {"side": True} for c in a._watch_current.call_args_list)
    svc._side_stop()


@pytest.mark.asyncio
async def test_switching_the_side_channel_stops_the_old_watcher_first():
    a, b = chan(1, "a"), chan(2, "b")
    svc, tw = side_service([a, b])
    svc._side_start(a)
    await settle()
    old = svc._side_task
    svc._side_start(b)
    await settle()
    assert old.cancelled()
    assert svc.side_channel is b and not svc._side_task.done()
    count = a.send_watch.await_count
    await settle()
    assert a.send_watch.await_count == count
    svc._side_stop()
    await settle()
    assert svc.side_channel is None


@pytest.mark.asyncio
async def test_side_watch_requests_go_through_the_channel_only_while_it_is_the_side_channel():
    transport = _Transport()
    channel = _make_channel(transport)
    segment = "https://video.example/live/0.ts"
    transport.add("GET", "https://video.example/live/playlist.m3u8", body=_playlist(segment))
    transport.add("HEAD", segment)
    tw = channel._twitch
    tw.idle_watch = SimpleNamespace(side_channel=None)
    # the drop watch: unchanged, and the side flag refuses the drop channel
    assert channel._watch_current(channel._stream)
    tw.idle_watch.side_channel = channel
    assert not channel._watch_current(channel._stream, side=True)
    # the second slot: another channel is the drop watch
    tw.watching_channel = AwaitableValue()
    tw.watching_channel.set(SimpleNamespace(id=999))
    assert not channel._watch_current(channel._stream)
    assert channel._watch_current(channel._stream, side=True)
    assert await channel._send_watch_playlist(side=True) is True
    assert transport.heads == [segment]
    assert await channel._send_watch_playlist() is False
    tw.idle_watch.side_channel = None
    assert not channel._watch_current(channel._stream, side=True)


# --- transitions ---


@pytest.mark.asyncio
async def test_entering_main_mode_stops_the_side_watcher_before_the_client_watches():
    a, drop = chan(1, "a"), chan(9, "d", drops=True)
    svc, tw = side_service([a], drop=drop)
    svc.enter(side=True)
    await settle()
    side_task = svc._side_task
    assert svc.side_channel is a
    order = []

    def watch(channel, update_status=True):
        order.append((channel, svc.side_channel, side_task.done() or side_task.cancelling()))
        tw._watching = channel

    tw.watch.side_effect = watch
    tw._watching = None  # CHANNEL_SWITCH found nothing: idle entry
    svc.enter()
    await settle()
    assert svc.mode == "main" and svc.active
    assert order and order[0] == (a, None, True)
    tw.gui.clear_drop.assert_called_once()
    await stopped(svc)


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["slots", "paused", "logout", "auth"])
async def test_side_loop_ends_when_side_mode_may_no_longer_run(change):
    a = chan(1, "a")
    svc, tw = side_service([a])
    svc.enter(side=True)
    await settle()
    assert svc.side_channel is a
    if change == "slots":
        tw.settings.watch_slots = 1
    elif change == "paused":
        svc.points_paused = True
    elif change == "logout":
        tw.logged_in_user_id = lambda: None
    else:
        tw._changing_auth = True
    svc.poke()
    await settle()
    assert not svc.active
    assert svc.side_channel is None and svc._side_task is None
    tw.stop_watching.assert_not_called()


@pytest.mark.asyncio
async def test_side_mode_never_hands_the_drop_channel_over():
    a, drop = chan(1, "a"), chan(9, "d", drops=True)
    svc, tw = side_service([a, drop], drop=drop)
    tw.can_watch.side_effect = lambda c: c is drop
    svc.enter(side=True)
    await settle()
    assert svc.active and svc.side_channel is a
    tw.change_state.assert_not_called()
    await stopped(svc)


@pytest.mark.asyncio
async def test_visit_clock_pauses_across_mode_switches(monkeypatch):
    # 60 s of a visit in main mode, a switch to side mode that takes 600 s to start, 40 s more in
    # side mode, a switch back that takes 600 s: the visit has run 100 s
    now = [1000.0]
    monkeypatch.setattr(idle_mod, "time", SimpleNamespace(monotonic=lambda: now[0]))
    a, b, drop = chan(1, "a"), chan(2, "b"), chan(9, "d", drops=True)
    svc, tw = side_service([a, b])
    svc.enter()
    await settle()
    assert (svc.visit.channel_id, svc.visit.started) == (1, 1000.0)
    assert tw._watching is a
    now[0] = 1060.0
    tw._watching = drop  # drops took over the client's watch
    svc.enter(side=True)
    now[0] = 1660.0
    await settle()
    assert svc.mode == "side" and svc.side_channel is a
    assert svc.visit.channel_id == 1 and now[0] - svc.visit.started == 60.0
    now[0] = 1700.0
    tw._watching = None
    svc.enter()
    now[0] = 2300.0
    await settle()
    assert svc.mode == "main" and tw._watching is a
    assert svc.visit.channel_id == 1 and now[0] - svc.visit.started == 100.0
    assert 10 not in svc.visited
    await stopped(svc)


def side_client(*, mode="side", active=True, allowed=True):
    client = client_stub(flag=False)
    client._state = State.CHANNEL_SWITCH
    client.idle_watch.mode = mode
    client.idle_watch.active = active
    client.idle_watch.side_allowed.return_value = allowed
    return client


@pytest.mark.parametrize("state", [State.INVENTORY_FETCH, State.GAMES_UPDATE, State.CHANNELS_FETCH, State.CHANNEL_SWITCH])
def test_side_mode_keeps_running_across_drop_states(state):
    client = side_client()
    client.change_state(state)
    client.idle_watch.stop.assert_not_called()
    client.stop_watching.assert_not_called()
    client.idle_watch.enter.assert_called_once_with(side=True)


def test_leaving_main_mode_starts_side_mode_after_the_stop():
    client = side_client(mode="main")
    calls = []
    client.idle_watch.stop.side_effect = lambda: calls.append("stop")
    client.idle_watch.enter.side_effect = lambda **kw: calls.append(("enter", kw))
    client.change_state(State.CHANNEL_SWITCH)
    assert calls == ["stop", ("enter", {"side": True})]


@pytest.mark.parametrize("state", [State.CHANNEL_SWITCH, State.IDLE, State.EXIT])
def test_side_mode_stops_when_it_may_not_run_or_on_exit(state):
    # not allowed: watch_slots 1, points paused, outside mining hours, logged out or an auth change
    client = side_client(allowed=state is State.EXIT)
    client.change_state(state)
    client.idle_watch.enter.assert_not_called()
    client.idle_watch.stop.assert_called_once()
    client.stop_watching.assert_not_called()


def test_entering_idle_with_idle_watching_leaves_side_mode_to_the_idle_step():
    client = side_client()
    client.change_state(State.IDLE, idle_watch=True)
    client.idle_watch.enter.assert_not_called()
    client.idle_watch.stop.assert_not_called()
    client._idle_watch_step()
    client.idle_watch.enter.assert_called_once_with()


@pytest.mark.parametrize("allowed", [True, False])
def test_a_settings_save_starts_or_stops_side_mode_while_drops_are_mined(allowed):
    client = side_client(active=not allowed, allowed=allowed)
    client.idle_channels_changed()
    assert client.idle_watch.enter.called is allowed
    assert client.idle_watch.stop.called is (not allowed)
    client._state_change.set.assert_not_called()


def test_a_drop_channel_going_online_is_watched_directly_in_side_mode():
    tw = MagicMock()
    tw.settings.idle_channels = ["c1"]
    tw.idle_watch.active = True
    tw.idle_watch.mode = "side"
    tw.watching_channel.get_with_default.return_value = SimpleNamespace(login="x")
    tw.can_watch.return_value = True
    tw.should_switch.return_value = True
    handler = MessageHandlerService(tw)
    drop = SimpleNamespace(login="d", name="D", display=MagicMock())
    handler.on_channel_update(drop, None, SimpleNamespace(drops_enabled=True))
    tw.watch.assert_called_once_with(drop)
    tw.change_state.assert_not_called()
    # a listed channel coming online still pokes the side loop
    tw.can_watch.return_value = False
    listed = SimpleNamespace(login="c1", name="C1", display=MagicMock())
    handler.on_channel_update(listed, None, SimpleNamespace(drops_enabled=False))
    tw.idle_watch.poke.assert_called_once()


# --- points actions on the side channel ---


@pytest.mark.asyncio
async def test_raids_moments_predictions_and_chat_act_on_the_side_channel_only_through_may_act():
    a, enabled, drop = chan(1, "a"), chan(3, "e", drops=True), chan(9, "d", drops=True)
    svc, tw = side_service([a, enabled, drop], drop=drop)
    tw.channels = {c.id: c for c in (a, enabled, drop)}
    for key in ("follow_raids", "claim_moments", "predictions_enabled", "join_chat"):
        setattr(tw.settings, key, True)
    svc._chat = MagicMock()
    svc.enter(side=True)
    await settle()
    assert svc.side_channel is a
    svc._chat.follow.assert_called_with(a)
    assert svc.idle_watched(a.id) is a
    assert svc.idle_watched(drop.id) is None and svc.idle_watched(enabled.id) is None
    for setting in ("follow_raids", "claim_moments", "predictions_enabled", "join_chat", "claim_channel_points"):
        tw.settings.claim_channel_points = True
        assert svc.may_act(a, setting), setting
        assert not svc.may_act(enabled, setting) and not svc.may_act(drop, setting), setting
    # the raid topic is the side channel's only
    raid_topics = {str(t) for t in svc.events._wanted() if "raid" in str(t)}
    assert raid_topics == {"raid.1"}
    tw.gql_request.reset_mock()
    tw.gql_request.return_value = {"data": {"joinRaid": {"error": None}}}
    raid = {"type": "raid_update_v2", "raid": {"id": "r1", "target_login": "t"}}
    await svc.events.process_raid(drop.id, raid)
    await svc.events.process_raid(enabled.id, {**raid, "raid": {"id": "r2", "target_login": "t"}})
    assert tw.gql_request.await_count == 0
    await svc.events.process_raid(a.id, {**raid, "raid": {"id": "r3", "target_login": "t"}})
    assert [c.args[0]["operationName"] for c in tw.gql_request.await_args_list] == ["JoinRaid"]
    tw.gql_request.reset_mock()
    tw.gql_request.return_value = {"data": {"claimCommunityMoment": {"error": None}}}
    await svc.events.process_moment(str(enabled.id), {"type": "active", "data": {"moment_id": "m1"}})
    assert tw.gql_request.await_count == 0
    await svc.events.process_moment(str(a.id), {"type": "active", "data": {"moment_id": "m2"}})
    assert [c.args[0]["operationName"] for c in tw.gql_request.await_args_list] == ["CommunityMomentCallout_Claim"]
    await stopped(svc)


@pytest.mark.asyncio
async def test_points_tab_shows_the_side_slot_row():
    from src.services.points_history import PointsHistory
    from src.web.managers.points import PointsManager

    a, b = chan(1, "a"), chan(2, "b")
    twitch = SimpleNamespace(
        channels={1: a, 2: b}, settings=SimpleNamespace(idle_channels=["a", "b"]),
        idle_watch=SimpleNamespace(balances={}, points_paused=False, active=True, history=PointsHistory(),
                                   side_channel=b),
    )
    points = PointsManager(AsyncMock(), twitch, MagicMock())
    assert [row["side"] for row in points.get_state()["channels"]] == [False, True]
    twitch.idle_watch.side_channel = None
    assert [row["side"] for row in points.get_state()["channels"]] == [False, False]


def test_the_status_line_and_drop_panel_stay_with_drop_mining():
    svc, tw = side_service([chan(1, "a")])
    svc.mode = "side"
    svc._show_status(chan(1, "a"))
    tw.gui.status.update.assert_not_called()


def test_side_allowed_needs_two_slots():
    svc, tw = side_service([chan(1, "a")])
    assert svc.side_allowed()
    tw.settings.watch_slots = 1
    assert not svc.side_allowed()
    assert svc.allowed()  # main mode is unaffected


def test_twitch_has_the_side_step():
    assert callable(Twitch._side_watch_step)
