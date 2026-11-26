"""Channel points events of idle watching: topics, raids, moments, predictions and chat presence."""

import asyncio
import logging
import re
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import aiohttp
import pytest

import src.services.chat_presence as chat_mod
import src.services.points_common as points_common
import src.services.predictions as pred_mod
from src.api.gql_client import GQLClient
from src.config import MAX_WEBSOCKETS, WS_TOPICS_LIMIT, ClientType, State, WebsocketTopic
from src.core.client import Twitch
from src.exceptions import GQLException, GQLServerError, MinerException, RequestException
from src.services.chat_presence import ChatPresence
from src.services.idle_watch import IdleWatchService
from src.services.predictions import PredictionStrategy
from src.web.managers.settings import SettingsManager
from src.websocket.pool import WebsocketPool
from tests.test_idle_points import POINTS_OPTIONS
from tests.test_idle_watch import fake, loop_service


ON = {"predictions_enabled": True, "follow_raids": True, "claim_moments": True, "join_chat": True}


def chan(cid, login, *, online=True):
    return SimpleNamespace(id=cid, login=login, name=login.upper(), online=online, banned=False, broadcast_id=cid * 10)


def setup(*, reply=None, **options):
    """A running idle service watching a; a and b are listed and live, z is tracked but not listed."""
    a, b, z = chan(1, "a"), chan(2, "b"), chan(9, "z")
    tw = SimpleNamespace(
        channels={1: a, 2: b, 9: z},
        settings=SimpleNamespace(**(POINTS_OPTIONS | ON | options), idle_channels=["a", "b"], proxy=""),
        gql_request=AsyncMock(return_value=reply),
        gui=SimpleNamespace(points=MagicMock(), websockets=MagicMock()),
        can_watch=MagicMock(return_value=False),
        is_mining_time=MagicMock(return_value=True),
        logged_in_user_id=lambda: 42,
    )
    tw.watching_channel = SimpleNamespace(get_with_default=lambda d: tw._watching)
    tw._watching = a
    tw.websocket = WebsocketPool(tw)
    svc = IdleWatchService(tw)
    svc._task = MagicMock(done=lambda: False)
    svc.watched = a
    svc.balances = {1: 1000, 2: 1000}
    return svc, tw, a, b


def subscribed(tw):
    return {name for ws in tw.websocket.websockets for name in ws.topics}


def sent(tw):
    return [c.args[0] for c in tw.gql_request.call_args_list]


def logged(tw):
    return [c.args[0] for c in tw.gui.points.log.call_args_list]


async def settle():
    for _ in range(5):
        await asyncio.sleep(0)


# --- topics ---


ALL = {
    "predictions-user-v1.42", "predictions-channel-v1.1", "predictions-channel-v1.2",
    "community-moments-channel-v1.1", "community-moments-channel-v1.2", "raid.1",
}


def test_sync_subscribes_the_enabled_features_for_live_listed_channels():
    svc, tw, *_ = setup()
    svc.events.sync()
    # z is tracked but not listed; the raid topic is only for the watched channel
    assert subscribed(tw) == ALL


@pytest.mark.parametrize(("option", "gone"), [
    ("predictions_enabled", {"predictions-user-v1.42", "predictions-channel-v1.1", "predictions-channel-v1.2"}),
    ("claim_moments", {"community-moments-channel-v1.1", "community-moments-channel-v1.2"}),
    ("follow_raids", {"raid.1"}),
])
def test_turning_a_feature_off_removes_its_topics(option, gone):
    svc, tw, *_ = setup()
    svc.events.sync()
    setattr(tw.settings, option, False)
    svc.events.sync()
    assert subscribed(tw) == ALL - gone
    setattr(tw.settings, option, True)
    svc.events.sync()
    assert subscribed(tw) == ALL


def test_raid_topic_follows_the_watched_channel_and_offline_channels_lose_theirs():
    svc, tw, a, b = setup()
    svc.events.sync()
    svc.watched = b
    a.online = False
    svc.events.sync()
    assert subscribed(tw) == {
        "predictions-user-v1.42", "predictions-channel-v1.2", "community-moments-channel-v1.2", "raid.2",
    }


def test_stop_and_pause_remove_every_topic_and_other_topics_stay():
    svc, tw, *_ = setup()
    other = WebsocketTopic("User", "Drops", 42, AsyncMock())
    tw.websocket.add_topics([other])
    svc.events.sync()
    svc.stop()
    assert subscribed(tw) == {"user-drop-events.42"}
    svc._task = MagicMock(done=lambda: False)
    svc.points_paused = True
    svc.events.sync()
    assert subscribed(tw) == {"user-drop-events.42"}
    svc.points_paused = False
    svc._task = MagicMock(done=lambda: True)  # the loop ended
    svc.events.sync()
    assert subscribed(tw) == {"user-drop-events.42"}


def fill(tw, count):
    tw.websocket.add_topics([WebsocketTopic("Channel", "StreamState", i, AsyncMock()) for i in range(1000, 1000 + count)])


def test_topic_limit_keeps_room_for_untracked_listed_channels_and_is_logged_once(caplog):
    svc, tw, *_ = setup()
    capacity = MAX_WEBSOCKETS * WS_TOPICS_LIMIT
    tw.settings.idle_channels.append("c")  # listed, not tracked yet: its 2 stream topics stay free
    fill(tw, capacity - 5)
    with caplog.at_level(logging.WARNING, logger="TwitchDrops"):
        svc.events.sync()
        svc.events.sync()
    assert len(subscribed(tw)) == capacity - 2  # 3 of the 6 topics fit
    assert logged(tw).count("topic_limit") == 1
    assert len([r for r in caplog.records if "Topic limit" in r.message]) == 1
    # room again: the rest is added and a later limit is logged again
    tw.websocket.remove_topics([f"video-playback-by-id.{i}" for i in range(1000, 1010)])
    svc.events.sync()
    assert subscribed(tw) >= ALL
    tw.settings.follow_raids = False
    svc.events.sync()
    # fill the pool up to the 2 reserved slots
    tw.websocket.add_topics([WebsocketTopic("Channel", "StreamState", i, AsyncMock()) for i in range(2000, 2008)])
    tw.settings.follow_raids = True
    svc.events.sync()
    assert logged(tw).count("topic_limit") == 2


def test_a_pool_limit_error_is_caught_and_logged_once():
    svc, tw, *_ = setup()
    tw.websocket.add_topics = MagicMock(side_effect=MinerException("Maximum topics limit has been reached"))
    svc.events.sync()
    svc.events.sync()
    assert logged(tw).count("topic_limit") == 1


@pytest.mark.asyncio
async def test_the_loop_drops_its_topics_when_it_ends_by_itself_but_not_a_newer_runs(monkeypatch):
    svc, tw = loop_service([fake(1, 10)])
    svc.events = MagicMock()
    svc._chat = MagicMock()
    tw.is_mining_time.side_effect = None
    tw.is_mining_time.return_value = False  # not allowed: the loop ends by itself
    svc.enter()
    await asyncio.wait_for(svc._task, 1)
    svc.events.clear.assert_called_once()
    svc._chat.leave.assert_called_once()
    # a stopped run that finishes after a new run started leaves the new run's events alone
    tw.is_mining_time.return_value = True
    svc.enter()
    old = svc._task
    await asyncio.sleep(0)
    svc.stop()
    svc.events.clear.reset_mock()
    svc.enter()
    await asyncio.gather(old, return_exceptions=True)
    svc.events.clear.assert_not_called()
    svc.stop()


@pytest.mark.asyncio
async def test_tick_syncs_topics_and_follows_the_chat_of_the_watched_channel():
    svc, tw = loop_service([fake(1, 10)])
    svc._task = MagicMock(done=lambda: False)
    svc._reader = SimpleNamespace(read_due=AsyncMock())
    svc.events = MagicMock()
    svc._chat = MagicMock()
    tw.settings.join_chat = True
    await svc._tick()
    svc.events.sync.assert_called_once()
    svc._chat.follow.assert_called_with(svc.watched)
    tw.settings.join_chat = False
    await svc._tick()
    svc._chat.follow.assert_called_with(None)
    tw.settings.join_chat = True
    tw.can_watch.return_value = True  # drops can use it: handed over, no chat
    svc._chat.follow.reset_mock()
    await svc._tick()
    svc._chat.follow.assert_not_called()


# --- raids ---


def raid(raid_id="r1", target="t"):
    return {"type": "raid_update_v2", "raid": {"id": raid_id, "target_login": target}}


@pytest.mark.asyncio
async def test_a_raid_on_the_watched_channel_is_joined_once_with_single_attempt():
    svc, tw, *_ = setup(reply={"data": {"joinRaid": {"raid": {"id": "r1"}}}})
    await svc.events.process_raid(1, raid())
    await svc.events.process_raid(1, raid())  # the countdown repeats the message
    [op] = sent(tw)
    assert op["operationName"] == "JoinRaid" and op["variables"] == {"input": {"raidID": "r1"}}
    assert op.single_attempt is True
    tw.gui.points.log.assert_called_once_with("raid_joined", channel="A", target="t")


@pytest.mark.asyncio
@pytest.mark.parametrize("case", ["other listed channel", "unlisted", "can_watch", "paused", "off", "stopped", "hours"])
async def test_raid_refusals(case):
    svc, tw, *_ = setup()
    channel_id = {"other listed channel": 2, "unlisted": 9}.get(case, 1)
    if case == "unlisted":
        tw._watching = svc.watched = tw.channels[9]
    tw.can_watch.return_value = case == "can_watch"
    svc.points_paused = case == "paused"
    tw.settings.follow_raids = case != "off"
    if case == "stopped":
        svc._task = MagicMock(done=lambda: True)
    tw.is_mining_time.return_value = case != "hours"
    await svc.events.process_raid(channel_id, raid())
    assert sent(tw) == []


@pytest.mark.asyncio
@pytest.mark.parametrize("message", [
    None, [], "raid", {}, {"type": "raid_update_v2"}, {"type": "raid_update_v2", "raid": "r"},
    {"type": "raid_update_v2", "raid": {"id": 5}}, {"type": "raid_update_v2", "raid": {"id": ""}},
    {"type": "raid_go_v2", "raid": {"id": "r1"}},
])
async def test_malformed_raid_messages_are_ignored(message):
    svc, tw, *_ = setup()
    await svc.events.process_raid(1, message)
    assert sent(tw) == []


def browser_gql(tw, reply):
    """The real GQL client answering with reply through the browser path."""
    browser = SimpleNamespace(gql=AsyncMock(return_value=reply))
    auth = SimpleNamespace(validate=AsyncMock(return_value=SimpleNamespace(
        browser_active=True, _twitch=SimpleNamespace(_browser=browser),
    )))
    tw.gql_request = GQLClient(MagicMock(), auth, ClientType.WEB).request
    return browser


@pytest.mark.asyncio
async def test_integrity_check_failed_on_join_raid_is_logged_and_never_resent():
    svc, tw, *_ = setup()
    browser = browser_gql(tw, {"errors": [{"message": "IntegrityCheckFailed", "path": ["joinRaid"]}],
                               "data": {"joinRaid": None}})
    await svc.events.process_raid(1, raid())
    await svc.events.process_raid(1, raid())
    browser.gql.assert_awaited_once()
    assert browser.gql.call_args.kwargs == {"single_attempt": True}
    tw.gui.points.log.assert_called_once_with("raid_failed", channel="A", code="GQLException")


@pytest.mark.asyncio
@pytest.mark.parametrize(("reply", "code"), [
    ({"data": {"joinRaid": {"error": {"code": "RAID_EXPIRED"}}}}, "RAID_EXPIRED"),
    ({"data": {"joinRaid": None}}, "unknown"),
    ({"data": {}}, "unknown"),
    ({"data": {"joinRaid": {"error": {"code": 3}}}}, "unknown"),
])
async def test_join_raid_error_replies(reply, code):
    svc, tw, *_ = setup(reply=reply)
    await svc.events.process_raid(1, raid())
    tw.gui.points.log.assert_called_once_with("raid_failed", channel="A", code=code)


@pytest.mark.asyncio
async def test_join_raid_timeout_is_a_failure(monkeypatch):
    import src.services.points_events as events_mod

    monkeypatch.setattr(events_mod, "MUTATION_TIMEOUT", 0.01)
    svc, tw, *_ = setup()

    async def slow(op):
        await asyncio.sleep(1)

    tw.gql_request = slow
    await svc.events.process_raid(1, raid())
    tw.gui.points.log.assert_called_once_with("raid_failed", channel="A", code="TimeoutError")


# --- moments ---


def moment(moment_id="m1"):
    return {"type": "active", "data": {"moment_id": moment_id, "channel_id": "2"}}


MOMENT_OK = {"data": {"claimCommunityMoment": {"moment": {"id": "m1"}, "error": None}}}


@pytest.mark.asyncio
async def test_a_moment_on_any_live_listed_channel_is_claimed_once():
    svc, tw, *_ = setup(reply=MOMENT_OK)
    await svc.events.process_moment(2, moment())
    await svc.events.process_moment(2, moment())
    [op] = sent(tw)
    assert op["operationName"] == "CommunityMomentCallout_Claim"
    assert op["variables"] == {"input": {"momentID": "m1"}}
    assert op.single_attempt is True  # a claim that may have run is not resent
    tw.gui.points.log.assert_called_once_with("moment_claimed", channel="B")


@pytest.mark.asyncio
@pytest.mark.parametrize("case", ["unlisted", "can_watch", "paused", "off", "offline", "stopped"])
async def test_moment_refusals(case):
    svc, tw, a, b = setup(reply=MOMENT_OK)
    channel_id = 9 if case == "unlisted" else 2
    tw.can_watch.return_value = case == "can_watch"
    svc.points_paused = case == "paused"
    tw.settings.claim_moments = case != "off"
    b.online = case != "offline"
    if case == "stopped":
        svc._task = MagicMock(done=lambda: True)
    await svc.events.process_moment(channel_id, moment())
    assert sent(tw) == []


@pytest.mark.asyncio
@pytest.mark.parametrize("message", [
    None, {"type": "active"}, {"type": "active", "data": {"moment_id": 1}}, {"type": "inactive", "data": {"moment_id": "m"}},
])
async def test_malformed_moment_messages_are_ignored(message):
    svc, tw, *_ = setup(reply=MOMENT_OK)
    await svc.events.process_moment(2, message)
    assert sent(tw) == []


@pytest.mark.asyncio
async def test_a_refused_moment_claim_is_not_logged_as_claimed(caplog):
    svc, tw, *_ = setup(reply={"data": {"claimCommunityMoment": {"moment": None, "error": {"code": "NOT_FOUND"}}}})
    with caplog.at_level(logging.WARNING, logger="TwitchDrops"):
        await svc.events.process_moment(2, moment())
    assert logged(tw) == []
    assert "NOT_FOUND" in caplog.text


# --- prediction strategy and stake ---


def outcomes(*pairs):
    return [{"id": f"o{i}", "title": f"T{i}", "total_users": u, "total_points": p} for i, (u, p) in enumerate(pairs)]


@pytest.mark.parametrize(("strategy", "pairs", "expected"), [
    # two outcomes
    ("most_voted", [(10, 100), (20, 50)], 1),
    ("most_voted", [(10, 100), (10, 500)], 0),  # tie keeps the first
    ("favourite", [(10, 100), (20, 50)], 0),
    ("favourite", [(10, 100), (20, 100)], 0),
    ("high_odds", [(10, 100), (20, 50)], 1),
    ("high_odds", [(10, 0), (20, 50)], 0),  # zero points: the largest payout
    ("high_odds", [(0, 0), (0, 0)], 0),
    ("smart", [(55, 100), (45, 50)], 1),  # split 55/45 within 20: high_odds
    ("smart", [(60, 100), (40, 50)], 1),  # gap exactly 20: still within
    ("smart", [(61, 100), (39, 50)], 0),  # gap 22: most_voted
    ("smart", [(0, 100), (0, 50)], 1),  # no users: an even split
    # three and more outcomes; smart reads the split of the two outcomes with the most users
    ("most_voted", [(1, 1), (5, 1), (5, 1)], 1),
    ("favourite", [(1, 0), (1, 0), (1, 0)], 0),
    ("high_odds", [(1, 30), (1, 10), (1, 10), (1, 20)], 1),
    ("smart", [(50, 10), (50, 20), (900, 30)], 2),  # 900 vs 50 (the first two are even): most_voted
    # a close pair bets the one of the two with fewer points, never a third outcome
    ("smart", [(90, 30), (10, 20), (95, 50)], 0),  # 95 vs 90: 30 < 50; the 20-point outcome is not in the pair
    ("smart", [(90, 30), (10, 20), (90, 0)], 2),  # 90 vs 90
    ("smart", [(10, 1), (61, 30), (39, 20)], 1),  # 61 vs 39, gap 22: most_voted
    ("smart", [(10, 1), (60, 30), (40, 20)], 2),  # 60 vs 40, gap 20: fewer points of the pair
    ("smart", [(1, 0), (45, 10), (55, 10)], 1),  # a points tie within the pair keeps the earlier outcome
    ("smart", [(50, 10), (1, 0), (50, 10)], 0),
    ("smart", [(5, 0), (0, 0), (5, 9), (0, 0)], 0),
    ("smart", [(0, 0), (0, 9), (0, 5), (0, 0)], 0),  # no users: the pair is the first two
    ("smart", [(0, 9), (0, 5), (0, 0)], 1),
    ("smart", [(100, 1), (40, 2), (60, 3), (60, 4)], 0),  # 100 vs 60 (a tie keeps the earlier 60): most_voted
])
def test_strategies(strategy, pairs, expected):
    assert PredictionStrategy.choose(strategy, outcomes(*pairs)) == expected


def test_strategy_needs_two_outcomes_and_ignores_bad_counts():
    assert PredictionStrategy.choose("smart", outcomes((1, 1))) is None
    assert PredictionStrategy.choose("most_voted", []) is None
    bad = [{"id": "a", "total_users": "9", "total_points": -5}, {"id": "b", "total_users": True, "total_points": 1}]
    assert PredictionStrategy.choose("most_voted", bad) == 0
    assert PredictionStrategy.choose("high_odds", bad) == 0


@pytest.mark.parametrize(("balance", "percent", "maximum", "expected"), [
    (0, 5, 1000, 0),
    (199, 5, 1000, 9),  # rounded down: below the 10-point minimum
    (200, 5, 1000, 10),
    (10**9, 5, 1000, 1000),
    (10**9, 100, 250000, 250000),
    (15, 100, 1000, 15),
    (-50, 5, 1000, 0),
])
def test_amount(balance, percent, maximum, expected):
    assert PredictionStrategy.amount(balance, percent, maximum) == expected


@pytest.mark.parametrize(("created", "window", "expected"), [
    (-60, 120, 54),
    (0, 3, 0),  # less than the delay left: bet right away
    (-600, 120, 0),
])
def test_delay(created, window, expected):
    start = (datetime.now(timezone.utc) + timedelta(seconds=created)).isoformat()
    delay = pred_mod.Predictions._delay({"created_at": start, "prediction_window_seconds": window})
    assert delay == pytest.approx(expected, abs=1)


@pytest.mark.parametrize("event", [
    {"created_at": "2026-11-26T12:00:00.123456789Z", "prediction_window_seconds": 0},
    {"created_at": "2026-11-26T12:00:00", "prediction_window_seconds": 60},  # no time zone
    {"created_at": "yesterday", "prediction_window_seconds": 60},
    {"created_at": None, "prediction_window_seconds": 60},
    {"created_at": "2026-11-26T12:00:00Z", "prediction_window_seconds": "60"},
    {"created_at": "2026-11-26T12:00:00Z", "prediction_window_seconds": True},
])
def test_delay_of_a_malformed_event_is_none(event):
    assert pred_mod.Predictions._delay(event) is None


def test_delay_reads_nanosecond_timestamps():
    start = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.123456789Z")
    assert pred_mod.Predictions._delay({"created_at": start, "prediction_window_seconds": 60}) == pytest.approx(54, abs=2)


# --- predictions ---


def event(event_id="e1", status="ACTIVE", window=60, ago=60, pairs=((10, 100), (20, 50))):
    start = (datetime.now(timezone.utc) - timedelta(seconds=ago)).isoformat()
    return {"type": "event-updated", "data": {"event": {
        "id": event_id, "status": status, "title": "Who wins?", "created_at": start,
        "prediction_window_seconds": window, "outcomes": outcomes(*pairs),
    }}}


BET_OK = {"data": {"makePrediction": {"prediction": {"id": "p"}, "error": None}}}


@pytest.mark.asyncio
async def test_a_bet_is_placed_once_with_the_stake_and_single_attempt():
    svc, tw, *_ = setup(reply=BET_OK, prediction_strategy="most_voted")
    await svc.events.predictions.process_event(1, event())
    await settle()
    await svc.events.predictions.process_event(1, event())  # a later update of the same event
    await settle()
    [op] = sent(tw)
    variables = op["variables"]["input"]
    assert op["operationName"] == "MakePrediction" and op.single_attempt is True
    assert (variables["eventID"], variables["outcomeID"], variables["points"]) == ("e1", "o1", 50)
    assert re.fullmatch(r"[0-9a-f]{32}", variables["transactionID"])
    tw.gui.points.log.assert_called_once_with("bet_placed", points=50, outcome="T1", channel="A", title="Who wins?")


@pytest.mark.asyncio
async def test_the_bet_uses_the_latest_outcomes_and_the_max_points(monkeypatch):
    monkeypatch.setattr(pred_mod, "PREDICTION_DELAY_SECONDS", 0.95)
    svc, tw, *_ = setup(reply=BET_OK, prediction_strategy="most_voted", prediction_max_points=300)
    svc.balances[2] = 10**7
    await svc.events.predictions.process_event(2, event(window=1, ago=0, pairs=((10, 1), (20, 1))))
    await svc.events.predictions.process_event(2, event(window=1, ago=0, pairs=((30, 1), (20, 1))))
    await asyncio.sleep(0.2)
    [op] = sent(tw)
    assert op["variables"]["input"]["outcomeID"] == "o0"
    assert op["variables"]["input"]["points"] == 300


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["LOCKED", "CANCELED", "RESOLVED"])
async def test_an_event_that_ends_before_the_timer_gets_no_bet(monkeypatch, status):
    monkeypatch.setattr(pred_mod, "PREDICTION_DELAY_SECONDS", 0.95)
    svc, tw, *_ = setup(reply=BET_OK)
    await svc.events.predictions.process_event(1, event(window=1, ago=0))
    await svc.events.predictions.process_event(1, event(status=status, window=1, ago=0))
    await asyncio.sleep(0.2)
    assert sent(tw) == []
    assert svc.events.predictions._timers == {}


@pytest.mark.asyncio
async def test_a_status_change_seen_only_at_send_time_refuses_the_bet():
    svc, tw, *_ = setup(reply=BET_OK)
    preds = svc.events.predictions
    message = event(window=1, ago=0)
    await preds.process_event(1, message)
    message["data"]["event"]["status"] = "LOCKED"  # the stored object changed without a message
    await preds.bet("e1")
    assert sent(tw) == []
    assert logged(tw) == ["bet_skipped"]


@pytest.mark.asyncio
@pytest.mark.parametrize("case", [
    "balance 0", "balance 199", "balance unknown", "paused", "off", "can_watch", "offline", "stopped", "hours",
    "one outcome", "no outcome id",
])
async def test_bet_refusals_at_send_time(case):
    svc, tw, a, _ = setup(reply=BET_OK)
    preds = svc.events.predictions
    pairs = ((10, 1),) if case == "one outcome" else ((10, 1), (20, 1))
    message = event(window=600, ago=0, pairs=pairs)
    if case == "no outcome id":
        for o in message["data"]["event"]["outcomes"]:
            o["id"] = ""
    await preds.process_event(1, message)
    preds.clear()  # cancel the timer: the bet is sent by hand below, after the change
    await preds.process_event(1, message)
    preds._timers.pop("e1").cancel()
    if case.startswith("balance"):
        if case == "balance unknown":
            del svc.balances[1]
        else:
            svc.balances[1] = int(case.split()[1])
    svc.points_paused = case == "paused"
    tw.settings.predictions_enabled = case != "off"
    tw.can_watch.return_value = case == "can_watch"
    a.online = case != "offline"
    tw.is_mining_time.return_value = case != "hours"
    if case == "stopped":
        svc._task = MagicMock(done=lambda: True)
    await preds.bet("e1")
    await preds.bet("e1")  # handled: no second try either
    assert sent(tw) == []
    # a bet that may not be placed now (paused, option off, ...) is not a skipped bet in the points log
    not_allowed = case in ("paused", "off", "can_watch", "offline", "stopped", "hours")
    assert logged(tw) == ([] if not_allowed else ["bet_skipped"])
    assert "e1" not in preds._latest  # the fired timer's event is not kept


@pytest.mark.asyncio
@pytest.mark.parametrize("channel_id", [9, 77])
async def test_events_of_unlisted_or_unknown_channels_are_ignored(channel_id):
    svc, tw, *_ = setup(reply=BET_OK)
    await svc.events.predictions.process_event(channel_id, event())
    await settle()
    assert sent(tw) == [] and svc.events.predictions._latest == {}


@pytest.mark.asyncio
async def test_no_event_is_stored_while_the_loop_is_stopped():
    svc, tw, *_ = setup(reply=BET_OK)
    svc._task = MagicMock(done=lambda: True)
    await svc.events.predictions.process_event(1, event())
    await settle()
    assert sent(tw) == [] and svc.events.predictions._timers == {}


@pytest.mark.asyncio
@pytest.mark.parametrize("message", [
    None, "x", {"type": "event-updated"}, {"type": "event-updated", "data": {"event": "e"}},
    {"type": "event-updated", "data": {"event": {"id": 5, "status": "ACTIVE"}}},
    {"type": "event-updated", "data": {"event": {"id": "e1", "status": "ACTIVE", "outcomes": "x"}}},
    {"type": "other", "data": event()["data"]},
])
async def test_malformed_prediction_messages_are_ignored(message):
    svc, tw, *_ = setup(reply=BET_OK)
    await svc.events.predictions.process_event(1, message)
    await settle()
    assert sent(tw) == []


@pytest.mark.asyncio
async def test_malformed_outcomes_are_skipped_at_send_time():
    svc, tw, *_ = setup(reply=BET_OK)
    message = event()
    message["data"]["event"]["outcomes"] = [{"id": "a"}, "b"]
    await svc.events.predictions.process_event(1, message)
    await settle()
    assert sent(tw) == [] and logged(tw) == ["bet_skipped"]


@pytest.mark.asyncio
@pytest.mark.parametrize(("reply", "code"), [
    ({"data": {"makePrediction": {"prediction": None, "error": {"code": "NOT_ENOUGH_POINTS"}}}}, "NOT_ENOUGH_POINTS"),
    ({"data": {"makePrediction": None}}, "unknown"),
    ({"data": {"makePrediction": {"prediction": None, "error": {}}}}, "unknown"),  # an empty error
    ({"data": {"makePrediction": {"prediction": {"id": "p"}, "error": {"code": ""}}}}, "unknown"),
    ({}, "unknown"),
])
async def test_bet_error_replies_are_logged_and_never_resent(reply, code):
    svc, tw, *_ = setup(reply=reply)
    await svc.events.predictions.process_event(1, event())
    await settle()
    await svc.events.predictions.process_event(1, event())
    await settle()
    assert len(sent(tw)) == 1
    tw.gui.points.log.assert_called_once_with("bet_failed", channel="A", code=code)
    assert "e1" in svc.events.predictions._placed


@pytest.mark.asyncio
async def test_a_failed_bet_request_is_not_resent():
    svc, tw, *_ = setup()
    tw.gql_request.side_effect = GQLException("Mutation response was not confirmed")
    await svc.events.predictions.process_event(1, event())
    await settle()
    await svc.events.predictions.process_event(1, event())
    await settle()
    assert tw.gql_request.await_count == 1
    tw.gui.points.log.assert_called_once_with("bet_failed", channel="A", code="GQLException")
    tw.gui.points.log.reset_mock()
    # kept like a sent bet: a result that comes anyway is logged
    await svc.events.predictions.process_result(42, result("WIN", won=120))
    tw.gui.points.log.assert_called_once_with("bet_won", points=120, channel="A", title="Who wins?")


@pytest.mark.asyncio
@pytest.mark.parametrize("error", [
    TimeoutError(), aiohttp.ClientConnectionError(), aiohttp.ServerDisconnectedError(),
    RequestException("Unconfirmed HTTP response"),
    GQLServerError("Mutation response was not confirmed"),  # "service timeout" and the like
])
async def test_a_bet_without_an_answer_may_be_placed_and_its_result_is_logged(error):
    svc, tw, *_ = setup()
    tw.gql_request.side_effect = error
    await svc.events.predictions.process_event(1, event())
    await settle()
    await svc.events.predictions.process_event(1, event())
    await settle()
    assert tw.gql_request.await_count == 1
    tw.gui.points.log.assert_called_once_with("bet_unknown", points=50, channel="A", title="Who wins?")
    tw.gui.points.log.reset_mock()
    await svc.events.predictions.process_result(42, result("LOSE"))
    tw.gui.points.log.assert_called_once_with("bet_lost", channel="A", title="Who wins?")


def made(event_id="e1"):
    return {"type": "prediction-made", "data": {"prediction": {
        "event_id": event_id, "outcome_id": "o0", "points": 10, "result": None,
    }}}


@pytest.mark.asyncio
async def test_a_bet_the_account_made_elsewhere_stops_the_bet(monkeypatch):
    monkeypatch.setattr(pred_mod, "PREDICTION_DELAY_SECONDS", 0.95)
    svc, tw, *_ = setup(reply=BET_OK)
    preds = svc.events.predictions
    await preds.process_result(42, made("e2"))  # before the event is seen
    await preds.process_event(1, event("e2", window=1, ago=0))
    await preds.process_event(1, event("e1", window=1, ago=0))
    await preds.process_result(42, made("e1"))  # while its timer runs
    await preds.process_result(42, made("e3"))
    await preds.process_result(42, made(""))  # no id: ignored
    await asyncio.sleep(0.2)
    await preds.process_event(1, event("e1", window=1, ago=0))
    await preds.bet("e1")
    assert sent(tw) == [] and logged(tw) == []
    assert preds._timers == {} and set(preds._handled) == {"e1", "e2", "e3"}
    await preds.process_event(1, event("e4", window=1, ago=0))  # another event still gets its bet
    await asyncio.sleep(0.2)
    assert len(sent(tw)) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["LOCKED", "CANCELED", "RESOLVED"])
async def test_a_late_active_after_the_end_gets_no_bet(monkeypatch, status):
    monkeypatch.setattr(pred_mod, "PREDICTION_DELAY_SECONDS", 0.95)
    svc, tw, *_ = setup(reply=BET_OK)
    preds = svc.events.predictions
    await preds.process_event(1, event(status=status, window=1, ago=0))  # never seen ACTIVE before
    await preds.process_event(1, event(window=1, ago=0))
    await asyncio.sleep(0.2)
    await preds.bet("e1")
    assert sent(tw) == [] and preds._timers == {} and "e1" in preds._handled


@pytest.mark.asyncio
@pytest.mark.parametrize("change", [
    {"created_at": "yesterday"}, {"created_at": None}, {"created_at": "2026-11-26T12:00:00"},
    {"prediction_window_seconds": 0}, {"prediction_window_seconds": "60"},
])
async def test_an_event_without_a_usable_window_is_logged_as_skipped_once(change):
    svc, tw, *_ = setup(reply=BET_OK)
    message = event()
    message["data"]["event"].update(change)
    await svc.events.predictions.process_event(1, message)
    await svc.events.predictions.process_event(1, message)
    await settle()
    assert sent(tw) == []
    tw.gui.points.log.assert_called_once_with("bet_skipped", channel="A", title="Who wins?")
    assert "e1" not in svc.events.predictions._handled


@pytest.mark.asyncio
async def test_a_later_update_with_a_usable_window_still_gets_the_bet(monkeypatch):
    monkeypatch.setattr(pred_mod, "PREDICTION_DELAY_SECONDS", 0.95)
    svc, tw, *_ = setup(reply=BET_OK)
    bad = event(window=1, ago=0)
    bad["data"]["event"]["created_at"] = None
    await svc.events.predictions.process_event(1, bad)
    await svc.events.predictions.process_event(1, event(window=1, ago=0))
    await asyncio.sleep(0.2)
    assert len(sent(tw)) == 1
    assert logged(tw) == ["bet_skipped", "bet_placed"]


@pytest.mark.asyncio
@pytest.mark.parametrize("case", ["points paused", "can_watch", "offline", "predictions off", "outside mining hours"])
async def test_an_unusable_window_is_not_logged_when_predictions_may_not_act(case):
    svc, tw, a, _ = setup(reply=BET_OK)
    svc.points_paused = case == "points paused"
    tw.can_watch.return_value = case == "can_watch"
    a.online = case != "offline"
    tw.settings.predictions_enabled = case != "predictions off"
    tw.is_mining_time.return_value = case != "outside mining hours"
    message = event()
    message["data"]["event"]["prediction_window_seconds"] = 0
    preds = svc.events.predictions
    await preds.process_event(1, message)
    await settle()
    assert sent(tw) == [] and logged(tw) == [] and "e1" not in preds._handled


def result(kind, event_id="e1", won=None):
    return {"type": "prediction-result", "data": {"prediction": {
        "event_id": event_id, "result": {"type": kind, "points_won": won},
    }}}


@pytest.mark.asyncio
@pytest.mark.parametrize(("message", "line"), [
    (result("WIN", won=120), ("bet_won", {"points": 120, "channel": "A", "title": "Who wins?"})),
    (result("LOSE"), ("bet_lost", {"channel": "A", "title": "Who wins?"})),
    (result("REFUND"), ("bet_refunded", {"channel": "A", "title": "Who wins?"})),
])
async def test_results_of_placed_bets_reach_the_points_log(message, line):
    svc, tw, *_ = setup(reply=BET_OK)
    await svc.events.predictions.process_event(1, event())
    await settle()
    tw.gui.points.log.reset_mock()
    await svc.events.predictions.process_result(42, message)
    await svc.events.predictions.process_result(42, message)  # once
    tw.gui.points.log.assert_called_once_with(line[0], **line[1])


@pytest.mark.asyncio
@pytest.mark.parametrize("message", [
    result("WIN", event_id="other", won=5), result("WIN", won=None), result("DRAW"), None,
    {"type": "prediction-result", "data": {"prediction": {"event_id": "e1"}}},
    {"type": "prediction-made", "data": result("WIN", won=5)["data"]},
])
async def test_other_results_are_ignored(message):
    svc, tw, *_ = setup(reply=BET_OK)
    await svc.events.predictions.process_event(1, event())
    await settle()
    tw.gui.points.log.reset_mock()
    await svc.events.predictions.process_result(42, message)
    tw.gui.points.log.assert_not_called()


@pytest.mark.asyncio
async def test_stop_cancels_pending_bets_and_a_new_run_can_bet_on_the_same_open_event(monkeypatch):
    monkeypatch.setattr(pred_mod, "PREDICTION_DELAY_SECONDS", 0.95)
    svc, tw, *_ = setup(reply=BET_OK)
    await svc.events.predictions.process_event(1, event(window=1, ago=0))
    svc.stop()
    await asyncio.sleep(0.2)
    assert sent(tw) == []
    svc._task = MagicMock(done=lambda: False)
    await svc.events.predictions.process_event(1, event(window=1, ago=0))
    await asyncio.sleep(0.2)
    assert len(sent(tw)) == 1


def test_account_reset_forgets_handled_ids():
    svc, *_ = setup()
    svc.events._raids["r"] = svc.events._moments["m"] = svc.events.predictions._handled["e"] = None
    svc.reset()
    assert not svc.events._raids and not svc.events._moments and not svc.events.predictions._handled


@pytest.mark.parametrize(("reply", "code"), [
    ({"data": {"f": {"error": None}}}, None),
    ({"data": {"f": {"prediction": {"id": "p"}}}}, None),  # no error key
    ({"data": {"f": {"error": {}}}}, "unknown"),  # an empty error is still an error
    ({"data": {"f": {"error": {"code": ""}}}}, "unknown"),
    ({"data": {"f": {"error": {"code": "X"}}}}, "X"),
    ({"data": {"f": {"error": "X"}}}, "unknown"),
    ({"data": {"f": {"error": False}}}, "unknown"),
    ({"data": {"f": {"error": []}}}, "unknown"),
    ({"data": {"f": None}}, "unknown"),
    ({"data": {"f": []}}, "unknown"),
    ({"data": {}}, "unknown"),
    ({}, "unknown"),
    ([], "unknown"),
    (None, "unknown"),
])
def test_error_code(reply, code):
    assert points_common.error_code(reply, "f") == code


def test_handled_ids_are_bounded():
    store = {}
    for i in range(points_common.EVENTS_KEPT + 5):
        points_common.remember(store, str(i))
    assert len(store) == points_common.EVENTS_KEPT and "0" not in store and str(points_common.EVENTS_KEPT + 4) in store


# --- chat presence ---


TOKEN = "s3cr3t-token-value"


def text(data):
    return aiohttp.WSMessage(aiohttp.WSMsgType.TEXT, data, None)


CLOSED = aiohttp.WSMessage(aiohttp.WSMsgType.CLOSED, None, None)


class FakeChat:
    """An IRC websocket that answers with the given messages, then closes."""

    def __init__(self, messages):
        self.messages = list(messages)
        self.sent = []

    async def send_str(self, data):
        self.sent.append(data)

    async def receive(self, timeout=None):
        return self.messages.pop(0) if self.messages else CLOSED

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


def chat_setup(connections, *, login="me"):
    svc, tw, a, b = setup()
    svc.gql = AsyncMock(return_value={"data": {"currentUser": {"login": login}}})
    tw.get_auth = AsyncMock(return_value=SimpleNamespace(access_token=TOKEN))
    session = SimpleNamespace(ws_connect=MagicMock(side_effect=connections))
    tw.get_session = AsyncMock(return_value=session)
    return svc, tw, a, b, session, ChatPresence(tw, svc)


WELCOME = ":tmi.twitch.tv 001 me :Welcome\r\n:me!me.tmi.twitch.tv JOIN #a\r\nPING :tmi.twitch.tv"


@pytest.mark.asyncio
async def test_chat_joins_answers_ping_and_never_sends_a_message(caplog):
    ws = FakeChat([text(WELCOME), text("PING :tmi.twitch.tv\r\n")])
    svc, tw, a, _, session, chat = chat_setup([ws])
    with caplog.at_level(logging.DEBUG):
        assert await chat._session(a) is True
    assert ws.sent == [f"PASS oauth:{TOKEN}", "NICK me", "JOIN #a", "PONG :tmi.twitch.tv", "PONG :tmi.twitch.tv"]
    assert not any(line.startswith(("PRIVMSG", "PART")) for line in ws.sent)
    assert session.ws_connect.call_args.args == ("wss://irc-ws.chat.twitch.tv:443",)
    tw.gui.points.log.assert_called_once_with("chat_joined", channel="A")
    assert TOKEN not in caplog.text


@pytest.mark.asyncio
async def test_chat_reconnects_with_backoff_and_never_logs_the_token(monkeypatch, caplog):
    sleeps = []

    async def fast_sleep(delay):
        sleeps.append(delay)

    monkeypatch.setattr(chat_mod.asyncio, "sleep", fast_sleep)
    error = aiohttp.ClientConnectionError(f"refused for oauth:{TOKEN}")
    login_error = MinerException(f"auth failed {TOKEN}")
    svc, tw, a, _, session, chat = chat_setup([error, login_error, FakeChat([text(WELCOME)]), RuntimeError("closed")])
    with caplog.at_level(logging.DEBUG):
        await chat._run(a)
    assert session.ws_connect.call_count == 4
    assert all(0 < s <= chat_mod.CHAT_BACKOFF_MAX for s in sleeps) and len(sleeps) == 3
    assert "ClientConnectionError" in caplog.text and "MinerException" in caplog.text
    assert TOKEN not in caplog.text
    assert logged(tw) == ["chat_joined", "chat_left"]


@pytest.mark.asyncio
async def test_chat_backoff_starts_over_only_after_a_stable_connection(monkeypatch):
    sleeps, clock = [], [0.0]

    async def fast_sleep(delay):
        sleeps.append(delay)

    class Held(FakeChat):
        """Stays up for the given seconds, then closes."""

        def __init__(self, seconds):
            super().__init__([text(WELCOME)])
            self.seconds = seconds

        async def receive(self, timeout=None):
            if self.messages:
                return self.messages.pop(0)
            clock[0] += self.seconds
            return CLOSED

    monkeypatch.setattr(chat_mod.asyncio, "sleep", fast_sleep)
    monkeypatch.setattr(chat_mod.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr("src.utils.backoff.random.uniform", lambda a, b: 1)  # no variance
    stable = chat_mod.CHAT_STABLE_SECONDS
    # joins that drop at once (also after 59 s) keep the wait growing; one that stayed up 60 s starts it over
    svc, tw, a, _, session, chat = chat_setup([Held(0), Held(0), Held(stable - 1), Held(stable), Held(0), RuntimeError()])
    await chat._run(a)
    assert sleeps == [1, 2, 4, 1, 2]


@pytest.mark.asyncio
async def test_chat_refused_login_is_not_retried_until_the_account_token_or_setting_changes(monkeypatch, caplog):
    sleeps = []

    async def fast_sleep(delay):
        sleeps.append(delay)

    monkeypatch.setattr(chat_mod.asyncio, "sleep", fast_sleep)
    notice = ":tmi.twitch.tv NOTICE * :Login authentication failed"
    svc, tw, a, _, session, chat = chat_setup([FakeChat([text(notice)]) for _ in range(4)])
    with caplog.at_level(logging.DEBUG):
        await chat._run(a)
        assert session.ws_connect.call_count == 1 and sleeps == []
        assert "refused the login" in caplog.text and TOKEN not in caplog.text and "NOTICE" not in caplog.text
        await chat._run(a)  # the next tick: the same account and token are not sent again
        chat.follow(None)  # a switch or pause with the option still on keeps the refusal
        await chat._run(a)
        assert session.ws_connect.call_count == 1
        tw.get_auth.return_value = SimpleNamespace(access_token="renewed-token")
        await chat._run(a)  # a new token
        assert session.ws_connect.call_count == 2
        await chat._run(a)  # refused again: blocked with the new token too
        assert session.ws_connect.call_count == 2
        chat.settings_changed()  # another option saved, join_chat still on: still blocked
        tw.settings.join_chat = False
        chat.follow(None)  # a tick with the option off does not clear it; the save does
        tw.settings.join_chat = True
        await chat._run(a)
        assert session.ws_connect.call_count == 2
        tw.settings.join_chat = False
        chat.settings_changed()  # the save that turned it off
        tw.settings.join_chat = True
        await chat._run(a)
        assert session.ws_connect.call_count == 3
        chat.reset()  # an account change
        await chat._run(a)
        assert session.ws_connect.call_count == 4 and sleeps == []


@pytest.mark.asyncio
@pytest.mark.parametrize("loop", ["stopped", "running"])
async def test_saving_join_chat_off_and_on_clears_a_refused_login_without_a_tick(loop):
    # the settings save path itself, while drops are mined (no loop) or within one tick of a running loop
    notice = ":tmi.twitch.tv NOTICE * :Login authentication failed"
    svc, tw, a, _, session, chat = chat_setup([FakeChat([text(notice)]), FakeChat([])])
    svc._chat = chat
    assert await chat._session(a) is False
    svc._task = None if loop == "stopped" else svc._task
    client = SimpleNamespace(
        idle_watch=svc, gui=tw.gui, _state_change=MagicMock(),
        _state=State.CHANNEL_SWITCH if loop == "stopped" else State.IDLE,
    )
    tw.settings.save = MagicMock()
    manager = SettingsManager(
        SimpleNamespace(emit=AsyncMock()), tw.settings, MagicMock(),
        on_idle_channels_change=lambda: Twitch.idle_channels_changed(client),
    )
    manager.update_settings({"prediction_percent": 7})  # another option: the refusal stays
    assert await chat._session(a) is False and session.ws_connect.call_count == 1
    manager.update_settings({"join_chat": False})
    manager.update_settings({"join_chat": True})
    assert await chat._session(a) is True and session.ws_connect.call_count == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("end", ["notice", "shutdown", "closed", "lost"])
async def test_every_end_of_a_joined_session_leaves_the_chat(monkeypatch, end):
    async def fast_sleep(delay):
        if end in ("closed", "lost"):
            chat._service.watched = None  # no longer wanted: _run ends after the wait

    monkeypatch.setattr(chat_mod.asyncio, "sleep", fast_sleep)

    class Ending(FakeChat):
        async def receive(self, timeout=None):
            if self.messages:
                return self.messages.pop(0)
            if end == "notice":
                return text(":tmi.twitch.tv NOTICE * :Login unsuccessful")
            if end == "shutdown":
                raise RuntimeError("Session is closed")
            if end == "lost":
                raise aiohttp.ClientConnectionError()
            return CLOSED

    svc, tw, a, _, session, chat = chat_setup([Ending([text(WELCOME)])])
    await chat._run(a)
    assert logged(tw) == ["chat_joined", "chat_left"] and chat._joined is False
    tw.gui.points.log.assert_called_with("chat_left", channel="A")


@pytest.mark.asyncio
async def test_chat_backoff_is_bounded(monkeypatch):
    sleeps = []

    async def fast_sleep(delay):
        sleeps.append(delay)

    monkeypatch.setattr(chat_mod.asyncio, "sleep", fast_sleep)
    svc, tw, a, _, session, chat = chat_setup([aiohttp.ClientConnectionError()] * 15 + [RuntimeError()])
    await chat._run(a)
    assert max(sleeps) == chat_mod.CHAT_BACKOFF_MAX


@pytest.mark.asyncio
@pytest.mark.parametrize("case", ["off", "not watched", "paused", "can_watch"])
async def test_chat_does_not_connect_when_not_wanted(case):
    svc, tw, a, b, session, chat = chat_setup([FakeChat([])])
    tw.settings.join_chat = case != "off"
    svc.watched = b if case == "not watched" else a
    svc.points_paused = case == "paused"
    tw.can_watch.return_value = case == "can_watch"
    await chat._run(a)
    session.ws_connect.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("login", [None, "", "Me", "me\r\nPRIVMSG #a :hi", "x" * 26])
async def test_chat_needs_a_valid_account_login(login):
    svc, tw, a, _, session, chat = chat_setup([FakeChat([])], login=login)
    assert await chat._session(a) is True  # retried with the backoff, like a lost connection
    session.ws_connect.assert_not_called()


@pytest.mark.asyncio
async def test_chat_reads_the_login_once_per_account():
    svc, tw, a, _, session, chat = chat_setup([FakeChat([]), FakeChat([])])
    await chat._session(a)
    await chat._session(a)
    svc.gql.assert_awaited_once()
    chat.reset()
    await chat._account_login()
    assert svc.gql.await_count == 2


@pytest.mark.asyncio
async def test_chat_follow_leaves_on_switch_and_stop():
    hold = asyncio.Event()

    class HeldChat(FakeChat):
        async def receive(self, timeout=None):
            if self.messages:
                return self.messages.pop(0)
            await hold.wait()
            return CLOSED

    svc, tw, a, b, session, chat = chat_setup([HeldChat([text(WELCOME)]), HeldChat([])])
    chat.follow(a)
    await settle()
    first = chat._task
    chat.follow(a)  # same channel: kept
    assert chat._task is first
    svc.watched = b
    chat.follow(b)
    await settle()
    assert first.cancelled()
    assert logged(tw) == ["chat_joined", "chat_left"]
    tw.gui.points.log.assert_called_with("chat_left", channel="A")
    chat.follow(None)
    await settle()
    assert chat._task is None and session.ws_connect.call_count == 2


@pytest.mark.asyncio
async def test_stop_leaves_the_chat():
    svc, *_ = setup()
    svc._chat = MagicMock()
    svc.stop()
    svc._chat.leave.assert_called_once()
