"""Channel points runtime of idle watching: the separate pause, claims and reads on every listed channel,
watch priority and watch streak recovery."""

import asyncio
import logging
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import aiohttp
import pytest

import src.services.idle_points as idle_points
import src.services.idle_watch as idle_mod
from src.config import State
from src.core.client import Twitch
from src.services.idle_points import POINTS_READ_SECONDS, WatchPriority
from src.services.idle_watch import (
    IDLE_VISIT_SECONDS,
    RECOVERY_GAP_SECONDS,
    RECOVERY_VISITS,
    IdleWatchService,
    Visit,
)
from tests.test_idle_points import CLAIM_OK, claim_msg, earned, sent
from tests.test_idle_points import setup as points_setup
from tests.test_idle_watch import fake, loop_service, service


def live_chan(cid, login):
    return SimpleNamespace(id=cid, name=login.upper(), login=login, online=True, banned=False, broadcast_id=cid * 10)


def answer(balance=500, claim=None, factors=(), channel_id=None):
    channel = {"self": {"communityPoints": {
        "balance": balance,
        "availableClaim": {"id": claim} if claim else None,
        "activeMultipliers": [{"factor": f} for f in factors],
    }}}
    if channel_id is not None:
        channel["id"] = channel_id
    return {"data": {"community": {"channel": channel}}}


# --- separate pause ---


def test_pause_blocks_allowed_and_notifies_once():
    svc, tw, _ = points_setup()
    tw.logged_in_user_id = lambda: 1
    tw.idle_channels_changed = MagicMock()
    assert svc.allowed()
    svc.set_points_paused(True)
    assert svc.points_paused and not svc.allowed()
    svc.set_points_paused(True)  # unchanged: no second broadcast or IDLE re-run
    tw.gui.points.paused_changed.assert_called_once_with(True)
    tw.idle_channels_changed.assert_called_once()
    svc.set_points_paused(False)
    assert svc.allowed()
    tw.gui.points.paused_changed.assert_called_with(False)
    assert tw.idle_channels_changed.call_count == 2


def pause_client(state):
    """A client with a real idle service, in the given state."""
    client = object.__new__(Twitch)
    client._state = state
    client._state_change = MagicMock()
    client._idle_watch_allowed = state is State.IDLE
    client.settings = SimpleNamespace(idle_channels=["c1"], mining_hours={"mode": "always"}, join_chat=False)
    client.gui = MagicMock()
    client._paused = False
    client.logged_in_user_id = lambda: 1
    client.stop_watching = MagicMock()
    client.watch = MagicMock()
    client.idle_watch = IdleWatchService(client)
    client.idle_watch.enter = MagicMock()
    client.idle_watch.stop = MagicMock()
    return client


def test_pause_while_mining_drops_leaves_mining_alone():
    client = pause_client(State.CHANNEL_SWITCH)
    client.idle_watch.set_points_paused(True)
    client._state_change.set.assert_not_called()
    client.stop_watching.assert_not_called()
    client.watch.assert_not_called()
    # drop mining does not read the points pause
    assert client.is_mining_time()


def test_pause_while_idle_stops_the_loop_and_its_watch_and_resume_starts_it():
    client = pause_client(State.IDLE)
    client.idle_watch.set_points_paused(True)
    client._state_change.set.assert_called_once()
    client._idle_watch_step()  # what the main loop runs on the state change
    client.idle_watch.stop.assert_called_once()
    client.stop_watching.assert_called_once()
    client.idle_watch.enter.assert_not_called()
    client.idle_watch.set_points_paused(False)
    client._idle_watch_step()
    client.idle_watch.enter.assert_called_once()


@pytest.mark.asyncio
async def test_pause_ends_a_running_loop(monkeypatch):
    monkeypatch.setattr(idle_mod, "IDLE_TICK_SECONDS", 5)
    svc, tw = loop_service([fake(1, 10)])
    tw.idle_channels_changed = MagicMock(side_effect=svc.poke)  # the client pokes a running loop
    svc.enter()
    await asyncio.sleep(0.02)
    tw.watch.assert_called_once()
    svc.set_points_paused(True)
    await asyncio.wait_for(svc._task, 1)  # the poke wakes the loop well before the 5 s tick
    assert not svc.active
    tw.stop_watching.assert_called()


@pytest.mark.asyncio
async def test_no_claim_share_or_read_while_points_paused():
    svc, tw, _ = points_setup()
    svc.points_paused = True
    await svc.process_points(1, claim_msg())
    await svc.claim(svc.watched, "direct")
    await svc._reader.read_due([live_chan(7, "b")], 0)
    assert sent(tw) == []
    tw.settings.share_watch_streaks = True
    svc.live_channels = lambda: [svc.watched]
    assert not svc._may_share(svc.watched)
    svc.points_paused = False
    assert svc._may_share(svc.watched)


# --- bonus claims on every listed channel ---


@pytest.mark.asyncio
async def test_claim_refused_for_a_drop_channel_an_unlisted_channel_and_a_stopped_loop():
    svc, tw, _ = points_setup()
    tw.channels[999] = SimpleNamespace(id=999, name="Z", login="z")  # tracked for drops, not listed
    tw.can_watch = MagicMock(side_effect=lambda c: c.id == 7)
    await svc.process_points(1, claim_msg("c1", channel_id="7"))  # listed, but drops can use it
    await svc.process_points(1, claim_msg("c2", channel_id="999"))  # unlisted
    await svc.process_points(1, claim_msg("c3", channel_id="12"))  # not tracked
    await svc.claim(tw.channels[999], "c4")
    assert sent(tw) == []
    tw.can_watch = MagicMock(return_value=False)
    svc._task = MagicMock(done=lambda: True)  # loop stopped
    await svc.process_points(1, claim_msg("c5", channel_id="7"))
    assert sent(tw) == []
    svc._task = MagicMock(done=lambda: False)
    await svc.process_points(1, claim_msg("c6", channel_id="7"))
    assert [op["variables"]["input"]["claimID"] for op in sent(tw)] == ["c6"]


@pytest.mark.asyncio
@pytest.mark.parametrize("case", ["offline", "banned"])
async def test_claim_refused_on_an_offline_or_banned_listed_channel(case):
    # claim() uses the guards of every points action (may_act), so a listed channel must be live
    svc, tw, _ = points_setup()
    setattr(tw.channels[7], "online" if case == "offline" else "banned", case == "banned")
    await svc.process_points(1, claim_msg("c1", channel_id="7"))
    assert sent(tw) == []
    await svc.process_points(1, claim_msg("c2"))  # the live watched channel still claims
    assert [op["variables"]["input"]["claimID"] for op in sent(tw)] == ["c2"]


@pytest.mark.asyncio
async def test_a_failed_claim_on_another_channel_does_not_trigger_the_watched_retry():
    svc, tw, _ = points_setup(gql=AsyncMock(side_effect=TimeoutError()))
    await svc.process_points(1, claim_msg(channel_id="7"))
    assert svc._claim_failed is False
    await svc.process_points(1, claim_msg("w"))
    assert svc._claim_failed is True


@pytest.mark.asyncio
async def test_a_claim_on_another_channel_is_logged_to_the_points_log():
    svc, tw, _ = points_setup()
    await svc.process_points(1, claim_msg(channel_id="7"))
    tw.gui.points.log.assert_called_once_with("bonus_claimed", channel="B")
    # the watched channel's retry state is untouched by another channel's success
    svc._claim_failed, svc._claim_retries = True, 1
    await svc.process_points(1, claim_msg("other", channel_id="7"))
    assert (svc._claim_failed, svc._claim_retries) == (True, 1)


# --- the periodic points read ---


def reader_setup(answers, *, can_watch=()):
    a, b, c = live_chan(123, "a"), live_chan(7, "b"), live_chan(8, "c")
    gql = AsyncMock(side_effect=lambda op: answers if isinstance(op, list) else CLAIM_OK)
    svc, tw, _ = points_setup(watched=a, gql=gql)
    tw.channels = {a.id: a, b.id: b, c.id: c}
    tw.settings.idle_channels = ["a", "b", "c"]
    tw.can_watch = MagicMock(side_effect=lambda ch: ch.login in can_watch)
    return svc, tw, [a, b, c]


def batches(tw):
    return [[op["variables"]["channelLogin"] for op in c.args[0]] for c in tw.gql_request.call_args_list
            if isinstance(c.args[0], list)]


def claims(tw):
    return [c.args[0]["variables"]["input"] for c in tw.gql_request.call_args_list
            if not isinstance(c.args[0], list) and c.args[0]["operationName"] == "ClaimCommunityPoints"]


@pytest.mark.asyncio
async def test_read_batches_the_other_live_listed_channels_every_five_minutes():
    svc, tw, live = reader_setup([answer(300, claim="open", factors=(0.2,)), answer(50, channel_id="8")])
    await svc._reader.read_due(live, 1000)
    assert batches(tw) == [["b", "c"]]  # one batch, without the watched channel
    assert svc.balances == {7: 300, 8: 50}
    assert svc.multipliers == {7: (0.2,), 8: ()}
    assert claims(tw) == [{"claimID": "open", "channelID": "7"}]
    tw.gui.points.refresh.assert_called()
    await svc._reader.read_due(live, 1000 + POINTS_READ_SECONDS - 1)
    assert len(batches(tw)) == 1
    await svc._reader.read_due(live, 1000 + POINTS_READ_SECONDS)
    assert len(batches(tw)) == 2


@pytest.mark.asyncio
async def test_read_skips_drop_channels_and_reads_nothing_without_other_channels():
    svc, tw, live = reader_setup([answer(1)], can_watch=("b",))
    await svc._reader.read_due(live, 0)
    assert batches(tw) == [["c"]]
    svc, tw, live = reader_setup([])
    await svc._reader.read_due(live[:1], 0)  # only the watched channel is live
    assert batches(tw) == []
    await svc._reader.read_due(live, 1)  # not delayed by the empty attempt
    assert batches(tw) == [["b", "c"]]


@pytest.mark.asyncio
async def test_read_needs_a_running_loop():
    svc, tw, live = reader_setup([answer()])
    svc._task = MagicMock(done=lambda: True)
    await svc._reader.read_due(live, 0)
    assert batches(tw) == []


@pytest.mark.asyncio
@pytest.mark.parametrize("answers", [
    [None, answer(70)],  # an entry its own GQL error failed
    [answer(1, channel_id="999"), answer(70)],  # an answer for another channel
    [{"data": {"community": None}}, answer(70)],
    [answer(True), answer(70)],  # no usable balance
])
async def test_a_partial_batch_applies_only_the_usable_answers(answers):
    svc, tw, live = reader_setup(answers)
    await svc._reader.read_due(live, 0)
    assert svc.balances.get(7) is None
    assert svc.balances[8] == 70


@pytest.mark.asyncio
async def test_a_short_reply_leaves_the_rest_for_the_next_read():
    svc, tw, live = reader_setup([answer(10)])
    await svc._reader.read_due(live, 0)
    assert svc.balances == {7: 10}


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", [TimeoutError(), aiohttp.ClientError("body")])
async def test_a_failed_batch_waits_for_the_next_read(failure, caplog):
    svc, tw, live = reader_setup([])
    tw.gql_request = AsyncMock(side_effect=[failure, [answer(5), answer(6)]])
    with caplog.at_level(logging.WARNING, logger="TwitchDrops"):
        await svc._reader.read_due(live, 0)
    assert svc.balances == {}
    assert "ChannelPointsContext" in caplog.text
    await svc._reader.read_due(live, POINTS_READ_SECONDS - 1)
    assert tw.gql_request.await_count == 1
    await svc._reader.read_due(live, POINTS_READ_SECONDS)
    assert svc.balances == {7: 5, 8: 6}


@pytest.mark.asyncio
async def test_a_batch_that_hangs_times_out(monkeypatch, caplog):
    monkeypatch.setattr("src.services.idle_points.POINTS_READ_TIMEOUT", 0.01)
    svc, tw, live = reader_setup([])

    async def hang(op):
        await asyncio.sleep(1)

    tw.gql_request = AsyncMock(side_effect=hang)
    with caplog.at_level(logging.WARNING, logger="TwitchDrops"):
        await svc._reader.read_due(live, 0)
    assert caplog.messages == ["Twitch request ChannelPointsContext timed out"]


@pytest.mark.asyncio
async def test_multipliers_keep_only_numeric_factors():
    svc, tw, live = reader_setup([])
    raw = answer(1)
    raw["data"]["community"]["channel"]["self"]["communityPoints"]["activeMultipliers"] = [
        {"factor": 0.2}, {"factor": True}, {"factor": "1"}, {}, "x", {"factor": 1},
    ]
    await svc.apply_points(live[1], raw)
    assert svc.multipliers[7] == (0.2, 1)


@pytest.mark.asyncio
async def test_tick_runs_the_read_after_the_watch():
    svc, tw, live = reader_setup([answer(1), answer(2)])
    tw.settings.share_watch_streaks = False
    tw.watching_channel = SimpleNamespace(get_with_default=lambda d: tw._watching or d)
    tw._watching = None
    tw.watch = MagicMock(side_effect=lambda c, update_status=True: setattr(tw, "_watching", c))
    tw.stop_watching = MagicMock()
    tw.change_state = MagicMock()
    svc.track = AsyncMock()
    svc.live_channels = lambda: live
    svc.visited = {1230: 123, 70: 7, 80: 8}  # no visits due: a is watched
    svc.watched = None
    await svc._tick()
    assert tw.watch.call_args.args == (live[0],)
    assert batches(tw) == [["b", "c"]]
    assert svc.balances[7] == 1 and svc.balances[8] == 2


# --- gains for the points tab ---


@pytest.mark.asyncio
async def test_gains_of_listed_channels_reach_the_tab_and_the_log():
    svc, tw, _ = points_setup()
    tw.channels[999] = SimpleNamespace(id=999, name="Z", login="z")
    await svc.process_points(1, earned("WATCH", channel_id="7", gain=10))
    tw.gui.points.gain.assert_called_once_with("b", 10, "WATCH")
    # the log names the reason like the tab does
    tw.gui.points.log.assert_called_once_with("gain", channel="B", points=10, reason="Watching")
    await svc.process_points(1, earned("CLAIM", gain=50))  # claim() logs the bonus line itself
    tw.gui.points.gain.assert_called_with("a", 50, "CLAIM")
    assert tw.gui.points.log.call_count == 1
    await svc.process_points(1, earned("WATCH", channel_id="999"))  # unlisted
    await svc.process_points(1, earned("WATCH", channel_id="7", gain="10"))  # unusable amount
    assert tw.gui.points.gain.call_count == 2
    # a reason code the tab has no name for is logged as Twitch sent it
    await svc.process_points(1, earned("NEW_CODE", channel_id="7", gain=5))
    tw.gui.points.log.assert_called_with("gain", channel="B", points=5, reason="NEW_CODE")
    assert tw.gui.points.gain.call_count == 3
    svc._task = MagicMock(done=lambda: True)
    await svc.process_points(1, earned("WATCH", channel_id="7"))
    assert tw.gui.points.gain.call_count == 3


# --- watch priority ---


def ch(cid):
    return SimpleNamespace(id=cid)


@pytest.mark.parametrize("mode,balances,factors,expected", [
    ("list", {1: 5, 2: 1}, {}, [1, 2, 3, 4]),
    ("points_low", {1: 50, 2: 10, 4: 10}, {}, [2, 4, 1, 3]),  # tie keeps list order, unknown last
    ("points_high", {1: 50, 2: 10, 4: 50}, {}, [1, 4, 2, 3]),
    ("points_high", {}, {}, [1, 2, 3, 4]),  # all unknown: list order
    ("points_low", {3: 0}, {}, [3, 1, 2, 4]),  # zero is a known balance
    ("subscribed", {}, {3: (0.2,), 4: (0.2, 0.5), 1: ()}, [4, 3, 1, 2]),
    ("subscribed", {}, {2: (0.2,), 3: (0.2,)}, [2, 3, 1, 4]),
    ("subscribed", {}, {}, [1, 2, 3, 4]),
])
def test_priority_orders(mode, balances, factors, expected):
    live = [ch(1), ch(2), ch(3), ch(4)]
    assert [c.id for c in WatchPriority.order(live, mode, balances, factors)] == expected


def priority_service(**options):
    a, b, c = (fake(cid, cid * 10, login=f"c{cid}") for cid in (1, 2, 3))
    svc, tw = service({1: a, 2: b, 3: c}, idle=["c1", "c2", "c3"])
    tw.settings.__dict__.update(options)
    return svc, [a, b, c]


def test_visits_and_the_after_visit_pick_follow_the_priority():
    svc, live = priority_service(idle_priority="points_high")
    svc.balances = {1: 10, 2: 30}
    assert svc.pick(live, 0).id == 2  # the first visit goes to the highest balance
    svc.visited = {10: 1, 20: 2, 30: 3}
    svc.visit = None
    assert svc.pick(live, 1).id == 2
    svc.balances[3] = 99
    assert svc.pick(live, 2).id == 3


def test_streak_first_off_makes_no_visits_and_picks_by_priority():
    svc, live = priority_service(idle_priority="points_low", idle_streak_first=False)
    svc.balances = {1: 10, 2: 5}
    assert svc.pick(live, 0).id == 2
    assert svc.visit is None and svc.visited == {}
    # a visit in progress when the option is turned off is dropped, not recorded
    svc.visit = Visit(1, 10, 0)
    assert svc.pick(live, 1).id == 2
    assert svc.visit is None and 10 not in svc.visited


@pytest.mark.parametrize("mode", ["points_low", "points_high"])
def test_crossing_balances_do_not_switch_the_watch_every_tick(mode):
    svc, live = priority_service(idle_priority=mode, idle_streak_first=False)
    live = live[:2]
    low = mode == "points_low"
    picks = []
    for tick in range(10):
        # the two balances pass each other on every tick
        svc.balances = {1: 5000, 2: 5100} if tick % 2 == 0 else {1: 5100, 2: 5000}
        svc.watched = svc.pick(live, tick)
        picks.append(svc.watched.id)
    assert picks == [1 if low else 2] * 10
    first = picks[0]
    other = 3 - first
    # a lead of exactly the margin keeps the watch; one point more switches it
    svc.balances = {first: 5000, other: 5000 - idle_points.SWITCH_MARGIN if low else 5000 + idle_points.SWITCH_MARGIN}
    assert svc.pick(live, 20).id == first
    svc.balances[other] += -1 if low else 1
    svc.watched = svc.pick(live, 21)
    assert svc.watched.id == other
    # an unknown balance of the watched channel, or a watched channel that went offline, gives order[0]
    svc.balances = {1: 5000, 2: 5100} if low else {1: 5100, 2: 5000}
    svc.watched = live[1]
    del svc.balances[2]
    assert svc.pick(live, 22).id == 1
    svc.balances[2] = 4900 if low else 5200
    svc.watched = svc.pick(live, 23)
    assert svc.watched.id == 2
    svc.balances = {1: 5000, 2: 5100} if low else {1: 5100, 2: 5000}
    assert svc.pick(live, 24).id == 2
    assert svc.pick([live[0]], 25).id == 1


def test_the_margin_applies_only_to_the_balance_orders_and_a_visit_still_takes_over():
    svc, live = priority_service(idle_priority="list", idle_streak_first=False)
    svc.balances = {1: 5000, 2: 5100}
    svc.watched = live[1]
    assert svc.pick(live, 0).id == 1  # list order: the first live channel, as before
    svc, live = priority_service(idle_priority="points_low")
    svc.balances = {1: 5000, 2: 5100, 3: 5050}
    svc.visited = {10: 1, 20: 2}
    svc.watched = live[0]
    assert svc.pick(live, 0).id == 3 and svc.visit is not None  # the new stream's visit wins
    svc.watched = live[2]
    svc.visit = Visit(3, 30, 0, done=True)
    assert svc.pick(live, 1).id == 3  # after the visit: 3 is watched and within the margin of 1


# --- watch streak recovery ---


def full_visit(svc, live, cid, start=0):
    svc.visit = Visit(cid, cid * 10, start)
    return svc.pick(live, start + IDLE_VISIT_SECONDS)


def test_a_full_visit_without_the_bonus_gets_recovery_visits_with_gap_and_limit():
    svc, live = priority_service()
    svc.visited = {20: 2, 30: 3}
    full_visit(svc, live, 1)  # ends at 420
    end = IDLE_VISIT_SECONDS
    assert svc.pick(live, end + RECOVERY_GAP_SECONDS - 1).id == 1 and svc.visit is None  # gap not over
    for _ in range(RECOVERY_VISITS):
        picked = svc.pick(live, end + RECOVERY_GAP_SECONDS)
        assert picked.id == 1 and svc.visit.recovery
        end += RECOVERY_GAP_SECONDS + IDLE_VISIT_SECONDS
        assert svc.pick(live, end).id == 1  # the recovery visit ran its time
        assert svc.visit is None
    assert svc.pick(live, end + 10 * RECOVERY_GAP_SECONDS).id == 1
    assert svc.visit is None  # the limit is reached


def test_recovery_visits_wait_for_every_first_visit():
    svc, live = priority_service()
    svc.visited = {30: 3}
    full_visit(svc, [live[0], live[2]], 1)  # channel 2 is offline meanwhile
    picked = svc.pick(live, IDLE_VISIT_SECONDS + RECOVERY_GAP_SECONDS)
    assert picked.id == 2 and not svc.visit.recovery  # its first visit comes before channel 1's recovery


@pytest.mark.parametrize("how", ["done", "gain", "off", "new_broadcast", "cut_short"])
def test_no_recovery_visit(how):
    svc, live = priority_service()
    svc.visited = {20: 2, 30: 3}
    svc._task = MagicMock(done=lambda: False)
    if how == "done":
        svc.visit = Visit(1, 10, 0, done=True)
        svc.pick(live, 5)
    elif how == "cut_short":
        svc.visit = Visit(1, 10, 0)
        svc.pick(live[1:], 5)  # channel 1 left the live list
    else:
        full_visit(svc, live, 1)
    if how == "gain":
        svc._twitch.watching_channel = SimpleNamespace(get_with_default=lambda d: d)
        asyncio.run(svc.process_points(1, earned("WATCH_STREAK", channel_id="1")))
    elif how == "off":
        svc._twitch.settings.streak_recovery = False
    elif how == "new_broadcast":
        live[0].broadcast_id = 11
        svc.visited[11] = 1
    svc.pick(live, IDLE_VISIT_SECONDS + RECOVERY_GAP_SECONDS)
    assert svc.visit is None or not svc.visit.recovery


def test_a_recovery_visit_that_gets_the_bonus_ends_recovery():
    svc, live = priority_service()
    svc.visited = {20: 2, 30: 3}
    full_visit(svc, live, 1)
    svc.pick(live, IDLE_VISIT_SECONDS + RECOVERY_GAP_SECONDS)
    assert svc.visit.recovery
    svc.visit.done = True
    svc.pick(live, IDLE_VISIT_SECONDS + RECOVERY_GAP_SECONDS + 5)
    assert svc._recovery == {}
    svc.pick(live, 10 * RECOVERY_GAP_SECONDS)
    assert svc.visit is None


@pytest.mark.asyncio
async def test_tick_logs_first_and_recovery_visits_to_the_points_log():
    svc, tw = loop_service([fake(1, 10)])
    svc._task = MagicMock(done=lambda: False)
    svc._reader = SimpleNamespace(read_due=AsyncMock())
    await svc._tick()
    tw.gui.points.log.assert_called_with("visit_started", channel="c1")
    svc.visit, svc.visited = None, {10: 1}
    svc._recovery = {10: (1, 0, -RECOVERY_GAP_SECONDS * 2.0)}
    await svc._tick()
    tw.gui.points.log.assert_called_with("recovery_visit_started", channel="c1")
