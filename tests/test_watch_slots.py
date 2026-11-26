"""Two channels at a time (watch_slots 2): idle watching in side mode next to drop mining."""

import asyncio
import json
import logging
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

import src.services.idle_watch as idle_mod
from src.config import (
    BASE_TOPICS,
    MAX_CHANNELS,
    MAX_WEBSOCKETS,
    TOPICS_PER_CHANNEL,
    WS_TOPICS_LIMIT,
    State,
)
from src.core.client import Twitch
from src.exceptions import MinerException
from src.models.channel import Channel
from src.services.message_handlers import MessageHandlerService
from src.services.points_events import PointsEvents
from src.utils.async_helpers import AwaitableValue
from src.websocket.pool import WebsocketPool
from tests.test_idle_points import POINTS_OPTIONS
from tests.test_idle_watch import client_stub, fake, loop_service, run_service
from tests.test_playlist_watch import _make_channel, _playlist, _Transport
from tests.test_session_lifecycle import miner
from tests.test_watched_channel_recovery import OFFLINE, _channel, _reloading_miner


def chan(cid, login, *, drops=False):
    c = fake(cid, cid * 10, login=login)
    c.name, c.drops_enabled = login.upper(), drops
    c._stream = object()
    c.send_watch = AsyncMock(return_value=True)
    c._watch_current = MagicMock(return_value=True)
    c.check_online = MagicMock()
    # the side mode drops check: a drops channel lists a campaign
    c.fetch_drop_campaigns = AsyncMock(return_value=[{"id": "camp"}] if drops else [])
    return c


def side_service(live, *, drop=None, mining=True):
    """A loop_service with watch_slots 2 while drop mining watches drop (None: nothing yet)."""
    svc, tw = loop_service(live, mining=mining, idle=tuple(c.login for c in live))
    tw.settings.watch_slots = 2
    tw._changing_auth = False
    tw._state = State.CHANNEL_SWITCH
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
    await svc._read_side_drops([a])
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
    await svc._read_side_drops([a])
    svc._side_start(a)
    await settle()
    a.check_online.assert_called()
    assert all(c.kwargs == {"side": True} for c in a._watch_current.call_args_list)
    svc._side_stop()


@pytest.mark.asyncio
async def test_switching_the_side_channel_stops_the_old_watcher_first():
    a, b = chan(1, "a"), chan(2, "b")
    svc, tw = side_service([a, b])
    await svc._read_side_drops([a, b])
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


@pytest.mark.parametrize("state", [State.INVENTORY_FETCH, State.CHANNEL_SWITCH])
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


# --- side candidates need a broadcast without any drops campaign (real Channels through track()) ---


def drops_answers(table):
    """GQL fake: GetStreamInfo for listed channels 1..n, AvailableDrops per channel from table (a list is
    used up call by call, "error" raises), an empty reply for anything else."""
    async def gql(ops):
        if isinstance(ops, list):
            return [{"data": {"user": {
                "id": str(cid), "login": f"c{cid}", "displayName": f"C{cid}",
                "stream": {"id": str(cid * 100), "viewersCount": 5},
                "broadcastSettings": {"game": {"id": "509658", "name": "Just Chatting"}, "title": "t"},
            }}} for cid in (int(op["variables"]["channel"][1:]) for op in ops)]
        if "channelID" not in ops["variables"]:  # not AvailableDrops
            return {"data": {}}
        answer = table[int(ops["variables"]["channelID"])]
        if isinstance(answer, list) and answer and answer[0] in ("error", None, "empty"):
            answer = answer.pop(0) if len(answer) > 1 else answer[0]
            answer = [] if answer == "empty" else answer
        if answer == "error":
            raise MinerException("secret body")
        return {"data": {"channel": {"viewerDropCampaigns": answer}}}
    return gql


def real_side_service(table, monkeypatch):
    monkeypatch.setattr(Channel, "send_watch", AsyncMock(return_value=True))
    svc, tw = run_service(idle=[f"c{cid}" for cid in table], gql=AsyncMock(side_effect=drops_answers(table)))
    tw.settings.watch_slots, tw._changing_auth, tw._state = 2, False, State.CHANNEL_SWITCH
    tw._campaigns = {}
    tw._watching = fake(9, 90, login="drop")
    return svc, tw


@pytest.mark.asyncio
@pytest.mark.parametrize("campaigns, side", [
    ([], True), (None, True),
    ([{"id": "not-earnable"}], False),  # drops_enabled stays False, but the campaign may take progress
    ([{"id": "a"}, {"id": "b"}], False),
])
async def test_a_side_candidate_needs_a_broadcast_without_any_drops_campaign(campaigns, side, monkeypatch):
    svc, tw = real_side_service({1: campaigns}, monkeypatch)
    svc.enter(side=True)
    await settle()
    channel = tw.channels[1]
    assert not channel.drops_enabled
    assert svc.side_candidate(channel) is side
    assert (svc.side_channel is channel) is side
    await stopped(svc)


def drops_reads(tw):
    return [c for c in tw.gql_request.await_args_list
            if isinstance(c.args[0], dict) and "channelID" in c.args[0]["variables"]]


def shifted_clock(monkeypatch):
    """idle_watch's clock, moved on by clock[0] seconds (the event loop keeps the real one)."""
    clock = [0.0]
    monkeypatch.setattr(idle_mod, "time", SimpleNamespace(
        time=time.time, monotonic=lambda: time.monotonic() + clock[0]))
    return clock


@pytest.mark.asyncio
async def test_a_failed_side_drops_read_waits_before_a_retry_and_warns_once_per_streak(monkeypatch, caplog):
    # track()'s own read fails, then the side reads fail twice, then answer; one tick per poke
    monkeypatch.setattr(idle_mod, "IDLE_TICK_SECONDS", 10)
    clock = shifted_clock(monkeypatch)
    svc, tw = real_side_service({1: ["error", "error", "error", "empty"]}, monkeypatch)
    with caplog.at_level(logging.WARNING, logger="TwitchDrops"):
        svc.enter(side=True)
        await settle()
        assert svc.side_channel is None and len(drops_reads(tw)) == 2
        for _ in range(3):  # inside the wait: no read
            svc.poke()
            await settle()
        assert len(drops_reads(tw)) == 2
        clock[0] += idle_mod.SIDE_DROPS_RETRY_SECONDS
        svc.poke()
        await settle()
        assert svc.side_channel is None and len(drops_reads(tw)) == 3
        clock[0] += idle_mod.SIDE_DROPS_RETRY_SECONDS
        svc.poke()
        await settle()
    assert svc.side_channel is tw.channels[1]
    warnings = [r.getMessage() for r in caplog.records if "second slot channels" in r.getMessage()]
    assert warnings == ["Drops check of second slot channels failed (MinerException), retrying"]
    assert "secret body" not in caplog.text
    assert len(drops_reads(tw)) == 4
    svc.poke()
    await settle()
    assert len(drops_reads(tw)) == 4  # answered: no read on later ticks
    await stopped(svc)


@pytest.mark.asyncio
async def test_a_side_drops_answer_is_read_again_after_the_refresh_time(monkeypatch):
    monkeypatch.setattr(idle_mod, "IDLE_TICK_SECONDS", 10)
    clock = shifted_clock(monkeypatch)
    table = {1: []}
    svc, tw = real_side_service(table, monkeypatch)
    svc.enter(side=True)
    await settle()
    assert svc.side_channel is tw.channels[1] and len(drops_reads(tw)) == 1  # track()'s read serves side mode
    clock[0] += idle_mod.SIDE_DROPS_REFRESH_SECONDS - 1
    svc.poke()
    await settle()
    assert len(drops_reads(tw)) == 1
    table[1] = [{"id": "new-campaign"}]  # a campaign appears mid-broadcast
    clock[0] += 1
    svc.poke()
    await settle()
    assert len(drops_reads(tw)) == 2
    assert not svc.side_candidate(tw.channels[1]) and svc.side_channel is None
    await stopped(svc)


@pytest.mark.asyncio
async def test_a_new_listed_channel_is_read_once_before_it_can_be_a_side_pick(monkeypatch):
    svc, tw = real_side_service({1: [], 2: []}, monkeypatch)
    svc.enter(side=True)
    await settle()
    assert sorted(int(c.args[0]["variables"]["channelID"]) for c in drops_reads(tw)) == [1, 2]
    assert svc.side_channel is not None


def test_side_drops_answers_are_bounded():
    svc, _ = side_service([])
    for broadcast in range(1000, 1000 + idle_mod.SIDE_DROPS_KEPT + 5):
        svc._side_drops[broadcast] = (True, 0.0)
    a = chan(1, "a")
    asyncio.run(svc._read_side_drops([a]))
    assert len(svc._side_drops) == idle_mod.SIDE_DROPS_KEPT and a.broadcast_id in svc._side_drops


# --- a channel reload with two slots: side mode pauses, and drop channels leave room for the listed ones ---


@pytest.mark.parametrize("state", sorted(idle_mod.SIDE_PAUSE_STATES, key=str))
def test_side_mode_may_not_run_while_drop_mining_reloads_its_channels(state):
    svc, tw = side_service([chan(1, "a")])
    assert svc.side_allowed()
    tw._state = state
    assert not svc.side_allowed()


@pytest.mark.parametrize("slots, listed, room", [(1, 20, MAX_CHANNELS), (2, 0, MAX_CHANNELS - 1), (2, 20, 157)])
def test_drop_channel_room_leaves_the_listed_channels_and_event_topics_room(slots, listed, room):
    svc, tw = side_service([])
    tw.settings.watch_slots, tw.settings.idle_channels = slots, [f"l{i}" for i in range(listed)]
    assert svc.drop_channel_room() == room
    if slots == 2:
        # stream topics of drop and listed channels plus every event topic fit the pool
        used = BASE_TOPICS + TOPICS_PER_CHANNEL * (room + listed) + PointsEvents.most_topics(listed)
        assert used <= MAX_WEBSOCKETS * WS_TOPICS_LIMIT


LISTED = 20


def listed_user(login):
    cid = 500 + int(login[1:])
    return {"data": {"user": {
        "id": str(cid), "login": login, "displayName": login.upper(),
        "stream": {"id": str(cid * 10), "viewersCount": 5},
        "broadcastSettings": {"game": {"id": "509658", "name": "Just Chatting"}, "title": "t"},
    }}}


@pytest.mark.asyncio
async def test_a_channel_reload_with_two_slots_keeps_the_topic_limit_and_the_side_watch(tmp_path, monkeypatch, caplog):
    """198 directory channels, 20 listed channels with every event topic, the real WebsocketPool."""
    monkeypatch.setattr(Channel, "send_watch", AsyncMock(return_value=True))
    client, _watched = _reloading_miner(tmp_path, monkeypatch, discovered=[], stream_status=[])
    logins = [f"l{i}" for i in range(LISTED)]
    for key, value in {**POINTS_OPTIONS, "predictions_enabled": True, "claim_moments": True,
                       "follow_raids": True, "idle_channels": logins, "share_watch_streaks": False,
                       "watch_slots": 2}.items():
        setattr(client.settings, key, value)
    client.logged_in_user_id = lambda: 42
    client.websocket = WebsocketPool(client)
    client.websocket.start = AsyncMock()  # no connections; the pool still counts every topic
    client._channel_service.filter_banned_channels = AsyncMock(
        side_effect=lambda channels, retained_channels=(): list(channels),
    )
    client.get_live_streams = AsyncMock(side_effect=lambda game, **kw: [
        _channel(client, 1000 + i) for i in range(MAX_CHANNELS)
    ])
    sides_during_reload = []
    fetch = client.get_live_streams.side_effect

    def directory(game, **kw):
        sides_during_reload.append(client.idle_watch.active)
        return fetch(game, **kw)

    client.get_live_streams.side_effect = directory

    async def gql(ops):
        if isinstance(ops, list):
            # GetStreamInfo lookups of listed logins; batched points reads get an empty reply
            return [listed_user(op["variables"]["channel"]) if "channel" in op["variables"] else {"data": {}}
                    for op in ops]
        variables = ops.get("variables") or {}
        if "channelID" in variables and "query" not in ops:
            return {"data": {"channel": {"viewerDropCampaigns": []}}}
        if variables.get("channel") == "channel1":
            return OFFLINE
        return {"data": {}}

    client.gql_request = AsyncMock(side_effect=gql)
    selected = asyncio.Event()

    def watch(channel, *, update_status=True):
        client._watch_service.watch(channel, update_status=update_status)
        selected.set()

    client.watch = watch

    def topics():
        return sum(len(ws.topics) for ws in client.websocket.websockets)

    def event_topics():
        return [t for ws in client.websocket.websockets for t in ws.topics if "predictions" in t or "moments" in t]

    # side mode next to drop mining, before the hourly reload
    client._state = State.CHANNEL_SWITCH
    client.idle_watch.enter(side=True)
    for _ in range(100):
        await asyncio.sleep(0.01)
        if client.idle_watch.side_channel is not None and event_topics():
            break
    assert client.idle_watch.side_channel is not None
    assert len(event_topics()) == 1 + 2 * LISTED
    # the reload: side mode pauses with its event topics gone
    client.change_state(State.GAMES_UPDATE)
    assert not client.idle_watch.active and client.idle_watch.side_channel is None and not event_topics()
    client.change_state(State.CHANNELS_CLEANUP)
    run = asyncio.create_task(client._run())
    with caplog.at_level(logging.WARNING, logger="TwitchDrops"):
        await asyncio.wait_for(selected.wait(), 2)
        for _ in range(100):
            await asyncio.sleep(0.01)
            if client.idle_watch.side_channel is not None and len(event_topics()) == 1 + 2 * LISTED:
                break
    try:
        assert sides_during_reload == [False]
        assert not run.done()  # no MinerException ended the client
        drops = [c for c in client.channels.values() if c.login not in logins]
        assert len(drops) == client.idle_watch.drop_channel_room() == 157
        assert {c.login for c in client.channels.values()} >= set(logins)
        side = client.idle_watch.side_channel
        assert side is not None and side.login in logins
        assert topics() <= MAX_WEBSOCKETS * WS_TOPICS_LIMIT
        assert "topic" not in caplog.text.lower() and "skipped" not in caplog.text
    finally:
        client.close()
        await asyncio.gather(run, return_exceptions=True)
        client.idle_watch.stop()  # close() ended side mode already
        if client._watching_task is not None:
            client._watching_task.cancel()


# --- side watch identity: by channel id, a re-tracked object continues the same side watch ---


@pytest.mark.asyncio
async def test_a_re_tracked_object_of_the_side_channel_continues_the_same_side_watch(caplog):
    a, drop = chan(1, "a"), chan(9, "d", drops=True)
    svc, tw = side_service([a], drop=drop)
    svc.refresh_points = AsyncMock()
    with caplog.at_level(logging.INFO, logger="TwitchDrops"):
        svc.enter(side=True)
        await settle()
        task = svc._side_task
        assert svc.side_channel is a and svc.refresh_points.await_count == 1
        svc._claimed.add("c1")
        svc._claim_failed, svc._claim_retries = True, idle_mod.CLAIM_RETRIES
        # a cleanup dropped the object and track() made a new one for the same login and broadcast
        a2 = chan(1, "a")
        svc.live_channels = lambda: [a2]
        svc.poke()
        await settle()
    assert svc.side_channel is a2 and svc.watched is a2
    assert svc._side_task is task and not task.done()
    assert a2.send_watch.await_count >= 1
    assert svc.refresh_points.await_count == 1
    assert svc._claimed == {"c1"} and svc._claim_failed and svc._claim_retries == idle_mod.CLAIM_RETRIES
    assert caplog.text.count("in the second slot") == 2  # the loop start and one side start
    assert "switched to" not in caplog.text
    await stopped(svc)


@pytest.mark.asyncio
async def test_a_reload_pause_resumes_the_same_side_watch_on_the_re_tracked_object(caplog):
    a, drop = chan(1, "a"), chan(9, "d", drops=True)
    svc, tw = side_service([a], drop=drop)
    svc.refresh_points = AsyncMock()
    with caplog.at_level(logging.INFO, logger="TwitchDrops"):
        svc.enter(side=True)
        await settle()
        svc._claimed.add("c1")
        tw._state = State.CHANNELS_CLEANUP
        svc.stop()
        await settle()
        assert svc.side_channel is None
        a2 = chan(1, "a")
        svc.live_channels = lambda: [a2]
        svc.track = AsyncMock(return_value=[a2])
        tw._state = State.CHANNEL_SWITCH
        svc.enter(side=True)
        await settle()
    assert svc.side_channel is a2 and svc.watched is a2 and svc._claimed == {"c1"}
    assert svc.refresh_points.await_count == 1
    assert caplog.text.count("Idle watching A in the second slot") == 1
    assert "switched to" not in caplog.text
    await stopped(svc)


@pytest.mark.asyncio
async def test_a_stop_outside_a_reload_starts_the_side_watch_afresh(caplog):
    a = chan(1, "a")
    svc, tw = side_service([a], drop=chan(9, "d", drops=True))
    svc.refresh_points = AsyncMock()
    with caplog.at_level(logging.INFO, logger="TwitchDrops"):
        svc.enter(side=True)
        await settle()
        svc._claimed.add("c1")
        svc.stop()  # state CHANNEL_SWITCH: not a reload pause
        svc.enter(side=True)
        await settle()
    assert svc.side_channel is a and svc._claimed == set()
    assert svc.refresh_points.await_count == 2
    assert caplog.text.count("Idle watching A in the second slot") == 2
    await stopped(svc)


def test_side_watch_checks_compare_the_drop_and_side_channels_by_id():
    channel = _make_channel(_Transport())
    twin = SimpleNamespace(id=channel.id)
    other = SimpleNamespace(id=channel.id + 1)
    stream = channel._stream
    tw = channel._twitch
    tw.idle_watch = SimpleNamespace(side_channel=twin)
    tw.watching_channel = SimpleNamespace(get_with_default=lambda d: other)
    assert channel._watch_current(stream, side=True)  # the side channel is a twin object of this one
    tw.watching_channel = SimpleNamespace(get_with_default=lambda d: SimpleNamespace(id=channel.id))
    assert not channel._watch_current(stream, side=True)  # a twin of this channel is the drop channel
    tw.idle_watch = SimpleNamespace(side_channel=other)
    tw.watching_channel = SimpleNamespace(get_with_default=lambda d: None)
    assert not channel._watch_current(stream, side=True)


# --- an auth change drains the side watcher too ---


@pytest.mark.asyncio
async def test_an_auth_change_drains_the_side_watcher_before_it_completes(tmp_path, monkeypatch):
    client = miner(tmp_path, monkeypatch)
    client.websocket.stop = AsyncMock()
    svc = client.idle_watch
    a = chan(1, "a")
    watching, cleaned = asyncio.Event(), asyncio.Event()

    async def send_watch(*, side=False):
        watching.set()
        try:
            await asyncio.Event().wait()
        finally:
            await asyncio.sleep(0)
            cleaned.set()

    a.send_watch = AsyncMock(side_effect=send_watch)
    client.is_mining_time = lambda: True
    svc.side_candidate = lambda channel: True
    svc._side_start(a)
    await watching.wait()
    side = svc._side_task
    async with client.authentication_change():
        assert side.done() and cleaned.is_set()
        calls = a.send_watch.await_count
        await asyncio.sleep(0.02)
        assert a.send_watch.await_count == calls
    assert svc._side_task is None and svc.side_channel is None


# --- the points history is saved when idle watching stops and at shutdown ---


@pytest.mark.asyncio
@pytest.mark.parametrize("side", [False, True])
async def test_stopping_idle_watching_saves_the_points_history(tmp_path, side):
    a = chan(1, "a")
    svc, tw = side_service([a], drop=chan(9, "d", drops=True) if side else None)
    svc.history = idle_mod.PointsHistory(tmp_path / "points_history.json")
    svc.enter(side=side)
    await settle()
    assert svc.history.account == 1
    svc.record_balance(a, 500)  # within the minute after the tick's save
    await stopped(svc)
    saved = json.loads((tmp_path / "points_history.json").read_text(encoding="utf-8"))
    assert saved["channels"]["1"]["samples"][-1][1] == 500


@pytest.mark.asyncio
async def test_shutdown_saves_the_points_history(tmp_path, monkeypatch):
    client = miner(tmp_path, monkeypatch)
    client.settings.idle_channels = ["a"]
    history = client.idle_watch.history
    history.use_account(7)
    history.sample(1, "a", 42)
    history._tried_at = history._monotonic()  # a tick just tried: the minute has not passed
    await client.shutdown()
    saved = json.loads((tmp_path / "points_history.json").read_text(encoding="utf-8"))
    assert saved["user_id"] == 7 and saved["channels"]["1"]["samples"][-1][1] == 42
