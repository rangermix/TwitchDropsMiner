"""Claiming a reward lane reselects once and keeps ambiguous mutations unresolved."""

import asyncio
from datetime import timedelta
from unittest.mock import AsyncMock

import pytest

from src.models.reward_campaign import RewardCampaign, RewardLedger
from tests.test_reward_campaigns import _data, _group, _iso, _twitch


READY = {"status": "CLAIMABLE", "currentMinutesWatched": 20, "grantCount": 0}


def build(twitch, ledger, readings, window=None):
    return RewardCampaign.from_reward_campaign(
        twitch, _data([_group("first", 20), _group("second", 20)]),
        readings, "uid", {"first": window} if window else {}, ledger=ledger,
    )


@pytest.mark.asyncio
async def test_claim_reselects_next_lane_without_repeating_a_confirmed_mutation():
    twitch, ledger = _twitch(), RewardLedger()
    twitch.gql_request = AsyncMock(return_value={"data": {"claimDropRewards": {
        "status": "ELIGIBLE_FOR_ALL",
    }}})
    readings = {"first": READY}
    first = build(twitch, ledger, readings).timed_drops["first"]
    assert await first.claim()
    twitch.request_inventory_refresh.assert_called_once_with()
    assert list(build(twitch, ledger, readings).timed_drops) == ["second"]
    # A transient omission followed by the stale reply still refers to the confirmed claim.
    build(twitch, ledger, {})
    assert list(build(twitch, ledger, readings).timed_drops) == ["second"]
    assert await first.claim()
    twitch.gql_request.assert_awaited_once()
    twitch.request_inventory_refresh.assert_called_once_with()


@pytest.mark.asyncio
async def test_uncertain_claim_is_not_replayed_and_a_new_watch_cycle_can_claim():
    twitch, ledger = _twitch(), RewardLedger()
    twitch.gql_request = AsyncMock(side_effect=[
        {"data": {"claimDropRewards": {}}},
        {"data": {"claimDropRewards": {"status": "ELIGIBLE_FOR_ALL"}}},
    ])
    readings = {"first": READY}
    first = build(twitch, ledger, readings).timed_drops["first"]
    assert not await first.claim() and not first.is_claimed
    assert not await first.claim()
    assert list(build(twitch, ledger, readings).timed_drops) == ["second"]
    twitch.request_inventory_refresh.assert_not_called()
    twitch.gql_request.assert_awaited_once()
    build(twitch, ledger, {"first": {"status": "IN_PROGRESS", "currentMinutesWatched": 3}})
    assert await build(twitch, ledger, readings).timed_drops["first"].claim()
    assert twitch.gql_request.await_count == 2


@pytest.mark.asyncio
async def test_missing_window_hints_preserve_claim_and_next_window_can_claim_again():
    twitch, ledger = _twitch(), RewardLedger()
    twitch.gql_request = AsyncMock(return_value={"data": {"claimDropRewards": {
        "status": "ELIGIBLE_FOR_ALL",
    }}})
    readings = {"first": READY}
    assert await build(twitch, ledger, readings, _iso(-timedelta(hours=1))).timed_drops["first"].claim()
    assert list(build(twitch, ledger, readings).timed_drops) == ["second"]
    assert await build(twitch, ledger, readings, _iso(timedelta(hours=23))).timed_drops["first"].claim()
    assert twitch.gql_request.await_count == 2


@pytest.mark.asyncio
async def test_cancelled_inflight_claim_stays_unconfirmed_and_is_not_replayed():
    twitch, ledger = _twitch(), RewardLedger()
    sent = asyncio.Event()
    never_replied = asyncio.Event()

    async def send(_op):
        sent.set()
        await never_replied.wait()

    twitch.gql_request = AsyncMock(side_effect=send)
    readings = {"first": READY}
    first = build(twitch, ledger, readings).timed_drops["first"]
    original = asyncio.create_task(first.claim())
    await sent.wait()
    # A refresh arriving during the mutation cannot open a second concurrent claim.
    build(twitch, ledger, {"first": {"status": "IN_PROGRESS", "currentMinutesWatched": 2}})
    assert list(build(twitch, ledger, readings).timed_drops) == ["second"]
    assert not await first.claim()
    original.cancel()
    with pytest.raises(asyncio.CancelledError):
        await original
    assert not first.is_claimed
    assert list(build(twitch, ledger, readings).timed_drops) == ["second"]
    twitch.gql_request.assert_awaited_once()
    twitch.request_inventory_refresh.assert_not_called()
