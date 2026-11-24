"""Idle watch channel points: bonus claims, balance, streak detection."""

import logging
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.services.idle_watch import IdleWatchService, Visit


def chan(cid=123, name="A"):
    return SimpleNamespace(id=cid, name=name, login=name.lower())


CLAIM_OK = {"data": {"claimCommunityPoints": {"error": None, "claim": {"id": "x"}}}}


def setup(*, active=True, claim=True, watched=None, gql=None):
    watched = watched or chan()
    tw = SimpleNamespace(
        channels={watched.id: watched, 7: chan(7, "B")},
        settings=SimpleNamespace(idle_channels=["a", "b"], claim_channel_points=claim),
        gql_request=gql or AsyncMock(return_value=CLAIM_OK),
        gui=SimpleNamespace(status=MagicMock()),
        watching_channel=SimpleNamespace(get_with_default=lambda d: watched),
        can_watch=MagicMock(return_value=False),
    )
    svc = IdleWatchService(tw)
    svc._task = MagicMock(done=lambda: not active)
    svc.poke = MagicMock()
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
async def test_claim_only_for_idle_watched_channel():
    svc, tw, _ = setup()
    await svc.process_points(1, claim_msg(channel_id="7"))
    assert sent(tw) == []
    await svc.process_points(1, claim_msg())
    [op] = sent(tw)
    assert op["operationName"] == "ClaimCommunityPoints"
    assert op["variables"] == {"input": {"claimID": "abc", "channelID": "123"}}


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
    # F3: the successful claim logs the INFO line; the CLAIM points-earned only DEBUG
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
    # F1: a failed request must not mark the claim as done
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
    # F7
    svc, _, _ = setup()
    await svc.process_points(1, earned("WATCH", balance=True))
    assert svc.balances == {}


@pytest.mark.asyncio
async def test_no_claim_on_a_channel_drops_can_use():
    # RT1: a mineable channel is not idle watched, even if the loop has not noticed yet
    svc, tw, _ = setup()
    tw.can_watch.return_value = True
    await svc.process_points(1, claim_msg())
    await svc.process_points(1, earned("WATCH"))
    assert sent(tw) == []
    tw.gui.status.update.assert_not_called()


@pytest.mark.asyncio
async def test_unexpected_points_error_is_logged_like_process_drops(caplog):
    # RT5: process_points runs as a websocket callback task; task_wrapper logs the traceback
    # (then re-raises into that task, exactly as for process_drops)
    svc, _, _ = setup()
    svc._points_earned = AsyncMock(side_effect=RuntimeError("boom"))
    with caplog.at_level(logging.ERROR, logger="TwitchDrops"), pytest.raises(RuntimeError):
        await svc.process_points(1, earned("WATCH"))
    assert [r.getMessage() for r in caplog.records] == ["Exception in process_points task"]


@pytest.mark.asyncio
async def test_raw_message_logged_once_at_debug_without_balance(caplog):
    svc, _, _ = setup()
    with caplog.at_level(logging.DEBUG, logger="TwitchDrops"):
        await svc.process_points(1, earned("WATCH"))
        await svc.process_points(1, earned("WATCH"))
    raw = [r for r in caplog.records if r.levelno == logging.DEBUG and "points-earned" in r.getMessage()]
    assert len(raw) == 1
    assert "1510" not in raw[0].getMessage() and "WATCH" in raw[0].getMessage()


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
    # FR6: both message types normalise the channel id the same way
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
