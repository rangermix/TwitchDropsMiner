"""Idle watch: pick logic for streak visits and points, lookup and tracking of listed channels."""

import asyncio
import inspect
import json
import logging
import pathlib
import re
import time
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.config import MAX_CHANNELS, State, WebsocketTopic
from src.core.client import Twitch
from src.services import idle_watch as idle_mod
from src.services.channel_service import ChannelService
from src.services.idle_watch import IDLE_VISIT_SECONDS, IdleWatchService
from src.services.message_handlers import MessageHandlerService


def fake(cid, broadcast, *, online=True, banned=False, login=None):
    return SimpleNamespace(
        id=cid, login=login or f"c{cid}", online=online, banned=banned, ban_expires_at=None,
        broadcast_id=broadcast,
    )


def service(channels=None, idle=(), gql=None, visits_path=None):
    handlers = SimpleNamespace(process_stream_state=object(), process_stream_update=object())
    twitch = SimpleNamespace(
        channels=dict(channels or {}),
        settings=SimpleNamespace(idle_channels=list(idle), share_watch_streaks=False),
        gql_request=gql or AsyncMock(),
        gui=SimpleNamespace(channels=MagicMock(), login=MagicMock()),
        websocket=MagicMock(),
        _message_handler_service=handlers,
        _channel_service=SimpleNamespace(filter_banned_channels=AsyncMock(side_effect=list)),
    )
    twitch._add_channel_topics = lambda channels: Twitch._add_channel_topics(twitch, channels)
    return IdleWatchService(twitch, visits_path), twitch


def test_first_due_channel_gets_a_visit():
    svc, _ = service()
    a, b = fake(1, 10), fake(2, 20)
    assert svc.pick([a, b], 0) is a
    assert svc.visit.channel_id == 1


def test_visit_holds_until_420_seconds():
    svc, _ = service()
    a, b = fake(1, 10), fake(2, 20)
    svc.pick([a, b], 0)
    assert svc.pick([a, b], IDLE_VISIT_SECONDS - 1) is a
    assert svc.pick([a, b], IDLE_VISIT_SECONDS) is b
    assert 10 in svc.visited


def test_done_visit_ends_early():
    svc, _ = service()
    a, b = fake(1, 10), fake(2, 20)
    svc.pick([a, b], 0)
    svc.visit.done = True
    assert svc.pick([a, b], 5) is b
    assert 10 in svc.visited


def test_visit_ends_when_channel_offline_or_new_broadcast():
    svc, _ = service()
    a, b = fake(1, 10), fake(2, 20)
    svc.pick([a, b], 0)
    assert svc.pick([b], 5) is b
    svc2, _ = service()
    svc2.pick([a, b], 0)
    a2 = fake(1, 11)
    assert svc2.pick([a2, b], 5) is a2  # new broadcast is due again
    assert svc2.visit.broadcast_id == 11


def test_points_watch_on_highest_live_after_visits():
    svc, _ = service()
    a, b = fake(1, 10), fake(2, 20)
    svc.visited = {10: 1, 20: 2}
    assert svc.pick([a, b], 0) is a
    assert svc.pick([], 1) is None


def test_removed_channel_is_dropped():
    svc, _ = service()
    a, b = fake(1, 10), fake(2, 20)
    svc.pick([a, b], 0)
    assert svc.pick([b], 1) is b


def test_visited_is_pruned_only_for_a_new_broadcast_or_an_unlisted_channel():
    # channel 1 is live with broadcast 11 (new), 2 is tracked but no longer listed,
    # 3 is listed but offline this tick, 4 is untracked (after a cleanup)
    tracked = {1: fake(1, 11, login="a"), 2: fake(2, 20, login="b"), 3: fake(3, 30, login="c")}
    svc, _ = service(tracked, idle=["a", "c", "d"])
    svc.visited = {10: 1, 20: 2, 30: 3, 40: 4}
    svc.pick([tracked[1]], 0)
    assert svc.visited == {30: 3, 40: 4}


def user(uid, login, live=True):
    return {"data": {"user": {
        "id": str(uid), "login": login,
        "stream": {"id": str(uid * 100), "viewersCount": 5, "type": "live",
                   "game": None, "broadcaster": {"id": str(uid), "login": login}} if live else None,
    }}}


@pytest.mark.asyncio
async def test_lookup_skips_unknown_and_errors(caplog):
    gql = AsyncMock(return_value=[
        {"data": {"user": None}}, {"errors": [{"message": "secret body"}]},
        {"data": None, "errors": [{"message": "secret body"}]}, user(3, "ok", live=False),
    ])
    svc, twitch = service(idle=["gone", "bad", "null", "ok"], gql=gql)
    with caplog.at_level(logging.WARNING, logger="TwitchDrops"):
        tracked = await svc.track()
    assert [c.login for c in tracked] == ["ok"]
    assert set(twitch.channels) == {3}
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 3
    assert all("secret body" not in r.getMessage() for r in warnings)


@pytest.mark.asyncio
async def test_enter_reuses_tracked_channel():
    existing = fake(3, 1, login="Other")
    gql = AsyncMock(return_value=[user(3, "ok", live=False)])
    svc, twitch = service({3: existing}, idle=["ok"], gql=gql)
    tracked = await svc.track()
    assert tracked == [existing]
    assert twitch.channels[3] is existing


@pytest.mark.asyncio
async def test_track_subscribes_stream_topics_and_respects_cap():
    gql = AsyncMock(return_value=[user(1, "a", live=False), user(2, "b", live=False)])
    filler = {1000 + i: fake(1000 + i, 1) for i in range(MAX_CHANNELS - 1)}
    svc, twitch = service(filler, idle=["a", "b"], gql=gql)
    tracked = await svc.track()
    assert len(twitch.channels) == MAX_CHANNELS
    assert [c.login for c in tracked] == ["a"]
    topics = twitch.websocket.add_topics.call_args.args[0]
    assert {str(t) for t in topics} == {
        WebsocketTopic.as_str("Channel", "StreamState", 1),
        WebsocketTopic.as_str("Channel", "StreamUpdate", 1),
    }
    twitch.gui.channels.display.assert_called_once()


def test_live_channels_follow_list_order_and_skip_banned_offline():
    chans = {1: fake(1, 1, login="a"), 2: fake(2, 2, login="b", banned=True),
             3: fake(3, 3, login="c", online=False), 4: fake(4, 4, login="d")}
    svc, _ = service(chans, idle=["d", "b", "c", "a", "zz"])
    assert [c.login for c in svc.live_channels()] == ["d", "a"]


@pytest.mark.asyncio
async def test_banned_new_channel_is_not_added_and_gets_no_topics():
    gql = AsyncMock(return_value=[user(1, "a", live=False), user(2, "b", live=False)])
    svc, twitch = service(idle=["a", "b"], gql=gql)
    twitch._channel_service.filter_banned_channels = AsyncMock(
        side_effect=lambda chans: [c for c in chans if c.id != 2],
    )
    tracked = await svc.track()
    assert set(twitch.channels) == {1}
    assert [c.login for c in tracked] == ["a"]
    topics = twitch.websocket.add_topics.call_args.args[0]
    assert {str(t) for t in topics} == {
        WebsocketTopic.as_str("Channel", "StreamState", 1),
        WebsocketTopic.as_str("Channel", "StreamUpdate", 1),
    }


# --- loop and client wiring ---


def loop_service(live, *, mining=True, idle=("c1",)):
    svc, tw = service(idle=idle)
    tw.is_mining_time = MagicMock(side_effect=lambda: mining() if callable(mining) else mining)
    tw.logged_in_user_id = lambda: 1
    tw.watching_channel = SimpleNamespace(
        get_with_default=lambda d: tw._watching or d, set=lambda c: setattr(tw, "_watching", c),
    )
    tw._watching = None
    tw.watch = MagicMock(side_effect=lambda c, update_status=True: setattr(tw, "_watching", c))
    tw.stop_watching = MagicMock()
    tw.can_watch = MagicMock(return_value=False)
    tw.change_state = MagicMock()
    tw.gui.status = MagicMock()
    tw.gui.clear_drop = MagicMock()
    svc.track = AsyncMock(return_value=live)
    svc.live_channels = lambda: live
    return svc, tw


@pytest.mark.asyncio
async def test_idle_stops_outside_mining_time():
    svc, tw = loop_service([fake(1, 10)], mining=False)
    svc.enter()
    await asyncio.wait_for(svc._task, 1)
    tw.stop_watching.assert_called_once()
    tw.watch.assert_not_called()
    assert not svc.active


@pytest.mark.asyncio
async def test_loop_watches_pick_with_idle_status(caplog, monkeypatch):
    monkeypatch.setattr(idle_mod, "IDLE_TICK_SECONDS", 0.01)
    monkeypatch.setattr(idle_mod, "IDLE_VISIT_SECONDS", 0.05)
    a, b = fake(1, 10, login="c1"), fake(2, 20, login="c2")
    a.name, b.name = "A", "B"
    svc, tw = loop_service([a, b], idle=("c1", "c2"))
    with caplog.at_level(logging.INFO, logger="TwitchDrops"):
        svc.enter()
        svc.enter()  # second call must not start a second task
        await asyncio.sleep(0.03)
        assert tw.watch.call_args_list[0].args == (a,)
        assert tw.watch.call_args_list[0].kwargs == {"update_status": False}
        assert svc.active
        await asyncio.sleep(0.2)
        svc.stop()
    assert [c.args[0] for c in tw.watch.call_args_list][:2] == [a, b]
    # no re-watch while the pick is unchanged
    assert len(tw.watch.call_args_list) <= 3
    assert any("Idle watching A" in c.args[0] for c in tw.gui.status.update.call_args_list)
    text = caplog.text
    assert text.count("Idle watching started") == 1
    assert "Idle visit started" in text and "Idle visit ended" in text
    assert "Idle watching switched" in text
    assert not svc.active


@pytest.mark.asyncio
async def test_track_failure_is_logged_and_retried(caplog, monkeypatch):
    monkeypatch.setattr(idle_mod, "IDLE_TICK_SECONDS", 0.01)
    a = fake(1, 10)
    a.name = "A"
    svc, tw = loop_service([a])
    svc.track = AsyncMock(side_effect=[TimeoutError("secret body"), [a]])
    with caplog.at_level(logging.INFO, logger="TwitchDrops"):
        svc.enter()
        await asyncio.sleep(0.1)
        svc.stop()
    assert svc.track.await_count >= 2
    tw.watch.assert_called()
    assert "secret body" not in caplog.text
    assert "Idle channel lookup failed" in caplog.text


def test_stop_keeps_visit_state():
    svc, _ = service()
    svc.pick([fake(1, 10)], 0)
    svc.stop()
    assert svc.visit is not None


def client_stub(*, flag, mining=True, logged_in=True, idle=("c1",)):
    client = object.__new__(Twitch)
    client._state = State.IDLE
    client._state_change = MagicMock()
    client._idle_watch_allowed = flag
    client.idle_watch = MagicMock()
    client.idle_watch.allowed.return_value = mining and logged_in and bool(idle)
    client.stop_watching = MagicMock()
    client.watching_channel = SimpleNamespace(get_with_default=lambda d: d)
    client.can_watch = MagicMock(return_value=False)
    return client


@pytest.mark.parametrize("flag,ok", [(True, True), (False, False)])
def test_idle_branch_enters_idle_watch_only_when_allowed(flag, ok):
    client = client_stub(flag=flag)
    client._idle_watch_step()
    assert client.idle_watch.enter.called is ok
    assert client.stop_watching.called is (not ok)


@pytest.mark.parametrize("kw", [{"mining": False}, {"logged_in": False}, {"idle": ()}])
def test_idle_branch_stops_when_a_condition_fails(kw):
    client = client_stub(flag=True, **kw)
    client._idle_watch_step()
    client.idle_watch.enter.assert_not_called()
    client.stop_watching.assert_called_once()


def test_leaving_idle_stops_the_loop():
    client = client_stub(flag=True)
    client.change_state(State.INVENTORY_FETCH)
    client.idle_watch.stop.assert_called_once()
    assert client._idle_watch_allowed is False
    client.change_state(State.IDLE, idle_watch=True)
    assert client._idle_watch_allowed is True
    client.change_state(State.IDLE)
    assert client._idle_watch_allowed is False


def test_nothing_to_mine_entries_allow_idle_watch():
    src = inspect.getsource(Twitch._run)
    calls = re.findall(r"self\.change_state\(State\.IDLE(?:, idle_watch=True)?\)", src)
    flagged = [c for c in calls if "idle_watch" in c]
    assert len(flagged) == 2
    # no-games cleanup branch and the no-channel switch branch carry the flag
    assert re.search(r'collapse_key="status.no_campaign",\s+\)\s+self\.change_state\(State\.IDLE, idle_watch=True\)', src)
    assert re.search(r'_t\["status"\]\["no_channel"\]\)\s+self\.change_state\(State\.IDLE, idle_watch=True\)', src.replace("_.t", "_t"))
    assert len(calls) == 3  # the mining-hours close stays unflagged; the schedule loop is another method
    assert "self._state = State.IDLE" in inspect.getsource(Twitch)


def test_idle_channel_online_event_pokes():
    tw = MagicMock()
    tw.settings.idle_channels = ["c1"]
    tw.idle_watch.active = True
    tw.watching_channel.get_with_default.return_value = None
    tw.can_watch.return_value = False
    handler = MessageHandlerService(tw)
    listed = SimpleNamespace(login="c1", name="C1", display=MagicMock())
    other = SimpleNamespace(login="zz", name="ZZ", display=MagicMock())
    handler.on_channel_update(listed, None, object())
    tw.idle_watch.poke.assert_called_once()
    handler.on_channel_update(other, None, object())
    tw.idle_watch.poke.assert_called_once()
    tw.idle_watch.active = False
    handler.on_channel_update(listed, None, object())
    tw.idle_watch.poke.assert_called_once()


def test_idle_entry_without_flag_stops_a_running_loop():
    client = client_stub(flag=True)
    client.change_state(State.IDLE)
    client.idle_watch.stop.assert_called_once()
    client.idle_watch.stop.reset_mock()
    client._idle_watch_allowed = False
    client._idle_watch_step()
    client.idle_watch.stop.assert_called_once()


@pytest.mark.asyncio
async def test_stale_channel_object_with_same_id_is_replaced(monkeypatch):
    monkeypatch.setattr(idle_mod, "IDLE_TICK_SECONDS", 0.01)
    old, new = fake(1, 10), fake(1, 10)
    old.name = new.name = "A"
    svc, tw = loop_service([new])
    tw._watching = old
    svc.enter()
    await asyncio.sleep(0.03)
    svc.stop()
    assert tw.watch.call_args_list[0].args == (new,)
    tw.gui.status.update.assert_called()
    assert tw.watch.call_count == 1


@pytest.mark.asyncio
async def test_unknown_login_is_looked_up_once_until_the_list_changes(caplog):
    answer = {"data": {"user": None}}
    gql = AsyncMock(return_value=[answer])
    svc, tw = service(idle=["typo"], gql=gql)
    with caplog.at_level(logging.WARNING, logger="TwitchDrops"):
        await svc.track()
        await svc.track()
    assert gql.await_count == 1
    assert caplog.text.count("skipped") == 1
    tw.settings.idle_channels = ["typo", "other"]
    gql.return_value = [answer, answer]
    await svc.track()
    assert gql.await_count == 2


@pytest.mark.asyncio
async def test_whole_request_failure_is_not_a_skip():
    gql = AsyncMock(side_effect=[TimeoutError(), [{"data": {"user": None}}]])
    svc, _ = service(idle=["a"], gql=gql)
    with pytest.raises(TimeoutError):
        await svc.track()
    await svc.track()
    assert gql.await_count == 2


def handler_twitch(*, active, can_watch=True, should_switch=True, watching=None):
    tw = MagicMock()
    tw.settings.idle_channels = ["c1"]
    tw.idle_watch.active = active
    tw.watching_channel.get_with_default.return_value = watching
    tw.can_watch.return_value = can_watch
    tw.should_switch.return_value = should_switch
    return tw, MessageHandlerService(tw)


def stream():
    return SimpleNamespace(drops_enabled=True)


@pytest.mark.parametrize("before", [None, "live"])
def test_drop_channel_event_while_idle_hands_over_to_channel_switch(before):
    # online, or a title/tag update that makes a channel mineable
    tw, handler = handler_twitch(active=True, watching=SimpleNamespace(login="c1"))
    drop = SimpleNamespace(login="d", name="D", display=MagicMock())
    handler.on_channel_update(drop, stream() if before else None, stream())
    tw.watch.assert_not_called()
    tw.change_state.assert_called_once_with(State.CHANNEL_SWITCH)


@pytest.mark.parametrize("before", [None, "live"])
def test_drop_channel_event_without_idle_watches_directly(before):
    tw, handler = handler_twitch(active=False, watching=SimpleNamespace(login="x"))
    drop = SimpleNamespace(login="d", name="D", display=MagicMock())
    handler.on_channel_update(drop, stream() if before else None, stream())
    tw.watch.assert_called_once_with(drop)
    tw.change_state.assert_not_called()


@pytest.mark.parametrize("after", [None, "live"])
def test_idle_watched_channel_offline_or_update_pokes_instead_of_switch(after):
    # offline, and a title/tag change of the idle channel that drops cannot use
    idle = SimpleNamespace(login="c1", name="C1", display=MagicMock())
    tw, handler = handler_twitch(active=True, can_watch=False, watching=idle)
    handler.on_channel_update(idle, stream(), stream() if after else None)
    tw.change_state.assert_not_called()
    tw.idle_watch.poke.assert_called_once()
    tw, handler = handler_twitch(active=False, can_watch=False, watching=idle)
    handler.on_channel_update(idle, stream(), stream() if after else None)
    tw.change_state.assert_called_once_with(State.CHANNEL_SWITCH)


def test_idle_watched_channel_becoming_mineable_pokes_the_loop():
    idle = SimpleNamespace(login="c1", name="C1", display=MagicMock())
    tw, handler = handler_twitch(active=True, can_watch=True, watching=idle)
    handler.on_channel_update(idle, stream(), stream())
    tw.idle_watch.poke.assert_called_once()
    tw.watch.assert_not_called()


@pytest.mark.asyncio
async def test_loop_hands_a_mineable_watched_channel_to_drops(monkeypatch, caplog):
    # the loop must not take the watch back from a drop channel
    monkeypatch.setattr(idle_mod, "IDLE_TICK_SECONDS", 0.01)
    a, drop = fake(1, 10, login="c1"), fake(9, 90, login="d")
    a.name, drop.name = "A", "D"
    svc, tw = loop_service([a])
    tw._watching = drop
    tw.can_watch = MagicMock(side_effect=lambda c: c is drop)
    with caplog.at_level(logging.INFO, logger="TwitchDrops"):
        svc.enter()
        await asyncio.sleep(0.05)
    tw.watch.assert_not_called()
    tw.stop_watching.assert_not_called()
    tw.change_state.assert_called_once_with(State.CHANNEL_SWITCH)
    assert not svc.active
    assert not [r for r in caplog.records if r.levelno >= logging.ERROR]


def test_base_topics_match_the_user_topics_subscribed_at_login():
    # every User topic subscribed in _run must be counted in BASE_TOPICS
    from src.config import (
        BASE_TOPICS,
        MAX_TOPICS,
        MAX_WEBSOCKETS,
        TOPICS_PER_CHANNEL,
        WS_TOPICS_LIMIT,
    )

    src = inspect.getsource(Twitch._run)
    user_topics = re.findall(r'WebsocketTopic\(\s*"User"', src)
    assert len(user_topics) == BASE_TOPICS
    assert MAX_CHANNELS * TOPICS_PER_CHANNEL + BASE_TOPICS <= MAX_WEBSOCKETS * WS_TOPICS_LIMIT
    assert MAX_TOPICS + BASE_TOPICS == MAX_WEBSOCKETS * WS_TOPICS_LIMIT


def test_idle_list_change_reruns_idle_step_without_games_update():
    # the client side of an idle_channels edit
    client = client_stub(flag=True)
    client._games_update_pending = False
    client.idle_watch.active = True
    client.idle_channels_changed()
    client._state_change.set.assert_called_once()
    client.idle_watch.poke.assert_called_once()
    assert client._games_update_pending is False
    client = client_stub(flag=False)
    client._state = State.CHANNEL_SWITCH
    client.idle_channels_changed()
    client._state_change.set.assert_not_called()
    client.idle_watch.poke.assert_not_called()


@pytest.mark.asyncio
async def test_unexpected_tick_error_is_logged_and_the_loop_continues(monkeypatch, caplog):
    monkeypatch.setattr(idle_mod, "IDLE_TICK_SECONDS", 0.01)
    a = fake(1, 10)
    a.name = "A"
    svc, tw = loop_service([a])
    svc.track = AsyncMock(side_effect=[KeyError("k"), RuntimeError("r")] + [[a]] * 50)
    with caplog.at_level(logging.INFO, logger="TwitchDrops"):
        svc.enter()
        await asyncio.sleep(0.1)
        assert svc.active
        task = svc._task
        svc.stop()
        await asyncio.gather(task, return_exceptions=True)
    assert task.cancelled()
    assert svc.track.await_count >= 3
    tw.watch.assert_called()
    assert len([r for r in caplog.records if r.levelno == logging.ERROR]) == 2


@pytest.mark.parametrize("pending", ["inventory", "games"])
def test_pending_state_activation_stops_idle_watch(pending):
    client = client_stub(flag=True)
    client.settings = SimpleNamespace(mining_hours={"mode": "always"})
    client._paused = False
    client._inventory_refresh_pending = pending == "inventory"
    client._games_update_pending = pending == "games"
    client._inventory_loaded = True
    if pending == "inventory":
        client._activate_pending_inventory_refresh()
    else:
        client._activate_pending_games_update()
    assert client._state is (State.INVENTORY_FETCH if pending == "inventory" else State.GAMES_UPDATE)
    client.idle_watch.stop.assert_called_once()
    assert client._idle_watch_allowed is False


@pytest.mark.asyncio
async def test_poke_during_a_tick_is_not_lost(monkeypatch):
    # a poke that arrives during track() must run another tick at once
    monkeypatch.setattr(idle_mod, "IDLE_TICK_SECONDS", 5)
    a = fake(1, 10)
    a.name = "A"
    svc, _ = loop_service([a])
    calls = []

    async def track():
        calls.append(1)
        if len(calls) == 1:
            svc.poke()
        return [a]

    svc.track = track
    svc.enter()
    await asyncio.sleep(0.1)
    svc.stop()
    assert len(calls) >= 2


@pytest.mark.asyncio
async def test_idle_start_clears_drop_panel_and_rerun_keeps_idle_status(monkeypatch):
    monkeypatch.setattr(idle_mod, "IDLE_TICK_SECONDS", 5)
    a = fake(1, 10)
    a.name = "A"
    svc, tw = loop_service([a])
    svc.enter()
    await asyncio.sleep(0.03)
    tw.gui.clear_drop.assert_called_once()
    tw.gui.status.update.reset_mock()
    svc.show_status()
    assert "Idle watching A" in tw.gui.status.update.call_args.args[0]
    tw._watching = fake(2, 20)
    tw.gui.status.update.reset_mock()
    svc.show_status()
    tw.gui.status.update.assert_not_called()
    svc.stop()


def test_idle_branch_rerun_reshows_idle_status():
    client = client_stub(flag=True)
    client._idle_watch_step()
    client.idle_watch.show_status.assert_called_once()


@pytest.mark.asyncio
async def test_authentication_change_drains_idle_task_and_keeps_account_state(tmp_path, monkeypatch):
    from tests.test_session_lifecycle import miner

    client = miner(tmp_path, monkeypatch)
    client.websocket.stop = AsyncMock()
    svc = client.idle_watch
    entered, cleaned = asyncio.Event(), asyncio.Event()

    async def run():
        entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            await asyncio.sleep(0)
            cleaned.set()

    svc._task = asyncio.create_task(run())
    task = svc._task
    await entered.wait()
    svc.balances[1] = 5
    svc.visited[10] = 1
    svc.visit = idle_mod.Visit(1, 10, 0.0)
    svc._skipped.add("x")
    svc._claimed.add("c")
    svc._seen_list = ["x"]
    svc._share_tries[("m1", 9)] = idle_mod.STREAK_SHARE_ATTEMPTS
    (tmp_path / "idle_visits.json").write_text('{"user_id": 1, "visits": {"10": 1}, "shares": []}', encoding="utf-8")
    async with client.authentication_change():
        assert cleaned.is_set() and task.done()
    assert not svc.active
    assert (svc.balances, svc.visit, svc._skipped, svc._claimed, svc._seen_list) == ({}, None, set(), set(), None)
    # visits and shares stay with their account: the next idle entry drops them for another one
    assert svc.visited == {10: 1}
    assert svc._share_tries == {("m1", 9): idle_mod.STREAK_SHARE_ATTEMPTS}
    assert (tmp_path / "idle_visits.json").exists()


@pytest.mark.asyncio
async def test_short_batch_reply_skips_unanswered_logins(caplog):
    gql = AsyncMock(return_value=[user(1, "a", live=False)])
    svc, _ = service(idle=["a", "b", "c"], gql=gql)
    with caplog.at_level(logging.WARNING, logger="TwitchDrops"):
        await svc.track()
        await svc.track()
    assert gql.await_count == 1
    assert caplog.text.count("skipped") == 2


@pytest.mark.asyncio
async def test_banned_logins_warn_once(caplog):
    gql = AsyncMock(return_value=[user(1, "a", live=False), user(2, "b", live=False)])
    tracked_banned = fake(3, 3, login="c", banned=True)
    svc, twitch = service({3: tracked_banned}, idle=["a", "b", "c"], gql=gql)

    twitch._channel_service.filter_banned_channels = AsyncMock(
        side_effect=lambda chans: [c for c in chans if c.id != 2],
    )
    with caplog.at_level(logging.WARNING, logger="TwitchDrops"):
        await svc.track()
        await svc.track()
    banned = [r for r in caplog.records if "banned" in r.getMessage()]
    assert sorted(r.getMessage() for r in banned) == [
        "Idle channel b skipped: banned", "Idle channel c skipped: banned",
    ]


def run_service(channels=None, idle=(), gql=None, visits_path=None):
    """A real track()/pick() loop on the fake client, one tick per wait (IDLE_TICK_SECONDS stays 30)."""
    svc, tw = service(channels, idle=idle, gql=gql, visits_path=visits_path)
    tw.is_mining_time = MagicMock(return_value=True)
    tw.logged_in_user_id = lambda: 1
    tw._watching = None
    tw.watching_channel = SimpleNamespace(get_with_default=lambda d: tw._watching or d)
    tw.watch = MagicMock(side_effect=lambda c, update_status=True: setattr(tw, "_watching", c))
    tw.stop_watching = MagicMock()
    tw.can_watch = MagicMock(return_value=False)
    tw.change_state = MagicMock()
    tw.gui.status = MagicMock()
    tw.gui.clear_drop = MagicMock()
    return svc, tw


async def idle_entry(svc, ticks=1):
    svc.enter()
    await asyncio.sleep(0.02)
    for _ in range(ticks - 1):
        svc.poke()
        await asyncio.sleep(0.02)
    task = svc.stop()
    await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
async def test_over_cap_login_warns_once_by_count_and_is_retried_on_the_next_idle_entry(caplog):
    # CHANNEL_SWITCH "no channel" keeps the drop channels tracked, so the cap can be full
    filler = {1000 + i: fake(1000 + i, 1) for i in range(MAX_CHANNELS)}
    gql = AsyncMock(return_value=[user(1, "secretlogin", live=False)])
    svc, tw = run_service(filler, idle=["secretlogin"], gql=gql)
    with caplog.at_level(logging.WARNING, logger="TwitchDrops"):
        await idle_entry(svc, ticks=2)
    assert gql.await_count == 1  # not re-requested on every tick of the run
    assert 1 not in tw.channels
    warnings = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
    assert warnings == ["1 idle channels skipped: tracked channel limit reached"]
    tw.channels.pop(1000)
    await idle_entry(svc)
    assert gql.await_count == 2
    assert 1 in tw.channels


@pytest.mark.asyncio
async def test_lifted_ban_is_retried_on_the_next_idle_entry():
    gql = AsyncMock(return_value=[user(2, "b", live=False)])
    svc, tw = run_service(idle=["b"], gql=gql)

    async def ban_then_lift(chans):
        return [] if tw._channel_service.filter_banned_channels.await_count == 1 else list(chans)

    tw._channel_service.filter_banned_channels = AsyncMock(side_effect=ban_then_lift)
    await idle_entry(svc, ticks=2)
    assert gql.await_count == 1
    assert 2 not in tw.channels
    await idle_entry(svc)
    assert gql.await_count == 2
    assert 2 in tw.channels


@pytest.mark.asyncio
async def test_visit_clock_pauses_while_the_loop_is_stopped(monkeypatch):
    # 60 s of a visit, then an hour of drops, must leave 360 s of the visit
    now = [1000.0]
    monkeypatch.setattr(idle_mod, "time", SimpleNamespace(monotonic=lambda: now[0]))
    a, b = fake(1, 10, login="c1"), fake(2, 20, login="c2")
    a.name, b.name = "A", "B"
    svc, tw = loop_service([a, b], idle=("c1", "c2"))
    svc.enter()
    await asyncio.sleep(0.02)
    assert (svc.visit.channel_id, svc.visit.started) == (1, 1000.0)
    now[0] = 1060.0
    task = svc.stop()
    await asyncio.gather(task, return_exceptions=True)
    now[0] = 1060.0 + 1800
    svc.enter()
    svc.stop()  # cancelled before its first step: must not move the stamp
    now[0] = 1060.0 + 3600
    svc.enter()
    await asyncio.sleep(0.02)
    svc.stop()
    assert svc.visit is not None and svc.visit.channel_id == 1
    assert now[0] - svc.visit.started == 60.0
    assert tw._watching is a


def test_visits_survive_a_tick_without_live_channels():
    # a failed track() after a full cleanup leaves no live channel for a tick
    svc, _ = service()
    a, b = fake(1, 10), fake(2, 20)
    svc.visited = {10: 1, 20: 2}
    assert svc.pick([], 0) is None
    assert svc.pick([a, b], 1) is a
    assert svc.visit is None  # points watch, not a fresh round of visits


def test_a_visit_cut_short_by_a_missing_channel_is_not_recorded():
    # a failed lookup after the hourly reload leaves the visited channel out of live for a tick
    svc, _ = service()
    a, b = fake(1, 10), fake(2, 20)
    svc.pick([a, b], 0)
    assert svc.pick([b], 5) is b  # a missing: its visit stops, b's starts
    assert 10 not in svc.visited
    svc.visit.done = True
    assert svc.pick([a, b], 6) is a  # same broadcast of a: it gets its full visit
    assert (svc.visit.channel_id, svc.visit.started) == (1, 6)
    assert svc.visited == {20: 2}
    solo, _ = service()
    assert solo.pick([a], 0) is a
    assert solo.pick([], 60) is None
    assert solo.visit is None and solo.visited == {}
    assert solo.pick([a], 90) is a
    assert (solo.visit.channel_id, solo.visit.started) == (1, 90)
    assert solo.pick([a], 90 + IDLE_VISIT_SECONDS) is a
    assert solo.visit is None and solo.visited == {10: 1}  # finished after its full time


def answers_by_login(table):
    async def gql(ops):
        logins = [op["variables"]["channel"] for op in ops]
        for login in logins:
            if isinstance(table[login], Exception):
                raise table[login]
        return [table[login] for login in logins]
    return AsyncMock(side_effect=gql)


@pytest.mark.asyncio
async def test_hard_gql_error_in_a_batch_skips_only_the_failing_login(caplog):
    # gql_request raises GQLException for the whole batch on an unrecognised error
    from src.exceptions import GQLException

    gql = answers_by_login({
        "good": user(1, "good", live=False), "bad": GQLException("secret body"),
        "other": user(3, "other", live=False),
    })
    svc, tw = service(idle=["good", "bad", "other"], gql=gql)
    with caplog.at_level(logging.WARNING, logger="TwitchDrops"):
        tracked = await svc.track()
        await svc.track()
    assert [c.login for c in tracked] == ["good", "other"]
    assert set(tw.channels) == {1, 3}
    assert gql.await_count == 4  # the batch, then one request per login; nothing next tick
    warnings = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
    assert warnings == ["Idle channel bad skipped: lookup failed or unknown"]
    assert "secret body" not in caplog.text


@pytest.mark.asyncio
async def test_single_login_gql_error_still_propagates():
    # only a batch is narrowed; a lone login failing is a whole-request failure, retried next tick
    from src.exceptions import GQLException

    svc, _ = service(idle=["bad"], gql=answers_by_login({"bad": GQLException("x")}))
    with pytest.raises(GQLException):
        await svc.track()
    assert svc._skipped == set()


@pytest.mark.asyncio
@pytest.mark.parametrize("bad_user", [
    {"id": None, "login": "bad"}, {"login": "bad"}, {"id": "12a", "login": "bad"},
    {"id": "", "login": "bad"}, {"id": True, "login": "bad"}, {"id": "²", "login": "bad"},
    {"id": [1], "login": "bad"}, "bad",
])
async def test_user_without_a_usable_id_is_skipped(bad_user, caplog):
    gql = AsyncMock(return_value=[{"data": {"user": bad_user}}, user(2, "ok", live=False)])
    svc, tw = service(idle=["bad", "ok"], gql=gql)
    with caplog.at_level(logging.WARNING, logger="TwitchDrops"):
        tracked = await svc.track()
        await svc.track()
    assert [c.login for c in tracked] == ["ok"]
    assert set(tw.channels) == {2}
    assert gql.await_count == 1
    assert caplog.text.count("Idle channel bad skipped") == 1


@pytest.mark.asyncio
async def test_numeric_user_id_is_accepted():
    gql = AsyncMock(return_value=[{"data": {"user": {"id": 5, "login": "n", "stream": None}}}])
    svc, _ = service(idle=["n"], gql=gql)
    assert [c.id for c in await svc.track()] == [5]


@pytest.mark.asyncio
async def test_visit_start_is_logged_once_across_loop_restarts(caplog):
    a = fake(1, 10, login="c1")
    a.name = "A"
    svc, _ = loop_service([a])
    with caplog.at_level(logging.INFO, logger="TwitchDrops"):
        await idle_entry(svc)
        await idle_entry(svc)
    assert caplog.text.count("Idle visit started") == 1
    assert caplog.text.count("Idle watching started") == 2


@pytest.mark.asyncio
async def test_rewatch_after_nothing_live_is_not_logged_as_a_switch(caplog, monkeypatch):
    monkeypatch.setattr(idle_mod, "IDLE_TICK_SECONDS", 5)
    a = fake(1, 10, login="c1")
    a.name = "A"
    live = [a]
    svc, tw = loop_service(live)
    tw.stop_watching.side_effect = lambda: setattr(tw, "_watching", None)
    with caplog.at_level(logging.INFO, logger="TwitchDrops"):
        svc.enter()
        await asyncio.sleep(0.02)
        live.clear()
        svc.poke()
        await asyncio.sleep(0.02)
        assert svc.watched is None
        live.append(a)
        svc.poke()
        await asyncio.sleep(0.02)
        svc.stop()
    assert tw.watch.call_count == 2
    assert "Idle watching switched" not in caplog.text


@pytest.mark.asyncio
async def test_exit_request_ends_the_loop_without_an_error_log(caplog):
    from src.exceptions import ExitRequest

    svc, _ = loop_service([])
    svc.track = AsyncMock(side_effect=ExitRequest())
    with caplog.at_level(logging.INFO, logger="TwitchDrops"):
        svc.enter()
        await asyncio.wait_for(svc._task, 1)
    assert svc.track.await_count == 1
    assert not [r for r in caplog.records if r.levelno >= logging.WARNING]


@pytest.mark.asyncio
async def test_points_request_reraises_exit_request():
    from src.exceptions import ExitRequest

    svc, _ = service(gql=AsyncMock(side_effect=ExitRequest()))
    with pytest.raises(ExitRequest):
        await svc._gql(idle_mod.GQL_OPERATIONS["ChannelPointsContext"].with_variables({"channelLogin": "a"}))


def test_idle_watch_logs_keep_their_module_name_in_diagnostics():
    from src.diagnostics import Diagnostics

    assert idle_mod.__name__.rsplit(".", 1)[-1] in Diagnostics.LOG_MODULES


def stopping_client(*, can_watch, active=True):
    client = client_stub(flag=True)
    idle = SimpleNamespace(id=1, login="c1")
    client.watching_channel = SimpleNamespace(get_with_default=lambda d: idle)
    client.can_watch = MagicMock(return_value=can_watch)
    client.idle_watch.active = active
    return client


@pytest.mark.parametrize("state", [State.INVENTORY_FETCH, State.CHANNEL_SWITCH, State.IDLE])
def test_leaving_idle_watching_releases_the_idle_channel(state):
    # otherwise refresh_watched_channel carries it into the CHANNELS_FETCH candidates
    client = stopping_client(can_watch=False)
    client.change_state(state)
    client.idle_watch.stop.assert_called_once()
    client.stop_watching.assert_called_once()


def test_leaving_idle_watching_keeps_a_channel_drops_can_use():
    # the loop's own handover leaves a drop channel watched for CHANNEL_SWITCH
    client = stopping_client(can_watch=True)
    client.change_state(State.CHANNEL_SWITCH)
    client.stop_watching.assert_not_called()


def test_state_change_without_a_running_idle_loop_keeps_the_watch():
    client = stopping_client(can_watch=False, active=False)
    client.change_state(State.CHANNEL_SWITCH)
    client.stop_watching.assert_not_called()
    client = stopping_client(can_watch=False)
    client.change_state(State.IDLE, idle_watch=True)
    client.stop_watching.assert_not_called()


# --- follow-up fixes after the live test ---


def banning_service(monkeypatch, status):
    """track() through the real ChannelService ban filter; status is the ban the account has on login b."""
    now = [time.time()]
    monkeypatch.setattr("src.models.channel.time", lambda: now[0])
    monkeypatch.setattr(idle_mod, "time", SimpleNamespace(time=lambda: now[0], monotonic=time.monotonic))
    ban = [status]

    async def gql(ops):
        if "channel" in ops[0].get("variables", {}):
            return [user(2, "b", live=False)]
        return [{"data": {"user": {"id": "2", "self": {"banStatus": ban[0]}}}}]

    svc, tw = service(idle=["b"], gql=AsyncMock(side_effect=gql))
    tw.request_games_update = MagicMock()
    tw._channel_service = ChannelService(tw)
    return svc, tw, now, ban


@pytest.mark.asyncio
async def test_timed_ban_is_retried_once_it_expires(monkeypatch):
    svc, tw, now, ban = banning_service(monkeypatch, None)
    ban[0] = {"isPermanent": False, "expiresAt": datetime.fromtimestamp(now[0] + 600, timezone.utc).isoformat()}
    try:
        await svc.track()
        now[0] += 590
        await svc.track()
        assert 2 not in tw.channels
        assert tw.gql_request.await_count == 2  # one lookup and one ban check; nothing before expiry
        now[0] += 11
        ban[0] = None
        tracked = await svc.track()  # same list, same idle run
        assert [c.login for c in tracked] == ["b"]
        assert 2 in tw.channels
    finally:
        tw._channel_service.clear_bans()


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [{"isPermanent": True}, {"isPermanent": False}])
async def test_ban_without_expiry_stays_skipped(monkeypatch, status):
    # a timed ban without a usable expiry waits for a list change or a new idle entry, like a permanent one
    svc, tw, now, _ = banning_service(monkeypatch, status)
    try:
        await svc.track()
        now[0] += 30 * 86400
        await svc.track()
        assert 2 not in tw.channels
        assert tw.gql_request.await_count == 2
    finally:
        tw._channel_service.clear_bans()


def visit_channels():
    a, b = fake(1, 10, login="a"), fake(2, 20, login="b")
    a.name, b.name = "A", "B"
    return a, b


@pytest.mark.asyncio
async def test_visits_survive_a_restart_and_are_written_on_change_only(tmp_path, monkeypatch):
    path = tmp_path / "idle_visits.json"
    saves = []
    real_save = idle_mod.json_save
    monkeypatch.setattr(idle_mod, "json_save", lambda *a, **k: (saves.append(a[1]), real_save(*a, **k)))
    a, b = visit_channels()
    svc, _ = run_service({1: a, 2: b}, idle=["a", "b"], visits_path=path)
    await idle_entry(svc)  # visit of a starts: nothing visited yet, the file is bound to the account
    assert json.loads(path.read_text(encoding="utf-8")) == {"user_id": 1, "visits": {}, "shares": []}
    svc.visit.done = True
    await idle_entry(svc, ticks=3)  # a visited, b's visit runs for three ticks
    assert json.loads(path.read_text(encoding="utf-8")) == {"user_id": 1, "visits": {"10": 1}, "shares": []}
    assert len(saves) == 2
    restarted, _ = service({1: a, 2: b}, idle=["a", "b"], visits_path=path)
    assert restarted.visited == {10: 1}
    assert restarted.pick([a, b], 0) is b  # a's broadcast is not visited again after the restart


@pytest.mark.parametrize("contents", [
    "not json", '{"user_id": 1, "visits": {"10": 1}, "sha', "[1, 2]", "null", bytes([0xFF, 0xFE]),
    '{"visits": []}', '{"visits": {}, "user_id": "1"}', '{"visits": {}, "user_id": true}',
    '{"visits": {}, "user_id": 0}', '{"visits": {}, "shares": {}}', "permission", "directory",
    # numbers or nesting Python's parser refuses
    "huge key", "deep nesting",
    # without "visits" but with the newer fields it is not the old flat format
    '{"user_id": 1, "shares": [["m1", 9]]}', '{"Visits": {"10": 1}, "shares": [["m1", 9]]}',
], ids=repr)
def test_unreadable_visits_file_is_kept_and_visits_stay_in_memory(tmp_path, monkeypatch, caplog, contents):
    path = tmp_path / "idle_visits.json"
    contents = {"huge key": '{"user_id": 1, "visits": {"' + "1" * 5000 + '": 1}, "shares": []}',
                "deep nesting": "[" * 100000}.get(contents, contents)
    if contents == "directory":
        path.mkdir()
    elif contents == "permission":
        path.write_text('{"user_id": 1, "visits": {"10": 1}, "shares": []}', encoding="utf-8")

        def denied(self, *args, **kwargs):
            raise PermissionError("secret path")
        monkeypatch.setattr(pathlib.Path, "read_text", denied)
    elif isinstance(contents, bytes):
        path.write_bytes(contents)
    else:
        path.write_text(contents, encoding="utf-8")
    before = None if path.is_dir() else path.read_bytes()
    with caplog.at_level(logging.WARNING, logger="TwitchDrops"):
        svc, _ = service(visits_path=path)
        assert svc.visited == {}
        svc._use_account(1)
        svc.visited[20] = 2
        assert svc._save() is False  # not written: the file is left for the owner to fix or delete
        assert svc._save() is False
    assert svc.visited == {20: 2}  # in memory, so this run does not revisit the broadcast
    assert (None if path.is_dir() else path.read_bytes()) == before
    assert len(caplog.messages) == 1  # once, with the exception type only and no file contents
    assert caplog.messages[0].startswith("Idle visits file unreadable (")
    assert "fix or delete" in caplog.messages[0]
    assert "secret path" not in caplog.text


@pytest.mark.parametrize(("contents", "visits", "keys", "skipped"), [
    # a bad visit next to a good one
    ('{"user_id": 1, "visits": {"10": 1, "x": 2, "-1": 3, " 12": 4, "13": "5", "14": true}, "shares": [["m1", 9]]}',
     {10: 1}, {("m1", 9)}, 5),
    # good shares, a repeated key and a refused one with its try count are not invalid
    ('{"user_id": 1, "visits": {"10": 1, "x": 2}, "shares": [["m1", 9], ["m7", null], ["m1", 9], ["m5", 9, 1]]}',
     {10: 1}, {("m1", 9), ("m7", None)}, 1),
    # the old flat format with a bad entry
    ('{"10": 1, "x": 2, "11": true}', {10: 1}, set(), 2),
], ids=["visit", "shares", "flat"])
def test_malformed_entries_are_skipped_and_the_rest_kept(tmp_path, caplog, contents, visits, keys, skipped):
    path = tmp_path / "idle_visits.json"
    path.write_text(contents, encoding="utf-8")
    with caplog.at_level(logging.WARNING, logger="TwitchDrops"):
        svc, _ = service(visits_path=path)
    assert svc.visited == visits
    assert {key for key, tries in svc._share_tries.items() if tries >= idle_mod.STREAK_SHARE_ATTEMPTS} == keys
    assert caplog.messages == [f"Idle visits file: {skipped} invalid entries skipped"]
    svc._use_account(1)
    svc.visited[20] = 2
    assert svc._save() is True  # the file is usable: it is rewritten with the valid entries
    saved = json.loads(path.read_text(encoding="utf-8"))
    assert saved["visits"] == {**{str(b): c for b, c in visits.items()}, "20": 2}


def test_missing_visits_file_starts_empty_without_a_warning(tmp_path, caplog):
    with caplog.at_level(logging.WARNING, logger="TwitchDrops"):
        svc, _ = service(visits_path=tmp_path / "idle_visits.json")
    assert svc.visited == {}
    assert caplog.records == []


def test_reset_keeps_the_accounts_visits_shares_and_file(tmp_path):
    path = tmp_path / "idle_visits.json"
    path.write_text('{"user_id": 1, "visits": {"10": 1}, "shares": [["m1", 9], ["m2", null]]}', encoding="utf-8")
    svc, _ = service(visits_path=path)
    assert svc.visited == {10: 1}
    attempts = idle_mod.STREAK_SHARE_ATTEMPTS
    assert svc._share_tries == {("m1", 9): attempts, ("m2", None): attempts}
    svc.reset()
    assert svc.visited == {10: 1} and path.exists()
    assert svc._share_tries == {("m1", 9): attempts, ("m2", None): attempts}


@pytest.mark.asyncio
@pytest.mark.parametrize("account,kept", [(1, True), (2, False)])
async def test_visits_and_shares_are_kept_for_their_account_only(tmp_path, account, kept):
    path = tmp_path / "idle_visits.json"
    path.write_text('{"user_id": 1, "visits": {"10": 1}, "shares": [["m1", 9]]}', encoding="utf-8")
    a, b = visit_channels()
    svc, tw = run_service({1: a, 2: b}, idle=["a", "b"], visits_path=path)
    tw.logged_in_user_id = lambda: account
    await idle_entry(svc)
    assert (10 in svc.visited) is kept
    assert (("m1", 9) in svc._share_tries) is kept
    assert tw._watching is (b if kept else a)  # a's broadcast was visited by account 1
    saved = json.loads(path.read_text(encoding="utf-8")) if path.exists() else None
    if not kept:
        assert saved == {"user_id": 2, "visits": {}, "shares": []}


@pytest.mark.asyncio
async def test_a_file_without_an_account_is_taken_by_the_first_account(tmp_path):
    # files written before the account was stored hold the visits alone
    path = tmp_path / "idle_visits.json"
    path.write_text('{"10": 1}', encoding="utf-8")
    a, b = visit_channels()
    svc, tw = run_service({1: a, 2: b}, idle=["a", "b"], visits_path=path)
    assert svc.visited == {10: 1}
    await idle_entry(svc)
    assert tw._watching is b
    assert json.loads(path.read_text(encoding="utf-8")) == {"user_id": 1, "visits": {"10": 1}, "shares": []}


def test_visited_stays_bounded_for_channels_that_left_the_list_while_untracked(tmp_path):
    # untracked channels keep their visits under the existing rules, so old entries are capped
    path = tmp_path / "idle_visits.json"
    path.write_text(json.dumps({str(b): 5000 + b for b in range(1, 101)}), encoding="utf-8")
    svc, _ = service(visits_path=path)
    a, _b = visit_channels()
    svc.pick([a], 0)
    assert len(svc.visited) == idle_mod.IDLE_VISITS_KEPT
    assert list(svc.visited)[-1] == 100  # the newest entries stay
    svc.visit.done = True
    svc.pick([a], 1)
    assert len(svc.visited) == idle_mod.IDLE_VISITS_KEPT
    assert list(svc.visited)[-1] == 10


@pytest.mark.asyncio
async def test_unwritable_visits_file_warns_and_keeps_idle_watching(tmp_path, monkeypatch, caplog):
    def fail(*args, **kwargs):
        raise PermissionError("secret path")

    monkeypatch.setattr(idle_mod, "json_save", fail)
    a, b = visit_channels()
    svc, tw = run_service({1: a, 2: b}, idle=["a", "b"], visits_path=tmp_path / "idle_visits.json")
    svc.visited = {99: 7}
    with caplog.at_level(logging.WARNING, logger="TwitchDrops"):
        await idle_entry(svc, ticks=3)
    warnings = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
    assert warnings.count("Idle visits could not be saved (PermissionError)") == 1
    assert "secret path" not in caplog.text
    assert tw._watching is a


@pytest.mark.asyncio
async def test_failed_visits_save_is_retried_until_it_succeeds(tmp_path, monkeypatch, caplog):
    path = tmp_path / "idle_visits.json"
    real_save = idle_mod.json_save
    failures = [PermissionError(), PermissionError()]

    def flaky(*args, **kwargs):
        if failures:
            raise failures.pop(0)
        real_save(*args, **kwargs)

    monkeypatch.setattr(idle_mod, "json_save", flaky)
    a, b = visit_channels()
    svc, _ = run_service({1: a, 2: b}, idle=["a", "b"], visits_path=path)
    svc.visited = {99: 7}
    with caplog.at_level(logging.WARNING, logger="TwitchDrops"):
        await idle_entry(svc, ticks=3)
    assert caplog.messages.count("Idle visits could not be saved (PermissionError)") == 1
    assert json.loads(path.read_text(encoding="utf-8"))["visits"] == {"99": 7}


@pytest.mark.asyncio
async def test_loop_ends_when_its_pick_can_be_mined():
    a = fake(1, 10, login="c1")
    a.name = "A"
    svc, tw = loop_service([a])
    tw.can_watch = MagicMock(side_effect=lambda c: c is a)
    svc.enter()
    await asyncio.wait_for(svc._task, 1)
    tw.change_state.assert_called_once_with(State.CHANNEL_SWITCH)
    tw.watch.assert_not_called()
    tw.gql_request.assert_not_called()


@pytest.mark.asyncio
async def test_idle_re_entry_forgets_the_previous_pick_before_the_loop_runs():
    # the last run handed its pick to drops, which kept watching it; the next entry must not treat it as idle watched
    a = fake(1, 10, login="c1")
    a.name = "A"
    svc, tw = loop_service([a])
    tw.settings.claim_channel_points = True
    svc.enter()
    await asyncio.sleep(0.02)
    assert svc.watched is a
    tw.can_watch.return_value = True  # a drop can use a: the running loop hands it over and ends
    svc.poke()
    await asyncio.sleep(0.02)
    assert not svc.active and tw._watching is a and svc.watched is a
    svc.stop()
    tw.can_watch.return_value = False
    tw.gui.status.reset_mock()
    tw.gql_request.reset_mock()
    svc.enter()  # CHANNEL_SWITCH found nothing: idle entry, then show_status() before the loop's first step
    svc.show_status()
    await svc.process_points(1, {"type": "claim-available", "data": {"claim": {"id": "abc", "channel_id": "1"}}})
    tw.gui.status.update.assert_not_called()
    assert not [c for c in tw.gql_request.call_args_list
                if not isinstance(c.args[0], list) and c.args[0]["operationName"] == "ClaimCommunityPoints"]
    task = svc.stop()
    await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
async def test_a_hand_over_to_drops_pauses_the_visit_clock(monkeypatch):
    # 60 s of a visit, a hand-over to drops, idle again 10 minutes later: 360 s of the visit are left
    now = [1000.0]
    monkeypatch.setattr(idle_mod, "time", SimpleNamespace(monotonic=lambda: now[0]))
    a, b = fake(1, 10, login="c1"), fake(2, 20, login="c2")
    a.name, b.name = "A", "B"
    svc, tw = loop_service([a, b], idle=("c1", "c2"))
    svc.enter()
    await asyncio.sleep(0.02)
    assert (svc.visit.channel_id, svc.visit.started) == (1, 1000.0)
    now[0] = 1060.0
    tw.can_watch.side_effect = lambda c: c is a
    svc.poke()
    await asyncio.sleep(0.02)
    assert not svc.active  # handed over; the client's stop() finds the task done
    svc.stop()
    now[0] = 1060.0 + 600
    tw.can_watch.side_effect = None
    tw.can_watch.return_value = False
    svc.enter()
    await asyncio.sleep(0.02)
    task = svc.stop()
    await asyncio.gather(task, return_exceptions=True)
    assert 10 not in svc.visited
    assert svc.visit is not None and svc.visit.channel_id == 1
    assert now[0] - svc.visit.started == 60.0
