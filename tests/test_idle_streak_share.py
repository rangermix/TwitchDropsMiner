"""Idle watch: sharing watch streaks."""

import asyncio
import json
import logging
import pathlib
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import aiohttp
import pytest

from src.api.gql_client import GQLClient
from src.api.http_client import HTTPClient
from src.auth.session_bundle import SessionError
from src.config import ClientType, State
from src.exceptions import GQLException, MinerException, RequestException
from src.services import idle_watch as idle_mod
from src.services.idle_watch import (
    STREAK_SHARE_ATTEMPTS,
    STREAK_SHARE_CHECK_SECONDS,
    IdleWatchService,
)


def milestone(mid="m1", *, category="WATCH_STREAK", state="ACTIVE", share="CAN_SHARE", value="9"):
    return {"id": mid, "category": category, "value": value, "state": state, "shareStatus": share}


def read(rows, channel_id="123"):
    return {"data": {"user": {"channel": {"id": channel_id, "self": {"viewerMilestones": rows}}}}}


def share_ok(mid="m1"):
    return {"data": {"shareViewerMilestone": {"milestone": {"id": mid}, "error": None}}}


SHARE_OK = share_ok()
REFUSED = {"data": {"shareViewerMilestone": {"milestone": None, "error": {"code": "NOT_ALLOWED"}}}}


def make_gql(reads, shares=None):
    """Answers a batch of reads with `reads` (one per login) and a share with the next of `shares`."""
    shares = list(shares or [SHARE_OK])

    async def gql(op):
        if isinstance(op, list):
            assert not any(getattr(o, "single_attempt", False) for o in op)  # reads may be resent
            return reads
        assert op.single_attempt is True  # a share is never resent by the client
        assert "shareViewerMilestone" in op["query"]
        item = shares.pop(0) if len(shares) > 1 else shares[0]
        if isinstance(item, Exception):
            raise item
        # SHARE_OK answers with the milestone the share asked for
        return share_ok(op["variables"]["input"]["milestoneID"]) if item is SHARE_OK else item

    return AsyncMock(side_effect=gql)


def setup(rows=None, *, share=True, active=True, gql=None, online=True, visits_path=None):
    channel = SimpleNamespace(id=123, name="A", login="a", online=online, banned=False)
    tw = SimpleNamespace(
        channels={123: channel},
        settings=SimpleNamespace(idle_channels=["a"], claim_channel_points=True, share_watch_streaks=share),
        gql_request=gql or make_gql([read(rows if rows is not None else [milestone()])]),
        gui=SimpleNamespace(status=MagicMock()),
        watching_channel=SimpleNamespace(get_with_default=lambda d: channel),
        can_watch=MagicMock(return_value=False),
        is_mining_time=MagicMock(return_value=True),
    )
    svc = IdleWatchService(tw, visits_path)
    svc._task = MagicMock(done=lambda: not active)
    svc.poke = MagicMock()
    return svc, tw


def shares(tw):
    return [c.args[0] for c in tw.gql_request.call_args_list if not isinstance(c.args[0], list)]


def reads(tw):
    return [c.args[0] for c in tw.gql_request.call_args_list if isinstance(c.args[0], list)]


@pytest.mark.asyncio
async def test_shares_a_can_share_streak_once(caplog):
    svc, tw = setup()
    with caplog.at_level(logging.INFO, logger="TwitchDrops"):
        await svc.share_streaks()
    [op] = shares(tw)
    assert op["variables"] == {"input": {"channelID": "123", "milestoneID": "m1"}}
    assert "shareViewerMilestone" in op["query"]
    [batch] = reads(tw)
    assert batch[0]["variables"] == {"login": "a"}
    assert "viewerMilestones" in batch[0]["query"]
    assert [r.getMessage() for r in caplog.records if r.levelno == logging.INFO] == [
        "Shared watch streak on A (9 streams)",
    ]
    # a later check, even with the id still reported as shareable, sends nothing
    svc._share_checked -= STREAK_SHARE_CHECK_SECONDS
    await svc.share_streaks()
    assert len(shares(tw)) == 1
    assert len(reads(tw)) == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("rows", [
    [milestone(share="CAN_NOT_SHARE")],
    [milestone(share="SHARED")],
    [milestone(state="SUSPENDED")],
    [milestone(category="SUBSCRIPTION_STREAK")],
    None,
    [],
    [{"category": "WATCH_STREAK", "state": "ACTIVE", "shareStatus": "CAN_SHARE"}],
    [{**milestone(), "id": ""}],
    [{**milestone(), "id": 5}],
    ["junk", None, 3, []],
    "junk",
])
async def test_no_share_for_other_rows(rows):
    svc, tw = setup()
    tw.gql_request = make_gql([read(rows)])
    await svc.share_streaks()
    assert shares(tw) == []


@pytest.mark.asyncio
@pytest.mark.parametrize("answer", [None, {}, {"data": None}, {"data": {"user": None}}, "x", {"errors": [{}], **read([milestone()])}])
async def test_malformed_answers_do_not_raise(answer):
    svc, tw = setup()
    tw.gql_request = make_gql([answer])
    await svc.share_streaks()
    assert shares(tw) == []


@pytest.mark.asyncio
async def test_refused_share_retries_once_then_stops(caplog):
    # an explicit error object means Twitch refused it: one more try, then never again
    svc, tw = setup(gql=make_gql([read([milestone()])], [REFUSED]))
    with caplog.at_level(logging.WARNING, logger="TwitchDrops"):
        for _ in range(3):
            await svc.share_streaks()
            svc._share_checked -= STREAK_SHARE_CHECK_SECONDS
    assert len(shares(tw)) == 2
    warnings = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
    assert warnings == ["Watch streak share on A was not accepted (NOT_ALLOWED)"] * 2


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", [
    MinerException("boom"), aiohttp.ClientError("boom"), TimeoutError("boom"), GQLException("boom"),
    {"errors": [{"message": "boom"}]},
], ids=repr)
async def test_unknown_outcome_uses_up_all_attempts(caplog, failure):
    # a share that may have been posted is never resent
    svc, tw = setup(gql=make_gql([read([milestone()])], [failure, SHARE_OK]))
    with caplog.at_level(logging.WARNING, logger="TwitchDrops"):
        for _ in range(3):
            await svc.share_streaks()
            svc._share_checked -= STREAK_SHARE_CHECK_SECONDS
    assert len(shares(tw)) == 1
    assert len(caplog.messages) == 1
    assert "boom" not in caplog.text


@pytest.mark.asyncio
async def test_refusal_then_success_marks_done(caplog):
    svc, tw = setup(gql=make_gql([read([milestone()])], [REFUSED, SHARE_OK]))
    with caplog.at_level(logging.INFO, logger="TwitchDrops"):
        for _ in range(3):
            await svc.share_streaks()
            svc._share_checked -= STREAK_SHARE_CHECK_SECONDS
    assert len(shares(tw)) == 2
    assert [r.getMessage() for r in caplog.records if r.levelno == logging.INFO] == ["Shared watch streak on A (9 streams)"]


@pytest.mark.asyncio
async def test_setting_off_or_not_active_or_not_live_sends_nothing():
    for kwargs in ({"share": False}, {"active": False}, {"online": False}):
        svc, tw = setup(**kwargs)
        await svc.share_streaks()
        assert tw.gql_request.call_count == 0, kwargs


@pytest.mark.asyncio
async def test_cadence_and_early_check():
    svc, tw = setup()
    await svc.share_streaks()
    await svc.share_streaks()
    assert len(reads(tw)) == 1
    # a WATCH_STREAK points-earned for a listed channel asks for an early check and wakes the loop
    await svc.process_points(1, {"type": "points-earned", "data": {
        "channel_id": "123", "point_gain": {"reason_code": "WATCH_STREAK", "total_points": 450},
        "balance": {"balance": 10}}})
    assert svc._share_now is True
    svc.poke.assert_called()
    assert len(reads(tw)) == 1  # the handler does no network work itself
    await svc.share_streaks()
    assert len(reads(tw)) == 2
    assert svc._share_now is False
    # other reasons and unlisted channels do not ask for one
    await svc.process_points(1, {"type": "points-earned", "data": {
        "channel_id": "123", "point_gain": {"reason_code": "WATCH", "total_points": 10}, "balance": {"balance": 10}}})
    await svc.process_points(1, {"type": "points-earned", "data": {
        "channel_id": "999", "point_gain": {"reason_code": "WATCH_STREAK", "total_points": 10}, "balance": {"balance": 10}}})
    assert svc._share_now is False
    # the interval alone also triggers a check
    svc._share_checked = time.monotonic() - STREAK_SHARE_CHECK_SECONDS - 1
    await svc.share_streaks()
    assert len(reads(tw)) == 3


@pytest.mark.asyncio
async def test_tick_runs_the_check_only_while_the_loop_is_active():
    svc, tw = setup(active=False)
    svc.track = AsyncMock()
    svc.pick = MagicMock(return_value=None)
    tw.stop_watching = MagicMock()
    await svc._tick()
    assert tw.gql_request.call_count == 0
    svc._task = MagicMock(done=lambda: False)
    await svc._tick()
    assert len(reads(tw)) == 1
    assert len(shares(tw)) == 1


def add_channel(tw, cid=456, login="b"):
    tw.channels[cid] = SimpleNamespace(id=cid, name=login.upper(), login=login, online=True, banned=False)
    tw.settings.idle_channels.append(login)


def real_client(monkeypatch, answer):
    """A real GQLClient whose transport is the browser path, answering each request with answer(op)."""
    monkeypatch.setattr("src.api.gql_client.asyncio.sleep", AsyncMock())
    browser = SimpleNamespace(gql=AsyncMock(side_effect=answer))
    auth = SimpleNamespace(validate=AsyncMock(return_value=SimpleNamespace(
        browser_active=True, _twitch=SimpleNamespace(_browser=browser),
    )))
    return GQLClient(MagicMock(), auth, ClientType.WEB), browser


def sent_shares(browser):
    return [c.args[0] for c in browser.gql.call_args_list if not isinstance(c.args[0], list)]


@pytest.mark.asyncio
@pytest.mark.parametrize("message", ["service timeout", "service unavailable", "context deadline exceeded", "service error"])
async def test_one_share_attempt_sends_the_mutation_once(monkeypatch, caplog, message):
    # a retryable error reply on a share is a failed attempt, never a resend
    replies = [{"errors": [{"message": message}]}] * 2 + [SHARE_OK]

    async def answer(op, single_attempt=False):
        return [read([milestone()])] if isinstance(op, list) else replies.pop(0)

    client, browser = real_client(monkeypatch, answer)
    svc, _ = setup(gql=client.request)
    with caplog.at_level(logging.WARNING, logger="TwitchDrops"):
        await svc.share_streaks()
    assert len(sent_shares(browser)) == 1
    assert caplog.messages == ["Twitch request ShareViewerMilestone failed (GQLException)"]
    # the outcome is unknown, so later checks do not send it again
    for _ in range(2):
        svc._share_checked -= STREAK_SHARE_CHECK_SECONDS
        await svc.share_streaks()
    assert len(sent_shares(browser)) == 1


@pytest.mark.asyncio
async def test_read_answer_for_another_channel_is_skipped():
    # answers are matched to channels by the channel id they carry, not by position alone
    svc, tw = setup(gql=make_gql([read([milestone("m1")], "999"), read([milestone("m2")], "456")]))
    add_channel(tw)
    await svc.share_streaks()
    assert [op["variables"]["input"] for op in shares(tw)] == [{"channelID": "456", "milestoneID": "m2"}]


def _stop_loop(svc, tw, ch):
    svc._task = MagicMock(done=lambda: True)


def _setting_off(svc, tw, ch):
    tw.settings.share_watch_streaks = False


def _unlisted(svc, tw, ch):
    tw.settings.idle_channels.remove(ch.login)


def _drops_channel(svc, tw, ch):
    tw.can_watch.side_effect = lambda c: c is ch


def _offline(svc, tw, ch):
    ch.online = False


def _banned(svc, tw, ch):
    ch.banned = True


def _untracked(svc, tw, ch):
    del tw.channels[ch.id]


FLIPS = [_stop_loop, _setting_off, _unlisted, _drops_channel, _offline, _banned, _untracked]


@pytest.mark.asyncio
@pytest.mark.parametrize("flip", FLIPS)
async def test_guards_are_checked_again_before_each_share(flip):
    # state can change while the read is in flight
    svc, tw = setup()
    inner = make_gql([read([milestone()])])

    async def gql(op):
        if isinstance(op, list):
            flip(svc, tw, tw.channels[123])
        return await inner(op)

    tw.gql_request = AsyncMock(side_effect=gql)
    await svc.share_streaks()
    assert shares(tw) == []
    # a skipped share does not use up an attempt
    assert svc._share_tries == {}


@pytest.mark.asyncio
@pytest.mark.parametrize("flip", FLIPS)
async def test_state_change_during_a_share_stops_the_next_channel(flip):
    svc, tw = setup()
    add_channel(tw)
    second = tw.channels[456]
    inner = make_gql([read([milestone("m1")]), read([milestone("m2")], "456")])

    async def gql(op):
        result = await inner(op)
        if not isinstance(op, list):
            flip(svc, tw, second)
        return result

    tw.gql_request = AsyncMock(side_effect=gql)
    await svc.share_streaks()
    assert [op["variables"]["input"]["milestoneID"] for op in shares(tw)] == ["m1"]


@pytest.mark.asyncio
async def test_failed_batch_falls_back_to_one_read_per_channel(caplog):
    # one channel's unrecognised error must not hide the other channels' streaks
    async def gql(op):
        if isinstance(op, list):
            if len(op) > 1 or op[0]["variables"]["login"] == "a":
                raise GQLException("bad entry")
            return [read([milestone("m2")], "456")]
        return share_ok("m2")

    svc, tw = setup(gql=AsyncMock(side_effect=gql))
    add_channel(tw)
    with caplog.at_level(logging.WARNING, logger="TwitchDrops"):
        await svc.share_streaks()
    assert [len(ops) for ops in reads(tw)] == [2, 1, 1]
    assert [op["variables"]["input"] for op in shares(tw)] == [{"channelID": "456", "milestoneID": "m2"}]
    assert caplog.messages == ["Twitch request ViewerMilestones failed (GQLException)"]
    assert "bad entry" not in caplog.text


@pytest.mark.asyncio
@pytest.mark.parametrize("error", [GQLException("x"), MinerException("x"), aiohttp.ClientError("x"), TimeoutError()])
@pytest.mark.parametrize("two", [False, True])
async def test_no_fallback_for_a_single_read_or_a_transport_failure(error, two):
    svc, tw = setup(gql=AsyncMock(side_effect=error))
    if two:
        add_channel(tw)
    await svc.share_streaks()
    assert tw.gql_request.call_count == (3 if two and isinstance(error, GQLException) else 1)
    assert shares(tw) == []


@pytest.mark.asyncio
async def test_raw_ops_carry_their_operation_name():
    # the name is sent and matches the one in the query text
    svc, tw = setup()
    await svc.share_streaks()
    [[read_op]] = reads(tw)
    [share_op] = shares(tw)
    assert read_op["operationName"] == "ViewerMilestones"
    assert "query ViewerMilestones(" in read_op["query"]
    assert share_op["operationName"] == "ShareViewerMilestone"
    assert "mutation ShareViewerMilestone(" in share_op["query"]


@pytest.mark.asyncio
async def test_request_warnings_use_a_neutral_prefix(caplog):
    svc, _ = setup(gql=make_gql([read([milestone()])], [{"errors": [{"message": "x"}]}]))
    with caplog.at_level(logging.WARNING, logger="TwitchDrops"):
        await svc.share_streaks()
    assert caplog.messages == ["Twitch request ShareViewerMilestone returned errors"]


@pytest.mark.asyncio
async def test_a_higher_value_of_the_same_streak_is_shared_again():
    # Twitch may keep the id while the count rises
    current = [read([milestone(value="9")])]
    svc, tw = setup(gql=make_gql(current))
    await svc.share_streaks()
    current[0] = read([milestone(value="10")])
    for _ in range(2):
        svc._share_checked -= STREAK_SHARE_CHECK_SECONDS
        await svc.share_streaks()
    assert [op["variables"]["input"]["milestoneID"] for op in shares(tw)] == ["m1", "m1"]


@pytest.mark.asyncio
async def test_refusals_of_one_value_do_not_block_the_next_value():
    current = [read([milestone(value="9")])]
    svc, tw = setup(gql=make_gql(current, [REFUSED, REFUSED, SHARE_OK]))
    for _ in range(3):
        await svc.share_streaks()
        svc._share_checked -= STREAK_SHARE_CHECK_SECONDS
    assert len(shares(tw)) == 2
    current[0] = read([milestone(value="10")])
    await svc.share_streaks()
    assert len(shares(tw)) == 3


@pytest.mark.asyncio
async def test_early_check_request_waits_for_a_live_channel():
    # a request that finds no live channel is kept for the next tick
    svc, tw = setup(online=False)
    svc._share_checked = time.monotonic()
    svc._share_now = True
    await svc.share_streaks()
    assert svc._share_now is True
    tw.channels[123].online = True
    await svc.share_streaks()
    assert svc._share_now is False
    assert len(reads(tw)) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("value", [None, ["9"], {"n": 9}, True])
async def test_share_without_a_usable_value_logs_no_count(caplog, value):
    row = milestone()
    if value is None:
        del row["value"]
    else:
        row["value"] = value
    svc, tw = setup(rows=[row])
    with caplog.at_level(logging.INFO, logger="TwitchDrops"):
        await svc.share_streaks()
    assert len(shares(tw)) == 1
    assert caplog.messages == ["Shared watch streak on A"]


@pytest.mark.asyncio
async def test_share_with_a_5xx_through_the_real_http_and_gql_clients_is_sent_once(monkeypatch, caplog):
    # a 5xx may come after Twitch applied the share; neither the clients nor a later check resend it
    monkeypatch.setattr("src.api.http_client.asyncio.sleep", AsyncMock())
    monkeypatch.setattr("src.api.gql_client.asyncio.sleep", AsyncMock())

    def response(status, payload=None):
        return SimpleNamespace(status=status, read=AsyncMock(return_value=b"{}"),
                               json=AsyncMock(return_value=payload), release=MagicMock())

    async def send(method, url, **kwargs):
        if isinstance(kwargs["json"], list):
            return response(200, [read([milestone()])])
        return response(503)

    session = SimpleNamespace(timeout=aiohttp.ClientTimeout(total=1), request=AsyncMock(side_effect=send))
    http = HTTPClient(SimpleNamespace(proxy=""), MagicMock(), SimpleNamespace(_state=State.IDLE), ClientType.WEB)
    http.get_session = AsyncMock(return_value=session)
    auth = SimpleNamespace(validate=AsyncMock(return_value=SimpleNamespace(browser_active=False, headers=lambda **_: {})))
    svc, _ = setup(gql=GQLClient(http, auth, ClientType.WEB).request)
    with caplog.at_level(logging.WARNING, logger="TwitchDrops"):
        for _ in range(3):
            await svc.share_streaks()
            svc._share_checked -= STREAK_SHARE_CHECK_SECONDS
    posts = [c.kwargs["json"] for c in session.request.call_args_list if not isinstance(c.kwargs["json"], list)]
    assert len(posts) == 1
    assert caplog.messages == ["Twitch request ShareViewerMilestone failed (RequestException)"]


@pytest.mark.asyncio
async def test_share_hanging_past_the_check_timeout_is_not_resent(monkeypatch, caplog):
    # the 30 s wait cancels the share mid-request; its outcome is unknown
    monkeypatch.setattr("src.services.idle_watch.STREAK_SHARE_TIMEOUT", 0.05)
    hang = asyncio.Event()

    async def gql(op):
        if isinstance(op, list):
            return [read([milestone()])]
        await hang.wait()

    svc, tw = setup(gql=AsyncMock(side_effect=gql))
    with caplog.at_level(logging.WARNING, logger="TwitchDrops"):
        await asyncio.wait_for(svc.share_streaks(), 2)
    assert caplog.messages == ["Watch streak share check timed out"]
    assert svc._share_tries == {("m1", 9): STREAK_SHARE_ATTEMPTS}
    svc._share_checked -= STREAK_SHARE_CHECK_SECONDS
    await asyncio.wait_for(svc.share_streaks(), 2)
    assert len(shares(tw)) == 1
    assert len(reads(tw)) == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("reply", [
    {"data": {"shareViewerMilestone": {"milestone": None, "error": None}}},
    {"data": {"shareViewerMilestone": {"error": None}}},
    {"data": {"shareViewerMilestone": {}}},
    {"data": {"shareViewerMilestone": {"milestone": {"id": "other"}, "error": None}}},
    {"data": {"shareViewerMilestone": {"milestone": {}, "error": None}}},
    {"data": {"shareViewerMilestone": None}},
    {"data": {"shareViewerMilestone": {"milestone": {"id": "m1"}, "error": "odd"}}},
    {"data": {"shareViewerMilestone": {"milestone": None, "error": {}}}},
    {"data": {"shareViewerMilestone": {"milestone": None, "error": {"code": None}}}},
    {"data": {"shareViewerMilestone": {"milestone": None, "error": {"code": ""}}}},
    {"data": {"shareViewerMilestone": {"milestone": None, "error": {"code": 5}}}},
    {"data": {"shareViewerMilestone": {"milestone": {"id": "m1"}, "error": {"code": "X"}}}},
    {"data": {"shareViewerMilestone": {"milestone": {}, "error": {"code": "X"}}}},
    # an error without the milestone field is malformed, not a refusal
    {"data": {"shareViewerMilestone": {"error": {"code": "X"}}}},
    {"data": None},
    {},
], ids=repr)
async def test_unexpected_share_reply_warns_and_is_not_resent(caplog, reply):
    # only the milestone coming back counts as a confirmed share
    svc, tw = setup(gql=make_gql([read([milestone()])], [reply]))
    with caplog.at_level(logging.INFO, logger="TwitchDrops"):
        for _ in range(2):
            await svc.share_streaks()
            svc._share_checked -= STREAK_SHARE_CHECK_SECONDS
    assert len(shares(tw)) == 1
    assert [(r.levelno, r.getMessage()) for r in caplog.records] == [
        (logging.WARNING, "Watch streak share on A returned an unexpected reply"),
    ]


@pytest.mark.asyncio
async def test_one_share_per_channel_per_check():
    # two shareable milestones on one channel go out in two checks, never as a burst
    svc, tw = setup(rows=[milestone("m1"), milestone("m2")])
    add_channel(tw)
    tw.gql_request = make_gql([read([milestone("m1"), milestone("m2")]), read([milestone("m3")], "456")])
    await svc.share_streaks()
    sent = [(op["variables"]["input"]["channelID"], op["variables"]["input"]["milestoneID"]) for op in shares(tw)]
    assert sent == [("123", "m1"), ("456", "m3")]
    svc._share_checked -= STREAK_SHARE_CHECK_SECONDS
    await svc.share_streaks()
    sent = [(op["variables"]["input"]["channelID"], op["variables"]["input"]["milestoneID"]) for op in shares(tw)]
    assert sent == [("123", "m1"), ("456", "m3"), ("123", "m2")]


@pytest.mark.asyncio
async def test_a_skipped_milestone_does_not_use_the_channels_share():
    # a milestone already shared is passed over, so the next one on the channel goes out in the same check
    svc, tw = setup(rows=[milestone("m1"), milestone("m2")])
    svc._share_tries[("m1", 9)] = STREAK_SHARE_ATTEMPTS
    await svc.share_streaks()
    assert [op["variables"]["input"]["milestoneID"] for op in shares(tw)] == ["m2"]


@pytest.mark.asyncio
async def test_int_and_string_values_are_one_key():
    # Twitch sends value as a string; an int of the same count must not share again
    current = [read([milestone(value=9)])]
    svc, tw = setup(gql=make_gql(current))
    await svc.share_streaks()
    current[0] = read([milestone(value="9")])
    svc._share_checked -= STREAK_SHARE_CHECK_SECONDS
    await svc.share_streaks()
    assert len(shares(tw)) == 1
    assert svc._share_tries == {("m1", 9): STREAK_SHARE_ATTEMPTS}


@pytest.mark.asyncio
async def test_a_list_reply_to_a_single_share_is_rejected(caplog):
    # only a batch may answer with a list
    svc, tw = setup(gql=make_gql([read([milestone()])], [[SHARE_OK]]))
    with caplog.at_level(logging.INFO, logger="TwitchDrops"):
        await svc.share_streaks()
    assert caplog.messages == ["Twitch request ShareViewerMilestone returned errors"]
    assert len(shares(tw)) == 1

@pytest.mark.asyncio
async def test_refused_share_through_the_gql_client_still_gets_one_more_try(caplog):
    # the refusal is nested in data; the single_attempt reply check only rejects top-level errors
    replies = [[read([milestone()])], REFUSED, [read([milestone()])], share_ok()]
    browser = SimpleNamespace(gql=AsyncMock(side_effect=replies))
    auth = SimpleNamespace(validate=AsyncMock(return_value=SimpleNamespace(
        browser_active=True, _twitch=SimpleNamespace(_browser=browser),
    )))
    svc, _ = setup(gql=GQLClient(MagicMock(), auth, ClientType.WEB).request)
    with caplog.at_level(logging.WARNING, logger="TwitchDrops"):
        await svc.share_streaks()
        assert svc._share_tries == {("m1", 9): 1}
        svc._share_checked -= STREAK_SHARE_CHECK_SECONDS
        await svc.share_streaks()
    assert caplog.messages == ["Watch streak share on A was not accepted (NOT_ALLOWED)"]
    assert svc._share_tries[("m1", 9)] == STREAK_SHARE_ATTEMPTS


@pytest.mark.asyncio
@pytest.mark.parametrize("error", [
    RequestException("Unconfirmed HTTP response"),
    GQLException("Mutation response was not confirmed"),
    SessionError("REQUEST"),
], ids=lambda e: type(e).__name__)
async def test_share_failure_raised_by_single_attempt_is_a_warning_not_a_crash(caplog, error):
    svc, tw = setup(gql=make_gql([read([milestone()])], [error]))
    with caplog.at_level(logging.WARNING, logger="TwitchDrops"):
        await svc.share_streaks()
    assert caplog.messages == [f"Twitch request ShareViewerMilestone failed ({type(error).__name__})"]
    assert svc._share_tries == {("m1", 9): STREAK_SHARE_ATTEMPTS}


@pytest.mark.asyncio
async def test_no_share_while_paused_or_outside_mining_hours():
    # can_watch is False then too, so the drops check alone would let the share through
    svc, tw = setup()
    tw.is_mining_time.return_value = False
    await svc.share_streaks()
    assert shares(tw) == []


@pytest.mark.parametrize("raw,value", [
    (9, 9), ("9", 9), (" 9", 9), ("9 ", 9), ("09", 9), (9.0, 9), ("0", 0),
    (None, None), (True, None), (9.5, None), (float("nan"), None), (float("inf"), None),
    ("+9", None), ("-1", None), ("9_0", None), ("\u0669", None), ("9.0", None), ("", None), ("x", None),
    (["9"], None), ({"n": 9}, None),
])
def test_streak_value_is_one_canonical_count(raw, value):
    assert IdleWatchService._streak_value(raw) == value


@pytest.mark.asyncio
@pytest.mark.parametrize("first,second", [
    ("9", 9.0), (9.0, "9"), ("9", " 9"), (" 9", "9"), ("09", 9), (9, "09"),
    ("9", None), (None, "9"), ("9", "x"), ("x", "9"), (None, None),
], ids=repr)
async def test_one_milestone_in_two_spellings_is_shared_once(first, second):
    # a missing or unusable value matches the id at any value, in either order
    def row(value):
        r = milestone()
        if value is None:
            del r["value"]
        else:
            r["value"] = value
        return r

    current = [read([row(first)])]
    svc, tw = setup(gql=make_gql(current))
    await svc.share_streaks()
    current[0] = read([row(second)])
    svc._share_checked -= STREAK_SHARE_CHECK_SECONDS
    await svc.share_streaks()
    assert len(shares(tw)) == 1


@pytest.mark.asyncio
async def test_a_refusal_of_a_missing_value_does_not_block_the_count():
    # only a used-up key blocks the id; one refusal leaves the next try open
    row = milestone()
    del row["value"]
    current = [read([row])]
    svc, tw = setup(gql=make_gql(current, [REFUSED, SHARE_OK]))
    await svc.share_streaks()
    current[0] = read([milestone(value="9")])
    svc._share_checked -= STREAK_SHARE_CHECK_SECONDS
    await svc.share_streaks()
    assert len(shares(tw)) == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("restart", [True, False], ids=["restart", "reset"])
async def test_unknown_outcome_share_is_not_resent_after_a_restart_or_reset(tmp_path, restart):
    # a timed-out share may have been posted; the next run reads the milestone as shareable again
    path = tmp_path / "idle_visits.json"
    gql = make_gql([read([milestone()])], [TimeoutError()])
    svc, tw = setup(gql=gql, visits_path=path)
    await svc.share_streaks()
    if restart:
        svc, _ = setup(gql=gql, visits_path=path)
    else:
        svc.reset()
    await svc.share_streaks()
    assert len(shares(tw)) == 1
    assert len(reads(tw)) == 2


@pytest.mark.asyncio
async def test_share_key_is_saved_before_the_share_is_sent(tmp_path):
    # a crash during the request must not lose the record of the share
    path = tmp_path / "idle_visits.json"
    on_disk = []

    async def gql(op):
        if isinstance(op, list):
            return [read([milestone()])]
        on_disk.append(json.loads(path.read_text(encoding="utf-8"))["shares"])
        return share_ok()

    svc, _ = setup(gql=AsyncMock(side_effect=gql), visits_path=path)
    await svc.share_streaks()
    assert on_disk == [[["m1", 9]]]


@pytest.mark.asyncio
async def test_a_refused_share_gets_exactly_one_more_try_across_restarts(tmp_path):
    path = tmp_path / "idle_visits.json"
    gql = make_gql([read([milestone()])], [REFUSED])
    svc, tw = setup(gql=gql, visits_path=path)
    await svc.share_streaks()
    assert json.loads(path.read_text(encoding="utf-8"))["shares"] == [["m1", 9, 1]]  # its try count is kept
    for _ in range(3):
        svc, _ = setup(gql=gql, visits_path=path)
        await svc.share_streaks()
    assert len(shares(tw)) == 2
    assert json.loads(path.read_text(encoding="utf-8"))["shares"] == [["m1", 9]]


@pytest.mark.asyncio
async def test_an_old_file_without_try_counts_still_blocks_its_shares(tmp_path):
    path = tmp_path / "idle_visits.json"
    path.write_text('{"user_id": null, "visits": {}, "shares": [["m1", 9]]}', encoding="utf-8")
    svc, tw = setup(visits_path=path)
    await svc.share_streaks()
    assert shares(tw) == []


def test_saved_share_keys_are_capped_oldest_first(tmp_path):
    path = tmp_path / "idle_visits.json"
    svc, _ = setup(visits_path=path)
    for n in range(idle_mod.IDLE_SHARES_KEPT + 5):
        svc._share_tries[(f"m{n}", n)] = STREAK_SHARE_ATTEMPTS
    svc._share_tries[("refused", 1)] = 1
    svc._save()
    saved = json.loads(path.read_text(encoding="utf-8"))["shares"]
    assert len(saved) == idle_mod.IDLE_SHARES_KEPT
    # a refused key is kept with its try count and takes a slot like a used one
    assert saved[0] == ["m6", 6] and saved[-1] == ["refused", 1, 1]


@pytest.mark.asyncio
async def test_share_is_skipped_when_its_key_cannot_be_saved(tmp_path, monkeypatch, caplog):
    # an unrecorded share could be sent again after a restart: it is not sent at all
    real_save = idle_mod.json_save
    failing = [True]

    def save(*args, **kwargs):
        if failing[0]:
            raise PermissionError("secret path")
        real_save(*args, **kwargs)

    monkeypatch.setattr(idle_mod, "json_save", save)
    path = tmp_path / "idle_visits.json"
    svc, tw = setup(visits_path=path)
    with caplog.at_level(logging.WARNING, logger="TwitchDrops"):
        await svc.share_streaks()
    assert shares(tw) == []
    assert caplog.messages == [
        "Idle visits could not be saved (PermissionError)",
        "Watch streak share on A skipped: it could not be recorded",
    ]
    assert "secret path" not in caplog.text
    failing[0] = False  # the disk works again: the next check sends it once
    svc._share_checked -= STREAK_SHARE_CHECK_SECONDS
    await svc.share_streaks()
    svc._share_checked -= STREAK_SHARE_CHECK_SECONDS
    await svc.share_streaks()
    assert len(shares(tw)) == 1
    assert json.loads(path.read_text(encoding="utf-8"))["shares"] == [["m1", 9]]


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["permission", "truncated", "wrong shape", "no visits key"])
async def test_unreadable_visits_file_blocks_shares_and_writes_for_the_run(tmp_path, monkeypatch, caplog, failure):
    # the file may hold used share keys the service cannot see: sending could post a share twice,
    # and writing would replace the keys
    path = tmp_path / "idle_visits.json"
    good = '{"user_id": 5, "visits": {"1": 123}, "shares": [["m1", 9]]}'
    path.write_text({"truncated": good[:-3], "wrong shape": "[1, 2]", "no visits key": good.replace('"visits": {"1": 123}, ', "")}.get(failure, good), encoding="utf-8")
    before = path.read_bytes()
    if failure == "permission":
        def denied(self, *args, **kwargs):
            raise PermissionError("secret path")
        monkeypatch.setattr(pathlib.Path, "read_text", denied)
    with caplog.at_level(logging.WARNING, logger="TwitchDrops"):
        svc, tw = setup(visits_path=path)
        svc._use_account(5)
        for _ in range(3):
            await svc.share_streaks()  # every call is due: a blocked check never runs
        svc.visited[2] = 123
        assert svc._save() is False
    assert shares(tw) == []
    assert path.read_bytes() == before
    assert len(caplog.messages) == 1
    assert caplog.messages[0].startswith("Idle visits file unreadable (")
    assert "fix or delete" in caplog.messages[0]
    assert "secret path" not in caplog.text and "m1" not in caplog.text


@pytest.mark.asyncio
@pytest.mark.parametrize("entry", [
    '["m", "9"]', '["m", 9.0]', '["m", true]', '["", 9]', '[1, 9]', '["m"]', '{"m": 9}', 'null',
    '["m", 9, 0]', '["m", 9, 2]', '["m", 9, "1"]', '["m", 9, true]', '["m", 9, 1, 1]',
])
async def test_a_damaged_share_entry_keeps_visits_but_blocks_shares_and_writes(tmp_path, caplog, entry):
    # the entry's share history is unknown: a share it recorded could go out again
    path = tmp_path / "idle_visits.json"
    path.write_text('{"user_id": 5, "visits": {"1": 123}, "shares": [["ok", 3], ' + entry + "]}", encoding="utf-8")
    before = path.read_bytes()
    with caplog.at_level(logging.WARNING, logger="TwitchDrops"):
        svc, tw = setup(rows=[milestone("m")], visits_path=path)
        svc._use_account(5)
        assert svc.visited == {1: 123}
        await svc.share_streaks()
        svc.visited[2] = 123
        assert svc._save() is False
    assert shares(tw) == []
    assert path.read_bytes() == before
    assert len(caplog.messages) == 1
    assert caplog.messages[0].startswith("Idle visits file unreadable (damaged share entry)")


@pytest.mark.asyncio
@pytest.mark.parametrize("keys", ['[["m1", 9], ["m1", 9, 1]]', '[["m1", 9, 1], ["m1", 9]]'], ids=["used first", "refused first"])
async def test_a_repeated_share_key_keeps_its_highest_try_count(tmp_path, keys):
    # a hand-edited or merged file: the used-up entry wins, so the share is not sent again
    path = tmp_path / "idle_visits.json"
    path.write_text('{"user_id": null, "visits": {}, "shares": ' + keys + "}", encoding="utf-8")
    svc, tw = setup(visits_path=path)
    assert svc._share_tries == {("m1", 9): STREAK_SHARE_ATTEMPTS}
    await svc.share_streaks()
    assert shares(tw) == []
