"""Idle watch channel points: bonus claims, balance, streak detection."""

import logging
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.config import State
from src.services.idle_watch import CLAIM_RETRIES, IdleWatchService, Visit


# the event features are off here; tests/test_points_events.py turns them on
POINTS_OPTIONS = {
    "idle_priority": "list", "idle_streak_first": True, "streak_recovery": True, "follow_raids": False,
    "predictions_enabled": False, "prediction_strategy": "smart", "prediction_percent": 5,
    "prediction_max_points": 1000, "prediction_min_balance": 0, "prediction_min_voters": 0,
    "prediction_min_odds": 1.0, "prediction_timing": "before_close", "prediction_delay_seconds": 6,
    "prediction_timer_percent": 50, "prediction_fixed_option": 1, "claim_moments": False, "join_chat": False,
    "idle_channel_options": {},
}


def chan(cid=123, name="A"):
    return SimpleNamespace(id=cid, name=name, login=name.lower(), online=True, banned=False)


CLAIM_OK = {"data": {"claimCommunityPoints": {"error": None, "claim": {"id": "x"}}}}


def setup(*, active=True, claim=True, watched=None, gql=None):
    watched = watched or chan()
    tw = SimpleNamespace(
        channels={watched.id: watched, 7: chan(7, "B")},
        settings=SimpleNamespace(**POINTS_OPTIONS, idle_channels=["a", "b"], claim_channel_points=claim),
        gql_request=gql or AsyncMock(return_value=CLAIM_OK),
        gui=SimpleNamespace(points=MagicMock(), status=MagicMock()),
        watching_channel=SimpleNamespace(get_with_default=lambda d: watched),
        can_watch=MagicMock(return_value=False),
        is_mining_time=MagicMock(return_value=True),
    )
    svc = IdleWatchService(tw)
    svc._task = MagicMock(done=lambda: not active)
    svc.poke = MagicMock()
    svc.watched = watched  # the loop's pick
    return svc, tw, watched


def claim_msg(claim_id="abc", channel_id="123"):
    return {"type": "claim-available", "data": {"claim": {"id": claim_id, "channel_id": channel_id}}}


def earned(reason, channel_id="123", balance=1510, gain=10):
    return {
        "type": "points-earned",
        "data": {
            "channel_id": channel_id,
            "point_gain": {"reason_code": reason, "total_points": gain},
            "balance": {"balance": balance, "channel_id": channel_id},
        },
    }


def sent(tw):
    return [c.args[0] for c in tw.gql_request.call_args_list]


@pytest.mark.asyncio
async def test_claim_on_the_watched_and_on_another_listed_channel():
    svc, tw, _ = setup()
    await svc.process_points(1, claim_msg(channel_id="7"))
    await svc.process_points(1, claim_msg(claim_id="def"))
    assert [op["variables"] for op in sent(tw)] == [
        {"input": {"claimID": "abc", "channelID": "7"}},
        {"input": {"claimID": "def", "channelID": "123"}},
    ]
    assert all(op["operationName"] == "ClaimCommunityPoints" for op in sent(tw))


@pytest.mark.asyncio
async def test_claim_ignored_when_not_idle():
    svc, tw, _ = setup(active=False)
    await svc.process_points(1, claim_msg())
    await svc.process_points(1, earned("WATCH_STREAK"))
    assert sent(tw) == []
    assert svc.balances == {}


@pytest.mark.asyncio
async def test_claim_respects_setting_and_dedupes():
    svc, tw, _ = setup(claim=False)
    await svc.process_points(1, claim_msg())
    assert sent(tw) == []
    svc, tw, _ = setup()
    await svc.process_points(1, claim_msg())
    await svc.process_points(1, claim_msg())
    assert len(sent(tw)) == 1


@pytest.mark.asyncio
async def test_points_earned_updates_balance_and_status():
    svc, tw, _ = setup()
    await svc.process_points(1, earned("WATCH"))
    assert svc.balances == {123: 1510}
    assert "1510" in tw.gui.status.update.call_args.args[0]
    assert "A" in tw.gui.status.update.call_args.args[0]
    tw.gui.status.update.reset_mock()
    await svc.process_points(1, earned("WATCH", channel_id="7", balance=5))
    assert svc.balances == {123: 1510, 7: 5}
    tw.gui.status.update.assert_not_called()
    await svc.process_points(1, earned("WATCH", channel_id="999", balance=5))
    assert 999 not in svc.balances


@pytest.mark.asyncio
async def test_streak_reason_ends_visit():
    svc, _, _ = setup()
    svc.visit = Visit(123, 10, 0.0)
    await svc.process_points(1, earned("WATCH_STREAK", channel_id="999"))
    assert not svc.visit.done
    svc.poke.assert_not_called()
    await svc.process_points(1, earned("WATCH_STREAK"))
    assert svc.visit.done
    svc.poke.assert_called_once()


@pytest.mark.asyncio
async def test_one_info_per_claim(caplog):
    # the successful claim logs the INFO line; the CLAIM points-earned only DEBUG
    svc, _, _ = setup()
    with caplog.at_level(logging.DEBUG, logger="TwitchDrops"):
        await svc.process_points(1, claim_msg())
        await svc.process_points(1, earned("WATCH"))
        await svc.process_points(1, earned("CLAIM", gain=50))
    infos = [r for r in caplog.records if r.levelno == logging.INFO]
    assert [r.getMessage() for r in infos] == ["Claimed channel points bonus on A"]
    assert any("50" in r.getMessage() for r in caplog.records if r.levelno == logging.DEBUG)
    assert all("1510" not in r.getMessage() for r in caplog.records if r.levelno > logging.DEBUG)


@pytest.mark.asyncio
async def test_failed_claim_logs_no_info_and_is_retried(caplog):
    # a failed request must not mark the claim as done
    gql = AsyncMock(side_effect=[TimeoutError(), CLAIM_OK, CLAIM_OK])
    svc, tw, _ = setup(gql=gql)
    with caplog.at_level(logging.INFO, logger="TwitchDrops"):
        await svc.process_points(1, claim_msg())
        assert not [r for r in caplog.records if r.levelno == logging.INFO]
        await svc.process_points(1, claim_msg())
        await svc.process_points(1, claim_msg())
    assert len(sent(tw)) == 2
    assert len([r for r in caplog.records if r.levelno == logging.INFO]) == 1


@pytest.mark.asyncio
async def test_claim_reply_with_error_field_is_not_marked(caplog):
    reply = {"data": {"claimCommunityPoints": {"error": {"code": "NOT_FOUND"}}}}
    svc, tw, _ = setup(gql=AsyncMock(return_value=reply))
    with caplog.at_level(logging.INFO, logger="TwitchDrops"):
        await svc.process_points(1, claim_msg())
    assert "abc" not in svc._claimed
    assert not [r for r in caplog.records if r.levelno == logging.INFO]
    assert caplog.messages == ["Twitch request ClaimCommunityPoints was not accepted (NOT_FOUND)"]


@pytest.mark.asyncio
async def test_claim_reply_without_error_is_marked(caplog):
    svc, tw, _ = setup(gql=AsyncMock(return_value=CLAIM_OK))
    with caplog.at_level(logging.INFO, logger="TwitchDrops"):
        await svc.process_points(1, claim_msg())
    assert "abc" in svc._claimed
    assert len([r for r in caplog.records if r.levelno == logging.INFO]) == 1


@pytest.mark.asyncio
async def test_claim_in_flight_is_sent_once():
    import asyncio

    release = asyncio.Event()

    async def slow(_op, **_):
        await release.wait()
        return {"data": {}}

    svc, tw, _ = setup(gql=AsyncMock(side_effect=slow))
    first = asyncio.create_task(svc.process_points(1, claim_msg()))
    await asyncio.sleep(0)
    second = asyncio.create_task(svc.process_points(1, claim_msg()))
    await asyncio.sleep(0)
    assert len(sent(tw)) == 1
    release.set()
    await asyncio.wait_for(asyncio.gather(first, second), 1)
    assert len(sent(tw)) == 1


@pytest.mark.asyncio
async def test_bool_balance_is_ignored():
    svc, _, _ = setup()
    await svc.process_points(1, earned("WATCH", balance=True))
    assert svc.balances == {}


@pytest.mark.asyncio
async def test_no_claim_on_a_channel_drops_can_use():
    # a mineable channel is not idle watched, even if the loop has not noticed yet
    svc, tw, _ = setup()
    tw.can_watch.return_value = True
    await svc.process_points(1, claim_msg())
    await svc.process_points(1, earned("WATCH"))
    assert sent(tw) == []
    tw.gui.status.update.assert_not_called()


@pytest.mark.asyncio
async def test_unexpected_points_error_is_logged_like_process_drops(caplog):
    # process_points runs as a websocket callback task; task_wrapper logs the traceback
    # (then re-raises into that task, exactly as for process_drops)
    svc, _, _ = setup()
    svc._points_earned = AsyncMock(side_effect=RuntimeError("boom"))
    with caplog.at_level(logging.ERROR, logger="TwitchDrops"), pytest.raises(RuntimeError):
        await svc.process_points(1, earned("WATCH"))
    assert [r.getMessage() for r in caplog.records] == ["Exception in process_points task"]


@pytest.mark.asyncio
async def test_points_message_is_not_logged(caplog):
    svc, _, _ = setup()
    with caplog.at_level(logging.DEBUG, logger="TwitchDrops"):
        await svc.process_points(1, earned("WATCH"))
    assert "points-earned" not in caplog.text and "1510" not in caplog.text


@pytest.mark.asyncio
async def test_refresh_points_reads_balance_and_claims_open_bonus():
    context = {"data": {"community": {"channel": {"self": {"communityPoints": {
        "balance": 900, "availableClaim": {"id": "x"},
    }}}}}}
    svc, tw, watched = setup(gql=AsyncMock(side_effect=[context, CLAIM_OK, context]))
    await svc.refresh_points(watched)
    assert svc.balances == {123: 900}
    ops = sent(tw)
    assert [o["operationName"] for o in ops] == ["ChannelPointsContext", "ClaimCommunityPoints"]
    assert ops[0]["variables"] == {"channelLogin": "a"}
    assert ops[1]["variables"]["input"]["claimID"] == "x"
    await svc.refresh_points(watched)
    assert [o["operationName"] for o in sent(tw)].count("ClaimCommunityPoints") == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("message", [
    {}, {"type": "points-earned"}, {"type": "points-earned", "data": []},
    {"type": "points-earned", "data": {"channel_id": "x"}},
    {"type": "points-earned", "data": {"channel_id": "123", "point_gain": 5, "balance": "n"}},
    {"type": "claim-available", "data": {"claim": {"id": 5, "channel_id": "123"}}},
    {"type": "claim-available", "data": {"claim": None}},
    {"type": "claim-available", "data": {"claim": {"id": "a", "channel_id": None}}},
    {"type": 5, "data": None},
])
async def test_malformed_points_message_is_ignored(message):
    svc, tw, _ = setup()
    await svc.process_points(1, message)
    assert sent(tw) == []
    assert svc.balances == {}


@pytest.mark.asyncio
async def test_gql_failures_log_one_warning_without_body(caplog):
    svc, tw, watched = setup(gql=AsyncMock(side_effect=TimeoutError("secret-body")))
    with caplog.at_level(logging.WARNING, logger="TwitchDrops"):
        await svc.process_points(1, claim_msg())
        await svc.refresh_points(watched)
    assert len([r for r in caplog.records if r.levelno == logging.WARNING]) == 2
    assert "secret-body" not in caplog.text
    svc, tw, _ = setup(gql=AsyncMock(return_value={"errors": [{"message": "secret-body"}]}))
    caplog.clear()
    with caplog.at_level(logging.WARNING, logger="TwitchDrops"):
        await svc.process_points(1, claim_msg("z"))
    assert len(caplog.records) == 1 and "secret-body" not in caplog.text


@pytest.mark.asyncio
async def test_numeric_channel_id_in_points_messages():
    # both message types normalise the channel id the same way
    svc, tw, _ = setup()
    svc.visit = Visit(123, 10, 0.0)
    await svc.process_points(1, earned("WATCH_STREAK", channel_id=123))
    assert svc.balances == {123: 1510}
    assert svc.visit.done
    await svc.process_points(1, claim_msg(channel_id=123))
    assert [o["operationName"] for o in sent(tw)] == ["ClaimCommunityPoints"]


@pytest.mark.asyncio
@pytest.mark.parametrize("channel_id", [True, 12.3, "\u00b9\u00b2\u00b3", "-123", " 123"])
async def test_unusable_channel_id_is_ignored(channel_id):
    svc, tw, _ = setup()
    svc.visit = Visit(123, 10, 0.0)
    await svc.process_points(1, earned("WATCH_STREAK", channel_id=channel_id))
    await svc.process_points(1, claim_msg(channel_id=channel_id))
    assert svc.balances == {}
    assert not svc.visit.done
    assert sent(tw) == []


@pytest.mark.asyncio
async def test_empty_claim_id_is_not_sent():
    context = {"data": {"community": {"channel": {"self": {"communityPoints": {
        "balance": 900, "availableClaim": {"id": ""},
    }}}}}}
    svc, tw, watched = setup(gql=AsyncMock(return_value=context))
    await svc.process_points(1, claim_msg(claim_id=""))
    await svc.refresh_points(watched)
    assert [o["operationName"] for o in sent(tw)] == ["ChannelPointsContext"]


@pytest.mark.asyncio
async def test_points_context_timeout_uses_the_request_prefix(caplog, monkeypatch):
    # the 15 s limit is a literal: a wait_for that times out at once stands in for it
    async def timed_out(coro, timeout):
        coro.close()
        raise TimeoutError

    svc, tw, watched = setup()
    monkeypatch.setattr("src.services.idle_watch.asyncio.wait_for", timed_out)
    with caplog.at_level(logging.WARNING, logger="TwitchDrops"):
        await svc.refresh_points(watched)
    assert caplog.messages == ["Twitch request ChannelPointsContext timed out"]


@pytest.mark.asyncio
async def test_list_reply_to_a_single_claim_is_rejected(caplog):
    svc, tw, _ = setup(gql=AsyncMock(return_value=[CLAIM_OK]))
    with caplog.at_level(logging.INFO, logger="TwitchDrops"):
        await svc.process_points(1, claim_msg())
    assert "abc" not in svc._claimed
    assert caplog.messages == ["Twitch request ClaimCommunityPoints returned errors"]


def context(claim_id="x"):
    claim = {"id": claim_id} if claim_id else None
    return {"data": {"community": {"channel": {"self": {"communityPoints": {
        "balance": 900, "availableClaim": claim,
    }}}}}}


def tick_setup(gql, *live):
    """A real _tick on a fake client: live channels in list order, track() a no-op."""
    live = live or (SimpleNamespace(id=123, name="A", login="a", online=True, banned=False, broadcast_id=77),)
    current = {"live": list(live), "watching": None}
    tw = SimpleNamespace(
        channels={c.id: c for c in live},
        settings=SimpleNamespace(**POINTS_OPTIONS, idle_channels=[c.login for c in live], claim_channel_points=True,
                                 share_watch_streaks=False),
        gql_request=gql,
        gui=SimpleNamespace(points=MagicMock(), status=MagicMock()),
        watching_channel=SimpleNamespace(get_with_default=lambda d: current["watching"] or d),
        can_watch=MagicMock(return_value=False),
        is_mining_time=MagicMock(return_value=True),
        stop_watching=MagicMock(),
        change_state=MagicMock(),
        watch=MagicMock(side_effect=lambda c, update_status=True: current.__setitem__("watching", c)),
    )
    svc = IdleWatchService(tw)
    svc._task = MagicMock(done=lambda: False)
    svc.track = AsyncMock()
    svc.live_channels = lambda: current["live"]
    # these tests cover the watched channel's reads; the batched read of all channels has its own tests
    svc._reader = SimpleNamespace(read_due=AsyncMock())
    return svc, tw, current


def names(tw):
    return [o["operationName"] for o in sent(tw)]


@pytest.mark.asyncio
async def test_tick_hands_a_picked_channel_drops_can_use_to_drops_unwatched(caplog):
    # the pick can be mined: no watch, balance read or claim before CHANNEL_SWITCH takes it
    svc, tw, _ = tick_setup(AsyncMock(side_effect=[context(), CLAIM_OK]))
    tw.can_watch.return_value = True
    with caplog.at_level(logging.INFO, logger="TwitchDrops"):
        assert await svc._tick() is True
    assert sent(tw) == []
    tw.watch.assert_not_called()
    tw.change_state.assert_called_once_with(State.CHANNEL_SWITCH)
    assert "Idle watching stopped: a drop channel can be watched" in caplog.messages


@pytest.mark.asyncio
async def test_claim_and_points_read_refuse_a_channel_drops_can_use():
    svc, tw, watched = setup(gql=AsyncMock(side_effect=[context(), CLAIM_OK]))
    tw.can_watch.return_value = True
    await svc.claim(watched, "x")
    await svc.refresh_points(watched)
    assert sent(tw) == []


@pytest.mark.asyncio
async def test_no_claim_while_paused_or_outside_mining_hours():
    # can_watch is False then too, so the drops check alone would let the claim through
    svc, tw, watched = setup(gql=AsyncMock(side_effect=[context(), CLAIM_OK]))
    tw.is_mining_time.return_value = False
    await svc.process_points(1, claim_msg())
    await svc.claim(watched, "x")
    await svc.refresh_points(watched)
    assert "ClaimCommunityPoints" not in names(tw)


FAILED_CLAIMS = [
    TimeoutError(),
    {"data": {"claimCommunityPoints": {"error": {"code": "INTERNAL"}}}},
    {"data": {"claimCommunityPoints": {}}},
    {"data": {"claimCommunityPoints": {"error": None}}},
    {"data": {"claimCommunityPoints": {"error": None, "claim": None}}},
    {"data": {"claimCommunityPoints": None}},
    # a claim object counts only with its id
    {"data": {"claimCommunityPoints": {"error": None, "claim": {}}}},
    {"data": {"claimCommunityPoints": {"error": None, "claim": {"id": ""}}}},
    {"data": {"claimCommunityPoints": {"error": None, "claim": {"id": 5}}}},
]


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", FAILED_CLAIMS, ids=repr)
async def test_failed_claim_is_retried_on_the_next_tick_until_it_succeeds(failure):
    gql = AsyncMock(side_effect=[context(), failure, context(), CLAIM_OK])
    svc, tw, _ = tick_setup(gql)
    await svc._tick()
    assert names(tw) == ["ChannelPointsContext", "ClaimCommunityPoints"]
    assert "x" not in svc._claimed
    await svc._tick()
    await svc._tick()  # claimed: no further read
    assert names(tw) == ["ChannelPointsContext", "ClaimCommunityPoints"] * 2
    assert "x" in svc._claimed


@pytest.mark.asyncio
async def test_claim_retry_stops_when_the_bonus_is_gone():
    gql = AsyncMock(side_effect=[context(), FAILED_CLAIMS[1], context(None)])
    svc, tw, _ = tick_setup(gql)
    for _ in range(3):
        await svc._tick()
    assert names(tw) == ["ChannelPointsContext", "ClaimCommunityPoints", "ChannelPointsContext"]


@pytest.mark.asyncio
async def test_claim_retry_stops_on_a_channel_change():
    a = SimpleNamespace(id=123, name="A", login="a", online=True, banned=False, broadcast_id=77)
    b = SimpleNamespace(id=7, name="B", login="b", online=True, banned=False, broadcast_id=78)
    # b's points read fails, so only the channel change can end a's retry
    gql = AsyncMock(side_effect=[context(), FAILED_CLAIMS[1], TimeoutError()])
    svc, tw, current = tick_setup(gql, a, b)
    svc.visited = {77: 123, 78: 7}  # both visited: the pick is the first live channel
    await svc._tick()
    current["live"] = [b]  # a goes offline
    await svc._tick()
    await svc._tick()
    assert names(tw) == ["ChannelPointsContext", "ClaimCommunityPoints", "ChannelPointsContext"]
    assert sent(tw)[2]["variables"] == {"channelLogin": "b"}


@pytest.mark.asyncio
async def test_claim_retries_stop_after_two_per_channel():
    # a bonus Twitch keeps offering and refusing is read again twice, then left to claim-available
    svc, tw, _ = tick_setup(AsyncMock(side_effect=[context(), FAILED_CLAIMS[1]] * 3))
    for _ in range(5):
        await svc._tick()
    assert names(tw) == ["ChannelPointsContext", "ClaimCommunityPoints"] * 3


@pytest.mark.asyncio
async def test_a_successful_claim_resets_the_retry_count():
    replies = [
        context(), FAILED_CLAIMS[1],  # tick 1: the claim fails
        context(), CLAIM_OK,  # tick 2: first retry succeeds
        FAILED_CLAIMS[1],  # claim-available for a new bonus fails
        context("abc"), FAILED_CLAIMS[1],  # tick 3: retry 1 of the new bonus
        context("abc"), FAILED_CLAIMS[1],  # tick 4: retry 2
    ]
    svc, tw, _ = tick_setup(AsyncMock(side_effect=replies))
    await svc._tick()
    await svc._tick()
    await svc.process_points(1, claim_msg())
    for _ in range(3):
        await svc._tick()
    assert names(tw) == [
        "ChannelPointsContext", "ClaimCommunityPoints", "ChannelPointsContext", "ClaimCommunityPoints",
        "ClaimCommunityPoints",
        "ChannelPointsContext", "ClaimCommunityPoints", "ChannelPointsContext", "ClaimCommunityPoints",
    ]


@pytest.mark.asyncio
async def test_a_channel_change_resets_the_retry_count():
    a = SimpleNamespace(id=123, name="A", login="a", online=True, banned=False, broadcast_id=77)
    b = SimpleNamespace(id=7, name="B", login="b", online=True, banned=False, broadcast_id=78)
    svc, tw, current = tick_setup(AsyncMock(side_effect=[context(), FAILED_CLAIMS[1]] * 6), a, b)
    svc.visited = {77: 123, 78: 7}
    for _ in range(4):
        await svc._tick()  # a: one claim and two retries, then nothing
    current["live"] = [b]
    for _ in range(4):
        await svc._tick()  # b: the same again
    assert names(tw) == ["ChannelPointsContext", "ClaimCommunityPoints"] * 6
    assert [o["variables"] for o in sent(tw)[6::2]] == [{"channelLogin": "b"}] * 3


@pytest.mark.asyncio
async def test_a_leftover_channel_the_loop_did_not_pick_gets_no_claim_or_points():
    # idle entry: the drop channel watched before is still watching_channel until the loop's first pick
    leftover = SimpleNamespace(id=555, name="X", login="x", online=True, banned=False)
    svc, tw, _ = setup(watched=leftover)
    svc.watched = None
    await svc.process_points(1, claim_msg(channel_id="555"))
    await svc.process_points(1, earned("WATCH", channel_id="555", balance=777))
    assert sent(tw) == []
    assert svc.balances == {}
    tw.gui.status.update.assert_not_called()
    svc.watched = leftover  # even as the loop's pick, an unlisted channel gets no claim
    await svc.process_points(1, claim_msg(channel_id="555"))
    await svc.claim(leftover, "direct")
    assert sent(tw) == []
    tw.settings.idle_channels.append("x")  # once listed, it is idle watched
    await svc.process_points(1, claim_msg(channel_id="555"))
    assert [op["operationName"] for op in sent(tw)] == ["ClaimCommunityPoints"]


@pytest.mark.asyncio
async def test_a_re_watch_of_the_same_pick_counts_as_a_claim_retry():
    # whatever clears watching_channel, the failed claim is retried at most CLAIM_RETRIES times
    async def gql(op):
        return context() if op["operationName"] == "ChannelPointsContext" else FAILED_CLAIMS[1]
    svc, tw, current = tick_setup(AsyncMock(side_effect=gql))
    for _ in range(8):
        current["watching"] = None
        await svc._tick()
    claims = [op for op in sent(tw) if op["operationName"] == "ClaimCommunityPoints"]
    assert len(claims) == 1 + CLAIM_RETRIES
    assert current["watching"] is not None  # it is still watched again each tick
