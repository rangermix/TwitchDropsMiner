"""Watch-based sitewide Open Reward Campaigns are mined through the regular drop model."""

import asyncio
import json
import re
from contextlib import suppress
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.config import GQL_OPERATIONS, MAX_EXTRA_MINUTES, State
from src.config.reward_priority import RewardPriority
from src.config.settings import default_settings
from src.exceptions import ExitRequest, GQLException
from src.models import DropsCampaign, RewardCampaign
from src.models.channel import Channel, Stream
from src.models.game import Game
from src.models.reward_campaign import RewardLedger
from src.services.inventory_service import InventoryService
from src.services.stream_selector import StreamSelector
from src.services.watch_service import WatchService
from src.utils.async_helpers import AwaitableValue
from src.web.managers.inventory import InventoryManager
from tests.test_priority_fallback import _settings
from tests.test_watched_channel_recovery import OFFLINE, _reloading_miner


GTA = {"id": "1867279146", "displayName": "Grand Theft Auto VI", "slug": "grand-theft-auto-vi"}
OTHER = {"id": "7", "name": "Other Game"}
CAMPAIGN_ID = "b7669f9e-c69c-4a9b-a3b2-36266c1bdfc0"
NO_WINDOWS: dict = {}
GROUP_ID = "2283245e-d15c-11f1-8cdd-0a58a9feac02"
CLAIM_ID = f"uid#{CAMPAIGN_ID}#{GROUP_ID}"
NOW = datetime.now(timezone.utc)


def _iso(delta):
    return (NOW + delta).strftime("%Y-%m-%dT%H:%M:%SZ")


def _group(group_id=GROUP_ID, goal=60, categories=(GTA,), subs=0):
    return {
        "id": group_id,
        "unlockRequirements": {"minuteWatchedGoal": goal, "subsGoal": subs},
        "categories": list(categories),
        "rewards": [{"id": "r1", "name": "GTA DVD Chest", "thumbnailImage": None}],
    }


def _data(groups=None, sitewide=True):
    return {
        "id": CAMPAIGN_ID,
        "name": "GTAVI Launch Celebration",
        "status": "ACTIVE",
        "startsAt": _iso(-timedelta(days=1)),
        "endsAt": _iso(timedelta(days=10)),
        "isSitewide": sitewide,
        "rewardGroups": [_group()] if groups is None else groups,
    }


def _twitch(**settings):
    settings.setdefault("mining_benefits", default_settings["mining_benefits"].copy())
    twitch = SimpleNamespace(
        settings=SimpleNamespace(
            allow_unlinked_campaigns=False, drop_name_blacklist=[], telegram_bot_token="",
            discord_webhook_url="", **settings,
        ),
        gui=MagicMock(),
        request_inventory_refresh=MagicMock(),
        request_games_update=MagicMock(),
        get_auth=AsyncMock(return_value=SimpleNamespace(user_id="uid")),
        gql_request=AsyncMock(),
        print=MagicMock(),
        drop_history=MagicMock(),
        _campaigns={},
        change_state=MagicMock(),
    )
    twitch.gui.broadcast_wanted_items_now = AsyncMock()
    return twitch


def _build(self_data=None, *, data=None, twitch=None):
    progress = {} if self_data is None else {GROUP_ID: self_data}
    return RewardCampaign.from_reward_campaign(twitch or _twitch(), data or _data(), progress, "uid")


def _drop(campaign):
    return campaign.timed_drops[GROUP_ID]


def _channel(twitch, game=GTA, *, drops_enabled=False, channel_id=1):
    channel = Channel(twitch, id=channel_id, login=f"c{channel_id}")
    channel._stream = Stream(
        channel, id=channel_id, game={"id": game["id"], "name": game.get("displayName") or game["name"]},
        viewers=10, title="Live",
    )
    channel._stream.drops_enabled = drops_enabled
    return channel


def _regular(twitch, game=OTHER):
    return DropsCampaign(twitch, {
        "id": "regular", "name": "Regular", "game": game, "status": "ACTIVE",
        "self": {"isAccountConnected": True}, "accountLinkURL": "https://link",
        "startAt": _iso(-timedelta(days=1)), "endAt": _iso(timedelta(days=1)),
        "allow": {"channels": None},
        "timeBasedDrops": [{
            "id": "d1", "name": "d1", "benefitEdges": [{"benefit": {
                "id": "b1", "name": "Item", "distributionType": "DIRECT_ENTITLEMENT", "imageAssetURL": None,
            }}], "startAt": _iso(-timedelta(days=1)),
            "endAt": _iso(timedelta(days=1)), "preconditionDrops": None,
            "requiredMinutesWatched": 30, "self": {
                "dropInstanceID": None, "isClaimed": False, "currentMinutesWatched": 10,
            },
        }],
    }, {})


def _progress_response(self_data):
    return {"data": {"currentUser": {"inventory": {"viewerRewardDropCampaignsInProgress": [
        {"id": CAMPAIGN_ID, "rewardGroups": [{"id": GROUP_ID, "self": self_data}]},
    ]}}}}


def _gql(progress, *, campaigns=None, windows=NO_WINDOWS, badges=(), completed=()):
    """Answer by operation: raw reward queries and the persisted ClaimDrop."""
    async def request(op):
        if isinstance(op, list):
            return [await request(single) for single in op]
        if "operationName" in op:
            assert op["operationName"] == "DropsPage_ClaimDropRewards"
            return {"data": {"claimDropRewards": {"status": "ELIGIBLE_FOR_ALL"}}}
        if "RewardCampaignProgress" in op["query"]:
            return progress
        if "RewardCampaignHints" in op["query"]:
            if windows is None:
                raise GQLException("unknown field currentWindow")
            return {"data": {"currentUser": {"inventory": {"viewerRewardDropCampaignsInProgress": [
                {"rewardGroups": [
                    {"id": g, "self": {"currentWindow": {"expiresAt": end}},
                     "rewards": [{"id": r, "dropDetail": {"badge": {"setID": "s"}}} for r in badges]}
                    for g, end in windows.items()
                ]},
            ]}}}}
        if "CompletedRewardCampaigns" in op["query"]:
            if completed is None:
                raise GQLException("unknown field completedRewardCampaigns")
            return _completed_response([{"id": c, "status": "COMPLETED"} for c in completed])
        return campaigns if campaigns is not None else {"data": {"rewardCampaignsAvailableToUser": [_data()]}}
    return AsyncMock(side_effect=request)


def _completed_response(entries):
    return {"data": {"currentUser": {"inventory": {"completedRewardCampaigns": entries}}}}


def _claims(twitch):
    return [c for c in twitch.gql_request.await_args_list if "operationName" in c.args[0]]


# 1
def test_unstarted_group_becomes_one_earnable_drop():
    twitch = _twitch()
    campaign = _build(twitch=twitch)
    drop = _drop(campaign)
    assert campaign.id == CAMPAIGN_ID and isinstance(campaign, RewardCampaign)
    assert campaign.game == Game(GTA)
    assert list(campaign.timed_drops) == [GROUP_ID]
    assert (drop.current_minutes, drop.required_minutes) == (0, 60)
    assert campaign.eligible and not twitch.settings.allow_unlinked_campaigns
    assert campaign.can_earn(_channel(twitch))
    assert drop.claim_id is None and not drop.can_claim


# 2
def test_in_progress_minutes():
    drop = _drop(_build({"status": "IN_PROGRESS", "currentMinutesWatched": 12, "nextEligibleAt": None}))
    assert drop.current_minutes == 12 and not drop.is_claimed


# 3
def test_claimable_gets_synthetic_claim_id():
    drop = _drop(_build({"status": "CLAIMABLE", "currentMinutesWatched": 60}))
    assert drop.claim_id == CLAIM_ID and drop.can_claim


# 4
def test_claimed_with_future_window_waits():
    twitch = _twitch()
    next_at = NOW + timedelta(hours=5)
    campaign = _build({"status": "CLAIMED", "nextEligibleAt": _iso(timedelta(hours=5))}, twitch=twitch)
    drop = _drop(campaign)
    assert not campaign.can_earn(_channel(twitch)) and not drop.is_claimed
    assert drop.starts_at == next_at.replace(microsecond=0)
    assert campaign.can_earn_within(next_at + timedelta(minutes=1))
    assert drop.starts_at in campaign.time_triggers
    assert drop.current_minutes == 0


# 5
def test_claimed_with_past_window_is_earnable():
    twitch = _twitch()
    campaign = _build({"status": "CLAIMED", "nextEligibleAt": _iso(-timedelta(hours=1))}, twitch=twitch)
    assert campaign.can_earn(_channel(twitch)) and _drop(campaign).current_minutes == 0


# 6
@pytest.mark.parametrize("status", ["CLAIMED", "FULFILLED"])
def test_final_grant_without_next_window_is_finished(status):
    campaign = _build({"status": status, "nextEligibleAt": None})
    assert _drop(campaign).is_claimed and campaign.finished


# 7
@pytest.mark.parametrize("status", ["EXPIRED", "SOMETHING_NEW"])
def test_unknown_status_is_not_farmed(status):
    assert _build({"status": status}) is None


# 8
def test_group_selection():
    subs = _group("sub", goal=0, subs=1)
    assert _build(data=_data([subs])) is None
    assert _build(data=_data([_group("empty", categories=())])) is None
    mixed = _build(data=_data([subs, _group()]))
    assert list(mixed.timed_drops) == [GROUP_ID]


@pytest.mark.parametrize("subs", [None, 0])
def test_group_without_a_subscription_goal_is_mined(subs):
    group = _group(subs=subs)
    if subs is None:
        del group["unlockRequirements"]["subsGoal"]
    assert list(_build(data=_data([group])).timed_drops) == [GROUP_ID]


def test_group_with_watch_and_subscription_goal_is_skipped():
    both = _group("both", goal=60, subs=1)
    assert _build(data=_data([both])) is None
    picked = _build(data=_data([both, _group()]))
    assert list(picked.timed_drops) == [GROUP_ID]


def test_group_without_rewards_is_skipped():
    # A drop without benefits is never mineable, so it would show up in Inventory but never earn.
    no_rewards = {**_group(), "rewards": []}
    assert _build(data=_data([no_rewards])) is None
    assert list(_build(data=_data([no_rewards, _group("second")])).timed_drops) == ["second"]


@pytest.mark.parametrize("progress", [
    {"status": "CLAIMED", "nextEligibleAt": "not a date"},
    {"status": "CLAIMED", "nextEligibleAt": 12345},
    {"status": "CLAIMED", "nextEligibleAt": ["x"]},
])
def test_unreadable_next_window_is_not_farmed(progress):
    assert _build(progress) is None


def test_naive_next_window_is_utc():
    naive = (NOW + timedelta(hours=2)).strftime("%Y-%m-%dT%H:%M:%S")
    drop = _drop(_build({"status": "CLAIMED", "nextEligibleAt": naive}))
    assert drop.starts_at == (NOW + timedelta(hours=2)).replace(microsecond=0)


@pytest.mark.parametrize(("minutes", "expected"), [("12", 0), (None, 0), (True, 0), (-5, 0), (999, 60), (7.5, 0)])
def test_minutes_are_validated_and_clamped(minutes, expected):
    drop = _drop(_build({"status": "IN_PROGRESS", "currentMinutesWatched": minutes}))
    assert drop.current_minutes == expected


def test_next_window_after_campaign_end_means_finished():
    campaign = _build({"status": "CLAIMED", "nextEligibleAt": _iso(timedelta(days=11))})
    assert _drop(campaign).is_claimed and campaign.finished


# 9
def test_can_watch_without_drops_flag_only_for_reward_category():
    twitch = _twitch()
    campaign = _build(twitch=twitch)
    regular = _regular(twitch)
    twitch.inventory = [campaign]
    twitch.wanted_games = [campaign.game]
    service = WatchService(twitch)
    assert service.can_watch(_channel(twitch))
    assert not service.can_watch(_channel(twitch, OTHER))
    offline = _channel(twitch)
    offline._stream = None
    assert not service.can_watch(offline)
    banned = _channel(twitch)
    banned.update_ban_status({"data": {"user": {"id": "1", "self": {"banStatus": {"isPermanent": True}}}}})
    assert not service.can_watch(banned)
    twitch.inventory, twitch.wanted_games = [regular], [regular.game]
    assert not service.can_watch(_channel(twitch, OTHER))
    assert service.can_watch(_channel(twitch, OTHER, drops_enabled=True))


# 10
@pytest.mark.asyncio
async def test_fetch_reward_campaigns_builds_sitewide_only():
    twitch = _twitch()
    twitch.gql_request = _gql(_progress_response({"status": "IN_PROGRESS", "currentMinutesWatched": 5}), campaigns={
        "data": {"rewardCampaignsAvailableToUser": [_data(), {**_data(sitewide=False), "id": "game"}]},
    })
    built = await InventoryService(twitch).fetch_reward_campaigns()
    assert [c.id for c in built] == [CAMPAIGN_ID]
    assert _drop(built[0]).current_minutes == 5


@pytest.mark.asyncio
async def test_fetch_reward_campaigns_null_progress_means_not_started():
    twitch = _twitch()
    twitch.gql_request = _gql({"data": {"currentUser": {"inventory": {"viewerRewardDropCampaignsInProgress": None}}}})
    built = await InventoryService(twitch).fetch_reward_campaigns()
    assert _drop(built[0]).current_minutes == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("error", [GQLException("x"), KeyError("data")])
async def test_fetch_reward_campaigns_failure_is_isolated(error, caplog):
    twitch = _twitch()
    twitch.gql_request = AsyncMock(side_effect=error)
    with caplog.at_level("WARNING", logger="TwitchDrops"):
        assert await InventoryService(twitch).fetch_reward_campaigns() == []
    assert "regular drops are unaffected" in caplog.text


@pytest.mark.asyncio
async def test_fetch_reward_campaigns_propagates_exit():
    twitch = _twitch()
    twitch.gql_request = AsyncMock(side_effect=ExitRequest())
    with pytest.raises(ExitRequest):
        await InventoryService(twitch).fetch_reward_campaigns()


# 11
@pytest.mark.asyncio
async def test_refresh_updates_minutes_and_resets_extra():
    twitch = _twitch()
    campaign = _build(twitch=twitch)
    twitch._campaigns[campaign.id] = campaign
    drop = _drop(campaign)
    drop.extra_current_minutes = 3
    twitch.gql_request = _gql(_progress_response({"status": "IN_PROGRESS", "currentMinutesWatched": 20}))
    assert await InventoryService(twitch).refresh_reward_progress([campaign], _channel(twitch)) is True
    assert (drop.real_current_minutes, drop.extra_current_minutes) == (20, 0)
    assert twitch.gql_request.await_count == 1


@pytest.mark.asyncio
async def test_refresh_claimable_reloads_inventory_instead_of_claiming():
    twitch = _twitch()
    campaign = _build(twitch=twitch)
    twitch._campaigns[campaign.id] = campaign
    twitch.gql_request = _gql(_progress_response({"status": "CLAIMABLE", "currentMinutesWatched": 60}))
    assert await InventoryService(twitch).refresh_reward_progress([campaign], _channel(twitch)) is True
    twitch.request_inventory_refresh.assert_called_once()
    assert not _claims(twitch) and not _drop(campaign).is_claimed
    # The rebuilt campaign carries the synthetic claim id for the regular claim loop.
    assert _drop(_build({"status": "CLAIMABLE", "currentMinutesWatched": 60})).claim_id == CLAIM_ID


@pytest.mark.asyncio
async def test_claim_id_uses_the_in_progress_campaign_id():
    other_id = "in-progress-campaign"
    twitch = _twitch()
    response = _progress_response({"status": "CLAIMABLE", "currentMinutesWatched": 60})
    response["data"]["currentUser"]["inventory"]["viewerRewardDropCampaignsInProgress"][0]["id"] = other_id
    twitch.gql_request = _gql(response, campaigns={"data": {"rewardCampaignsAvailableToUser": [_data()]}})
    [campaign] = await InventoryService(twitch).fetch_reward_campaigns()
    assert _drop(campaign).claim_id == f"uid#{other_id}#{GROUP_ID}"


def test_claim_id_falls_back_to_the_event_id_without_an_in_progress_id():
    progress = {GROUP_ID: {"status": "CLAIMABLE", "currentMinutesWatched": 60}}
    for ids in (None, {}, {"other-group": "x"}):
        campaign = RewardCampaign.from_reward_campaign(_twitch(), _data(), progress, "uid", None, ids)
        assert _drop(campaign).claim_id == CLAIM_ID


@pytest.mark.asyncio
async def test_refresh_without_observation_only_bumps_the_estimate():
    twitch = _twitch()
    campaign = _build({"status": "IN_PROGRESS", "currentMinutesWatched": 9}, twitch=twitch)
    twitch._campaigns[campaign.id] = campaign
    twitch.gql_request = _gql({"data": {"currentUser": {"inventory": {"viewerRewardDropCampaignsInProgress": []}}}})
    assert await InventoryService(twitch).refresh_reward_progress([campaign], _channel(twitch)) is True
    assert (_drop(campaign).real_current_minutes, _drop(campaign).extra_current_minutes) == (9, 1)
    twitch.request_inventory_refresh.assert_not_called()


@pytest.mark.asyncio
async def test_refresh_finished_window_requests_inventory_reload():
    twitch = _twitch()
    campaign = _build(twitch=twitch)
    twitch._campaigns[campaign.id] = campaign
    twitch.gql_request = _gql(_progress_response(
        {"status": "CLAIMED", "nextEligibleAt": _iso(timedelta(hours=3))}))
    assert await InventoryService(twitch).refresh_reward_progress([campaign], _channel(twitch)) is True
    twitch.request_inventory_refresh.assert_called_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("error", [GQLException("x"), KeyError("data"), TimeoutError()])
async def test_refresh_failure_returns_false(error):
    twitch = _twitch()
    campaign = _build(twitch=twitch)
    twitch._campaigns[campaign.id] = campaign
    twitch.gql_request = AsyncMock(side_effect=error)
    assert await InventoryService(twitch).refresh_reward_progress([campaign], _channel(twitch)) is False


@pytest.mark.asyncio
async def test_refresh_apply_failure_is_contained_and_exit_propagates():
    twitch = _twitch()
    campaign = _build(twitch=twitch)
    twitch._campaigns[campaign.id] = campaign
    twitch.gql_request = _gql(_progress_response({"status": "IN_PROGRESS", "currentMinutesWatched": 5}))
    service = InventoryService(twitch)
    with patch.object(RewardCampaign, "apply_reward_progress", side_effect=ValueError("x")):
        assert await service.refresh_reward_progress([campaign], _channel(twitch)) is True
    with (
        patch.object(RewardCampaign, "apply_reward_progress", side_effect=ExitRequest()),
        pytest.raises(ExitRequest),
    ):
        await service.refresh_reward_progress([campaign], _channel(twitch))


@pytest.mark.asyncio
async def test_a_failing_campaign_does_not_make_applied_ones_fall_back():
    twitch = _twitch()
    first, second = _build({"status": "IN_PROGRESS", "currentMinutesWatched": 5}, twitch=twitch), _build(twitch=twitch)
    second.id = "second"
    for campaign in (first, second):
        twitch._campaigns[campaign.id] = campaign
    twitch.gql_request = _gql(_progress_response({"status": "IN_PROGRESS", "currentMinutesWatched": 9}))
    real = RewardCampaign.apply_reward_progress

    def apply(self, progress, channel):
        if self is second:
            raise ValueError("x")
        real(self, progress, channel)

    with patch.object(RewardCampaign, "apply_reward_progress", apply):
        assert await InventoryService(twitch).refresh_reward_progress([first, second], _channel(twitch)) is True
    assert (_drop(first).real_current_minutes, _drop(first).extra_current_minutes) == (9, 0)
    assert _drop(second).extra_current_minutes == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("stale", ["revision", "replaced"])
async def test_refresh_discards_stale_reply_per_campaign(stale):
    twitch = _twitch()
    campaign = _build(twitch=twitch)
    twitch._campaigns[campaign.id] = campaign
    drop = _drop(campaign)

    async def request(op):
        if stale == "revision":
            campaign.progress_revision += 1
        else:
            twitch._campaigns[campaign.id] = object()
        return _progress_response({"status": "IN_PROGRESS", "currentMinutesWatched": 33})

    twitch.gql_request = AsyncMock(side_effect=request)
    assert await InventoryService(twitch).refresh_reward_progress([campaign], _channel(twitch)) is True
    assert drop.current_minutes == 0


@pytest.mark.asyncio
async def test_fetch_reward_campaigns_isolates_a_broken_campaign(caplog):
    twitch = _twitch()
    broken = {"id": "broken", "isSitewide": True}
    twitch.gql_request = _gql(_progress_response(None), campaigns={
        "data": {"rewardCampaignsAvailableToUser": [broken, {"id": "no flag"}, _data()]},
    })
    with caplog.at_level("WARNING", logger="TwitchDrops"):
        built = await InventoryService(twitch).fetch_reward_campaigns()
    assert [c.id for c in built] == [CAMPAIGN_ID]
    assert "broken" in caplog.text


@pytest.mark.asyncio
async def test_fetch_reward_campaigns_is_time_bounded(monkeypatch, caplog):
    twitch = _twitch()

    async def hang(op):
        await asyncio.Event().wait()

    twitch.gql_request = AsyncMock(side_effect=hang)
    real_wait_for = asyncio.wait_for
    monkeypatch.setattr(
        "src.services.inventory_service.asyncio.wait_for",
        lambda awaitable, timeout: real_wait_for(awaitable, 0.05),
    )
    with caplog.at_level("WARNING", logger="TwitchDrops"):
        assert await InventoryService(twitch).fetch_reward_campaigns() == []
    assert "regular drops are unaffected" in caplog.text


# 12: the real InventoryService picks the active campaign, as in the miner
def _loop_miner(channel_flag, *, progress_error=None, wanted=True):
    twitch = _twitch()
    reward = _build({"status": "IN_PROGRESS", "currentMinutesWatched": 5}, twitch=twitch)
    regular = _regular(twitch, GTA)
    twitch.inventory = [regular, reward]
    twitch.wanted_games = [reward.game] if wanted else [Game(OTHER)]
    twitch._campaigns = {c.id: c for c in twitch.inventory}
    twitch._drops = {d.id: d for c in twitch.inventory for d in c.drops}
    twitch.is_mining_time = lambda: True
    channel = _channel(twitch, drops_enabled=channel_flag)
    twitch.watching_channel = AwaitableValue()
    twitch.watching_channel.set(channel)
    twitch._inventory_service = InventoryService(twitch)
    ops = []

    async def request(op):
        ops.append(op)
        if "query" in op:
            if progress_error is not None:
                raise progress_error
            return _progress_response({"status": "IN_PROGRESS", "currentMinutesWatched": 20})
        return {"data": {"currentUser": {"dropCurrentSession": None}}}

    twitch.gql_request = request
    return twitch, regular, reward, ops


async def _run_loop(twitch):
    clock = [0.0]
    service = WatchService(twitch)

    async def advance(delay):
        clock[0] += delay
        if clock[0] >= 40:
            raise asyncio.CancelledError

    with (
        patch("src.services.watch_service.monotonic", side_effect=lambda: clock[0]),
        patch.object(service, "watch_sleep", side_effect=advance),
        patch.object(Channel, "send_watch", AsyncMock(return_value=True)),
        pytest.raises(asyncio.CancelledError),
    ):
        await service.watch_loop()


def _current_drop_queries(ops):
    return [op for op in ops if op.get("operationName") == GQL_OPERATIONS["CurrentDrop"]["operationName"]]


@pytest.mark.asyncio
async def test_flagged_channel_polls_reward_and_still_queries_current_drop_for_regular():
    twitch, regular, reward, ops = _loop_miner(True)
    await _run_loop(twitch)
    assert _drop(reward).real_current_minutes == 20
    assert _current_drop_queries(ops)


@pytest.mark.asyncio
async def test_flagless_channel_never_progresses_regular_campaign_of_the_same_game():
    twitch, regular, reward, ops = _loop_miner(False)
    await _run_loop(twitch)
    assert _drop(reward).real_current_minutes == 20
    assert not _current_drop_queries(ops)
    assert regular.timed_drops["d1"].extra_current_minutes == 0


@pytest.mark.asyncio
async def test_reward_refresh_failure_falls_back_to_current_drop_and_bump():
    twitch, regular, reward, ops = _loop_miner(False, progress_error=GQLException("x"))
    await _run_loop(twitch)
    assert _current_drop_queries(ops)
    assert _drop(reward).extra_current_minutes >= 1
    assert regular.timed_drops["d1"].extra_current_minutes == 0


def test_active_campaign_requires_drops_flag_and_can_skip_rewards():
    twitch, regular, reward, _ops = _loop_miner(True)
    service = twitch._inventory_service
    assert service.get_active_campaign() in (regular, reward)
    assert service.get_active_campaign(exclude_rewards=True) is regular
    twitch.watching_channel.get_with_default(None)._stream.drops_enabled = False
    assert service.get_active_campaign() is reward
    assert service.get_active_campaign(exclude_rewards=True) is None


# 13
def _channels_fetch_client(tmp_path, monkeypatch, campaigns):
    client, _watched = _reloading_miner(tmp_path, monkeypatch, discovered=[2], stream_status=[OFFLINE])
    game = Game(GTA)
    client.wanted_games = [game]
    client.inventory = [
        (MagicMock(spec=RewardCampaign) if reward else MagicMock(spec=DropsCampaign)) for reward in campaigns
    ]
    for campaign, reward in zip(client.inventory, campaigns, strict=True):
        campaign.configure_mock(
            game=game, can_earn_within=lambda stamp: True, allowed_channels=[],
            can_earn=lambda channel: True, can_progress_on=lambda channel, reward=reward: reward or channel.drops_enabled,
        )
    calls = []

    async def live_streams(game, *, drops_enabled=True, **_):
        calls.append(drops_enabled)
        return [_channel(client, drops_enabled=drops_enabled, channel_id=2)]

    client.get_live_streams = live_streams
    return client, calls


async def _run_client(client):
    try:
        await asyncio.wait_for(client._run(), 1)
    finally:
        if client._watching_task is not None:
            client._watching_task.cancel()


@pytest.mark.asyncio
@pytest.mark.parametrize(("campaigns", "expected"), [
    ([False], [True]),
    ([False, True], [True, False]),
])
async def test_channels_fetch_adds_flagless_channels_after_drops_enabled_ones(
    tmp_path, monkeypatch, campaigns, expected,
):
    client, calls = _channels_fetch_client(tmp_path, monkeypatch, campaigns)
    await _run_client(client)
    assert calls == expected
    # The drops-enabled Channel object found first is the one that is kept.
    assert client.channels[2].drops_enabled is True


# Dashboard
@pytest.mark.asyncio
async def test_dashboard_serializes_reward_campaign_without_images_or_link():
    campaign = _build({"status": "IN_PROGRESS", "currentMinutesWatched": 12})
    manager = InventoryManager(MagicMock(), MagicMock())
    serialized = manager._serialize_campaign(campaign)
    json.dumps(serialized)
    assert serialized["link_url"] == "" and serialized["game_name"] == "Grand Theft Auto VI"
    assert serialized["drops"][0]["benefits"] == [
        {"name": "GTA DVD Chest", "type": "UNKNOWN", "image_url": None},
    ]
    assert serialized["drops"][0]["current_minutes"] == 12


@pytest.mark.asyncio
async def test_claim_reply_without_status_is_unconfirmed():
    twitch = _twitch()
    drop = _drop(_build({"status": "CLAIMABLE", "currentMinutesWatched": 60}, twitch=twitch))
    twitch.gql_request = AsyncMock(return_value={"data": {"claimDropRewards": {"dropInstanceID": CLAIM_ID}}})
    assert await drop.claim() is False
    assert not drop.is_claimed


# Second review round
def test_claimable_requests_reload_only_until_the_drop_has_a_claim_id():
    twitch = _twitch()
    channel = _channel(twitch)
    ready = _build({"status": "CLAIMABLE", "currentMinutesWatched": 60}, twitch=twitch)
    for _ in range(3):
        ready.apply_reward_progress({"status": "CLAIMABLE"}, channel)
    twitch.request_inventory_refresh.assert_not_called()
    stale = _build(twitch=twitch)
    stale.apply_reward_progress({"status": "CLAIMABLE"}, channel)
    twitch.request_inventory_refresh.assert_called_once()


def test_stalled_reward_progress_ends_with_a_channel_switch():
    twitch = _twitch()
    campaign = _build(twitch=twitch)
    channel = _channel(twitch)
    for _ in range(MAX_EXTRA_MINUTES):
        assert campaign.can_earn(channel)
        campaign.apply_reward_progress(None, channel)
    assert not campaign.can_earn(channel)
    twitch.change_state.assert_called_with(State.CHANNEL_SWITCH)


def test_progress_without_increase_bumps_and_an_increase_resets_extra():
    twitch = _twitch()
    campaign = _build({"status": "IN_PROGRESS", "currentMinutesWatched": 5}, twitch=twitch)
    drop, channel = _drop(campaign), _channel(twitch)
    campaign.apply_reward_progress({"status": "IN_PROGRESS", "currentMinutesWatched": 5}, channel)
    campaign.apply_reward_progress({"status": "IN_PROGRESS", "currentMinutesWatched": 5}, channel)
    assert (drop.real_current_minutes, drop.extra_current_minutes) == (5, 2)
    campaign.apply_reward_progress({"status": "IN_PROGRESS", "currentMinutesWatched": 8}, channel)
    assert (drop.real_current_minutes, drop.extra_current_minutes) == (8, 0)


@pytest.mark.asyncio
async def test_non_dict_payload_entries_are_contained():
    twitch = _twitch()
    campaign = _build(twitch=twitch)
    twitch._campaigns[campaign.id] = campaign
    odd = {"data": {"currentUser": {"inventory": {"viewerRewardDropCampaignsInProgress": [
        None, "x", {"rewardGroups": [None, {"id": GROUP_ID, "self": ["x"]}, {"id": "g", "self": 3}]},
    ]}}}}
    twitch.gql_request = _gql(odd, campaigns={
        "data": {"rewardCampaignsAvailableToUser": [None, ["x"], "y", _data()]},
    })
    service = InventoryService(twitch)
    assert await service._fetch_reward_progress() == ({}, {})
    assert await service.refresh_reward_progress([campaign], _channel(twitch)) is True
    assert [c.id for c in await service.fetch_reward_campaigns()] == [CAMPAIGN_ID]


@pytest.mark.asyncio
async def test_attribute_errors_are_contained():
    twitch = _twitch()
    campaign = _build(twitch=twitch)
    twitch._campaigns[campaign.id] = campaign
    twitch.gql_request = AsyncMock(side_effect=AttributeError("x"))
    service = InventoryService(twitch)
    assert await service.fetch_reward_campaigns() == []
    assert await service.refresh_reward_progress([campaign], _channel(twitch)) is False


def test_window_that_cannot_finish_before_campaign_end_is_finished():
    ends = datetime.strptime(_data()["endsAt"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    late = (ends - timedelta(seconds=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
    campaign = _build({"status": "CLAIMED", "nextEligibleAt": late})
    assert _drop(campaign).is_claimed and campaign.finished
    early = (ends - timedelta(minutes=61)).strftime("%Y-%m-%dT%H:%M:%SZ")
    assert not _drop(_build({"status": "CLAIMED", "nextEligibleAt": early})).is_claimed


@pytest.mark.asyncio
async def test_channels_fetch_marks_a_kept_channel_of_the_same_broadcast_drops_enabled(tmp_path, monkeypatch):
    client, calls = _channels_fetch_client(tmp_path, monkeypatch, [False])
    kept = _channel(client, drops_enabled=False, channel_id=2)
    client.channels[2] = kept
    await _run_client(client)
    assert client.channels[2] is kept and kept.drops_enabled is True


@pytest.mark.asyncio
async def test_channels_fetch_keeps_flag_of_a_new_broadcast(tmp_path, monkeypatch):
    client, calls = _channels_fetch_client(tmp_path, monkeypatch, [False])
    kept = _channel(client, drops_enabled=False, channel_id=2)
    kept._stream.broadcast_id = 999
    client.channels[2] = kept
    with pytest.raises(TimeoutError):  # the replaced stream ends the scripted run early
        await _run_client(client)
    assert kept.drops_enabled is False


@pytest.mark.asyncio
async def test_unwanted_reward_game_is_not_polled():
    twitch, regular, reward, ops = _loop_miner(True, wanted=False)
    await _run_loop(twitch)
    assert not [op for op in ops if "query" in op]


def test_lower_reading_corrects_minutes_instead_of_bumping():
    twitch = _twitch()
    campaign = _build({"status": "IN_PROGRESS", "currentMinutesWatched": 30}, twitch=twitch)
    drop = _drop(campaign)
    campaign.apply_reward_progress({"status": "IN_PROGRESS", "currentMinutesWatched": 3}, _channel(twitch))
    assert (drop.real_current_minutes, drop.extra_current_minutes) == (3, 0)


def test_claim_that_keeps_failing_ends_with_a_channel_switch():
    twitch = _twitch()
    campaign = _build({"status": "CLAIMABLE", "currentMinutesWatched": 60}, twitch=twitch)
    channel = _channel(twitch)
    for _ in range(MAX_EXTRA_MINUTES):
        campaign.apply_reward_progress({"status": "CLAIMABLE"}, channel)
    assert not campaign.can_earn(channel)
    twitch.change_state.assert_called_with(State.CHANNEL_SWITCH)
    twitch.request_inventory_refresh.assert_not_called()


@pytest.mark.asyncio
async def test_channels_fetch_keeps_flag_of_a_kept_stream_in_another_category(tmp_path, monkeypatch):
    client, calls = _channels_fetch_client(tmp_path, monkeypatch, [False])
    kept = _channel(client, OTHER, drops_enabled=False, channel_id=2)
    client.channels[2] = kept
    with suppress(TimeoutError):  # a stream change may end the scripted run early
        await _run_client(client)
    assert kept.drops_enabled is False


# GTAVI follows the real snapshot (.dev-notes/reward-campaigns-research.md): one watch group
# for "GTA DVD Chest" plus 8 subscription-tier groups. The sub-tier IDs are synthetic.
DVD = GROUP_ID
SUBS = [f"sub-tier-{n}" for n in range(1, 9)]
POKEMON = {"id": "1001", "displayName": "Pokemon", "slug": "pokemon"}


def _gtavi():
    return _data([_group(DVD)] + [_group(sub, goal=0, subs=1) for sub in SUBS])


def _pokemon(lanes=3):
    # Synthetic: the research notes hold no Pokemon snapshot; only the several-watch-lanes
    # shape ("one counts once started") is taken from them.
    return _data([_group(f"lane{n}", goal=20, categories=(POKEMON,)) for n in range(lanes)])


def _pick(data, progress=None, windows=None):
    return RewardCampaign.from_reward_campaign(_twitch(), data, progress or {}, "uid", windows or {})


def test_started_group_is_picked():
    campaign = _pick(_gtavi(), {DVD: {"status": "IN_PROGRESS", "currentMinutesWatched": 54}})
    assert list(campaign.timed_drops) == [DVD] and _drop(campaign).current_minutes == 54


def test_without_progress_the_watch_goal_is_kept_and_sub_tiers_are_ignored():
    campaign = _pick(_gtavi())
    assert list(campaign.timed_drops) == [DVD]
    assert _pick(_gtavi(), {sub: {"status": "IN_PROGRESS", "currentMinutesWatched": 0} for sub in SUBS}).id == CAMPAIGN_ID
    assert list(_pick(_pokemon()).timed_drops) == ["lane0"]


def test_unstarted_first_lane_of_evolutions_is_mined():
    live = {"lane0": {"status": "IN_PROGRESS", "currentMinutesWatched": 0, "grantCount": 0, "nextEligibleAt": None}}
    assert list(_pick(_pokemon(), live).timed_drops) == ["lane0"]


def test_finished_last_lane_leaves_the_first_unstarted_lane():
    done = {"lane2": {"status": "FULFILLED", "nextEligibleAt": None}}
    assert list(_pick(_pokemon(), done).timed_drops) == ["lane0"]


def test_granted_daily_goal_alone_stays_as_the_finished_goal():
    campaign = _pick(_gtavi(), {DVD: {"status": "CLAIMED", "nextEligibleAt": None}})
    assert list(campaign.timed_drops) == [DVD] and _drop(campaign).is_claimed


def test_next_lane_is_picked_after_a_lane_is_finished():
    progress = {
        "lane0": {"status": "FULFILLED", "nextEligibleAt": None},
        "lane1": {"status": "IN_PROGRESS", "currentMinutesWatched": 2},
    }
    assert list(_pick(_pokemon(), progress).timed_drops) == ["lane1"]


def test_single_watch_group_works_alone():
    assert list(_pick(_data()).timed_drops) == [GROUP_ID]


# Live payload: Twitch grants at the goal and keeps IN_PROGRESS.
GRANTED = {"status": "IN_PROGRESS", "currentMinutesWatched": 60, "grantCount": 1, "nextEligibleAt": None}


def test_granted_goal_waits_for_the_window_to_end():
    end = NOW + timedelta(hours=5)
    campaign = _pick(_data(), {GROUP_ID: GRANTED}, {GROUP_ID: _iso(timedelta(hours=5))})
    drop = _drop(campaign)
    assert drop.starts_at == end.replace(microsecond=0)
    assert not drop.is_claimed and not campaign.can_earn(_channel(_twitch()))
    assert campaign.can_earn_within(end + timedelta(minutes=1))
    assert drop.current_minutes == 0


def test_granted_goal_with_a_past_window_is_earnable_again():
    twitch = _twitch()
    campaign = _pick(_data(), {GROUP_ID: GRANTED}, {GROUP_ID: _iso(-timedelta(hours=1))})
    assert campaign.can_earn(_channel(twitch)) and _drop(campaign).current_minutes == 0


@pytest.mark.parametrize("windows", [None, {}, {GROUP_ID: "not a date"}, {GROUP_ID: 5}])
def test_granted_goal_without_a_usable_window_counts_as_granted(windows):
    campaign = _pick(_data(), {GROUP_ID: GRANTED}, windows)
    assert _drop(campaign).is_claimed and not campaign.can_earn(_channel(_twitch()))


def test_granted_goal_whose_next_window_cannot_finish_is_finished():
    late = (datetime.strptime(_data()["endsAt"], "%Y-%m-%dT%H:%M:%SZ") - timedelta(seconds=1)).strftime(
        "%Y-%m-%dT%H:%M:%SZ")
    campaign = _pick(_data(), {GROUP_ID: GRANTED}, {GROUP_ID: late})
    assert _drop(campaign).is_claimed and campaign.finished


def test_tick_with_a_reached_goal_reloads_once_without_bumping():
    twitch = _twitch()
    campaign = _build({"status": "IN_PROGRESS", "currentMinutesWatched": 54}, twitch=twitch)
    campaign.apply_reward_progress(GRANTED, _channel(twitch))
    twitch.request_inventory_refresh.assert_called_once()
    assert (_drop(campaign).real_current_minutes, _drop(campaign).extra_current_minutes) == (54, 0)


@pytest.mark.asyncio
async def test_fetch_uses_the_window_hint_and_survives_its_rejection(monkeypatch):
    twitch = _twitch()
    campaigns = {"data": {"rewardCampaignsAvailableToUser": [_data()]}}
    progress = _progress_response(GRANTED)
    twitch.gql_request = _gql(progress, campaigns=campaigns, windows={GROUP_ID: _iso(timedelta(hours=3))})
    (waiting,) = await InventoryService(twitch).fetch_reward_campaigns()
    assert not _drop(waiting).is_claimed and _drop(waiting).starts_at > NOW

    twitch.gql_request = _gql(progress, campaigns=campaigns, windows=None)
    (rejected,) = await InventoryService(twitch).fetch_reward_campaigns()
    assert _drop(rejected).is_claimed

    plain = _gql(progress, campaigns=campaigns)

    async def slow(op):
        if "query" in op and "RewardCampaignHints" in op["query"]:
            await asyncio.sleep(10)
        return await plain(op)

    twitch.gql_request = slow
    real_wait_for = asyncio.wait_for
    monkeypatch.setattr(
        "src.services.inventory_service.asyncio.wait_for",
        lambda awaitable, timeout: real_wait_for(awaitable, {5: 0.05, 15: 3}[timeout]),
    )
    assert [c.id for c in await InventoryService(twitch).fetch_reward_campaigns()] == [CAMPAIGN_ID]


# Grant count shown in Inventory
@pytest.mark.parametrize(("count", "expected"), [
    (1, 1), (0, 0), (6, 6), (None, 0), (-1, 0), (True, 0), ("1", 0), (1.5, 0),
])
def test_grant_count_is_a_non_negative_int(count, expected):
    progress = {"status": "IN_PROGRESS", "currentMinutesWatched": 5, "grantCount": count}
    assert _build(progress).reward_grants == expected
    assert _build({"status": "IN_PROGRESS", "currentMinutesWatched": 5}).reward_grants == 0
    assert _build().reward_grants == 0


def test_tick_updates_the_grant_count_only_for_a_valid_reading():
    twitch = _twitch()
    campaign = _build({"status": "IN_PROGRESS", "currentMinutesWatched": 5, "grantCount": 1}, twitch=twitch)
    channel = _channel(twitch)
    campaign.apply_reward_progress({"status": "IN_PROGRESS", "currentMinutesWatched": 9, "grantCount": 2}, channel)
    assert campaign.reward_grants == 2
    for bad in (None, -3, "x", True):
        campaign.apply_reward_progress({"status": "IN_PROGRESS", "currentMinutesWatched": 12, "grantCount": bad}, channel)
    assert campaign.reward_grants == 2


def test_dashboard_reports_grants_for_reward_drops_only():
    manager = InventoryManager(MagicMock(), MagicMock())
    reward = _build({"status": "IN_PROGRESS", "currentMinutesWatched": 5, "grantCount": 1})
    assert manager._serialize_campaign(reward)["drops"][0]["reward_grants"] == 1
    regular = _regular(_twitch())
    assert manager._serialize_campaign(regular)["drops"][0]["reward_grants"] is None


# Reward type from the optional hints query
def _hints(groups):
    return {"data": {"currentUser": {"inventory": {"viewerRewardDropCampaignsInProgress": [
        {"rewardGroups": groups},
    ]}}}}


HINT_CASES = [
    # (label, group payload, expected windows, expected badge ids)
    ("badge and window", {"id": "g", "self": {"currentWindow": {"expiresAt": "T"}},
                          "rewards": [{"id": "r1", "dropDetail": {"badge": {"setID": "s"}}}]}, {"g": "T"}, {"r1"}),
    ("badge, currentWindow missing", {"id": "g", "self": {}, "rewards": [
        {"id": "r1", "dropDetail": {"badge": {"setID": "s"}}}]}, {}, {"r1"}),
    ("badge, self null", {"id": "g", "self": None, "rewards": [
        {"id": "r1", "dropDetail": {"badge": {"setID": "s"}}}]}, {}, {"r1"}),
    ("window, badge null", {"id": "g", "self": {"currentWindow": {"expiresAt": "T"}}, "rewards": [
        {"id": "r1", "dropDetail": {"badge": None}}]}, {"g": "T"}, set()),
    ("window, dropDetail null", {"id": "g", "self": {"currentWindow": {"expiresAt": "T"}}, "rewards": [
        {"id": "r1", "dropDetail": None}]}, {"g": "T"}, set()),
    ("window, dropDetail missing", {"id": "g", "self": {"currentWindow": {"expiresAt": "T"}}, "rewards": [
        {"id": "r1"}]}, {"g": "T"}, set()),
    ("window, rewards missing", {"id": "g", "self": {"currentWindow": {"expiresAt": "T"}}}, {"g": "T"}, set()),
    ("window, rewards null", {"id": "g", "self": {"currentWindow": {"expiresAt": "T"}}, "rewards": None},
     {"g": "T"}, set()),
    ("window without expiresAt, badge", {"id": "g", "self": {"currentWindow": {}}, "rewards": [
        {"id": "r1", "dropDetail": {"badge": {}}}]}, {}, {"r1"}),
    ("junk reward entries", {"id": "g", "self": {"currentWindow": {"expiresAt": "T"}}, "rewards": [
        None, "x", {"id": "r2", "dropDetail": {"badge": {"setID": "s"}}}]}, {"g": "T"}, {"r2"}),
]


@pytest.mark.parametrize(("label", "group", "windows", "badges"), HINT_CASES, ids=[c[0] for c in HINT_CASES])
def test_hints_parse_windows_and_badges_independently(label, group, windows, badges):
    assert InventoryService._parse_reward_hints(_hints([group])) == (windows, badges)


@pytest.mark.parametrize("response", [None, {}, {"data": None}, {"data": {"currentUser": {"inventory": None}}},
                                      {"data": {"currentUser": {"inventory": {"viewerRewardDropCampaignsInProgress": 5}}}}])
@pytest.mark.asyncio
async def test_unusable_hints_response_returns_empty_hints(response):
    twitch = _twitch()
    twitch.gql_request = AsyncMock(return_value=response)
    assert await InventoryService(twitch)._fetch_reward_hints() == ({}, set())


def _types(campaign):
    return [benefit.type.name for benefit in _drop(campaign).benefits]


def test_reward_type_is_badge_only_when_twitch_reports_one():
    progress = {GROUP_ID: {"status": "IN_PROGRESS", "currentMinutesWatched": 5}}

    def build(badges):
        return RewardCampaign.from_reward_campaign(_twitch(), _data(), progress, "uid", None, None, badges)

    assert _types(build({"r1"})) == ["BADGE"]
    assert _types(build({"other-reward"})) == _types(build(set())) == _types(build(None)) == ["UNKNOWN"]
    # Before the event starts there is no in-progress entry and so no hint.
    assert _types(_build()) == ["UNKNOWN"]


def test_badge_reward_campaign_is_still_mined_and_follows_the_reward_type_filter():
    twitch = _twitch()
    campaign = RewardCampaign.from_reward_campaign(
        twitch, _data(), {GROUP_ID: {"status": "IN_PROGRESS", "currentMinutesWatched": 5}}, "uid", None, None, {"r1"}
    )
    assert campaign.can_earn(_channel(twitch)) and _drop(campaign).can_earn()
    assert campaign.has_badge_or_emote and campaign.skips_drops_flag
    assert campaign.has_wanted_unclaimed_benefits({"BADGE": True, "UNKNOWN": False})
    assert not campaign.has_wanted_unclaimed_benefits({"BADGE": False, "UNKNOWN": True})
    # A claimable badge needs no account link (reward campaigns are always linked).
    ready = RewardCampaign.from_reward_campaign(
        twitch, _data(), {GROUP_ID: {"status": "CLAIMABLE"}}, "uid", None, None, {"r1"}
    )
    assert _drop(ready).can_claim


@pytest.mark.asyncio
async def test_loading_marks_badge_rewards_from_the_hints_query():
    twitch = _twitch()
    twitch.gql_request = _gql(
        _progress_response({"status": "IN_PROGRESS", "currentMinutesWatched": 5}),
        windows={GROUP_ID: _iso(timedelta(hours=3))}, badges=("r1",),
    )
    [campaign] = await InventoryService(twitch).fetch_reward_campaigns()
    assert _types(campaign) == ["BADGE"]
    twitch.gql_request = _gql(_progress_response({"status": "IN_PROGRESS", "currentMinutesWatched": 5}), windows=None)
    [campaign] = await InventoryService(twitch).fetch_reward_campaigns()
    assert _types(campaign) == ["UNKNOWN"]


@pytest.mark.asyncio
async def test_loading_batches_progress_and_campaigns_into_one_request():
    twitch = _twitch()
    twitch.gql_request = _gql(_progress_response({"status": "IN_PROGRESS", "currentMinutesWatched": 5}))
    assert len(await InventoryService(twitch).fetch_reward_campaigns()) == 1
    batch, hints, completed = (call.args[0] for call in twitch.gql_request.await_args_list)
    assert [op["query"].split()[1] for op in batch] == ["RewardCampaignProgress", "RewardCampaigns"]
    assert "RewardCampaignHints" in hints["query"] and twitch.gql_request.await_count == 3
    assert "CompletedRewardCampaigns" in completed["query"]


# Goals that cannot be finished before the campaign ends
def _state(progress, goal=60, *, ends_in):
    return RewardCampaign.reward_state(progress, goal, datetime.now(timezone.utc) + ends_in)


def test_next_window_is_checked_from_now_not_from_a_past_wait_time():
    # The wait time passed an hour ago, so a full goal no longer fits in the 30 minutes left.
    past = {"status": "CLAIMED", "nextEligibleAt": _iso(-timedelta(hours=1))}
    assert _state(past, ends_in=timedelta(minutes=30)) == (60, True, None)
    assert _state(past, ends_in=timedelta(minutes=90)) == (0, False, None)


@pytest.mark.parametrize(("minutes", "ends_in", "unmineable"), [
    (30, timedelta(minutes=29), True),
    (30, timedelta(minutes=31), False),
    (59, timedelta(seconds=30), True),
    (59, timedelta(minutes=2), False),
])
def test_reading_that_cannot_reach_the_goal_before_the_end_is_unmineable_but_not_claimed(minutes, ends_in, unmineable):
    reading = {"status": "IN_PROGRESS", "currentMinutesWatched": minutes}
    got_minutes, granted, wait = _state(reading, ends_in=ends_in)
    assert (got_minutes, granted) == (minutes, False)
    assert (wait is not None) is unmineable


def test_unstarted_goal_that_no_longer_fits_is_unmineable_but_not_claimed():
    assert _state(None, ends_in=timedelta(minutes=59))[1:2] == (False,)
    assert _state(None, ends_in=timedelta(minutes=59))[2] is not None
    assert _state(None, ends_in=timedelta(minutes=61)) == (0, False, None)


def test_unfinishable_goal_shows_neither_claimed_nor_earnable_in_inventory():
    twitch = _twitch()
    data = {**_data(), "endsAt": _iso(timedelta(minutes=30))}
    campaign = RewardCampaign.from_reward_campaign(
        twitch, data, {GROUP_ID: {"status": "IN_PROGRESS", "currentMinutesWatched": 10}}, "uid"
    )
    drop = _drop(campaign)
    assert not drop.is_claimed and drop.real_current_minutes == 10
    assert not drop.can_earn(_channel(twitch))


# A passed window leaves Twitch showing the old at-goal reading until the next one starts
def test_stale_at_goal_reading_after_the_window_bumps_instead_of_reloading():
    twitch = _twitch()
    campaign = RewardCampaign.from_reward_campaign(
        twitch, _data(), {GROUP_ID: GRANTED}, "uid", {GROUP_ID: _iso(-timedelta(minutes=2))}
    )
    drop = _drop(campaign)
    assert not drop.is_claimed and drop.real_current_minutes == 0
    channel = _channel(twitch)
    for tick in range(1, 4):
        campaign.apply_reward_progress(GRANTED, channel)
        assert drop.extra_current_minutes == tick
    twitch.request_inventory_refresh.assert_not_called()


def test_at_goal_reading_inside_the_window_still_reloads_once_the_window_ends():
    twitch = _twitch()
    campaign = RewardCampaign.from_reward_campaign(
        twitch, _data(), {GROUP_ID: {"status": "IN_PROGRESS", "currentMinutesWatched": 59}}, "uid",
        {GROUP_ID: _iso(timedelta(hours=3))},
    )
    campaign.apply_reward_progress(GRANTED, _channel(twitch))
    twitch.request_inventory_refresh.assert_called_once()


# Without a started group the first usable, unfinished group is mined
@pytest.mark.parametrize("status", ["EXPIRED", "SOMETHING_NEW", "FULFILLED", "CLAIMED"])
def test_unusable_or_finished_first_lane_does_not_hide_unstarted_lanes(status):
    first = {"lane0": {"status": status, "nextEligibleAt": None}}
    assert list(_pick(_pokemon(), first).timed_drops) == ["lane1"]


def test_only_granted_groups_left_keeps_the_first_granted_one():
    done = {f"lane{n}": {"status": "FULFILLED", "nextEligibleAt": None} for n in range(3)}
    campaign = _pick(_pokemon(), done)
    assert list(campaign.timed_drops) == ["lane0"] and _drop_of(campaign).is_claimed


def test_all_groups_unusable_builds_nothing():
    assert _pick(_pokemon(2), {f"lane{n}": {"status": "EXPIRED"} for n in range(2)}) is None


def _drop_of(campaign):
    return next(iter(campaign.drops))


# Twitch's own grant has no claim, so the miner announces it
def _granted_build(count, twitch=None, windows=None, ledger=None, group=GROUP_ID):
    reading = {**GRANTED, "grantCount": count}
    return RewardCampaign.from_reward_campaign(
        twitch or _twitch(), _data([_group(group)]), {group: reading}, "uid",
        windows or {group: _iso(timedelta(hours=3))}, None, None, ledger,
    )


def _history_ids(twitch):
    return [call.args[2] for call in twitch.drop_history.record.call_args_list]


@pytest.mark.asyncio
async def test_grants_that_exist_at_the_first_load_are_not_announced():
    twitch = _twitch()
    campaign = _granted_build(3, twitch)
    await campaign.announce_new_grants()
    twitch.print.assert_not_called()
    twitch.drop_history.record.assert_not_called()


@pytest.mark.asyncio
async def test_a_grant_seen_on_a_tick_is_announced_once_and_recorded_per_grant():
    twitch = _twitch()
    campaign = RewardCampaign.from_reward_campaign(
        twitch, _data(), {GROUP_ID: {"status": "IN_PROGRESS", "currentMinutesWatched": 59, "grantCount": 1}}, "uid",
    )
    channel = _channel(twitch)
    campaign.apply_reward_progress({**GRANTED, "grantCount": 2}, channel)
    await campaign.announce_new_grants()
    await campaign.announce_new_grants()
    twitch.print.assert_called_once()
    assert _history_ids(twitch) == [f"{GROUP_ID}#2"]
    campaign.apply_reward_progress({**GRANTED, "grantCount": 4}, channel)
    await campaign.announce_new_grants()
    assert _history_ids(twitch) == [f"{GROUP_ID}#2", f"{GROUP_ID}#3", f"{GROUP_ID}#4"]


@pytest.mark.asyncio
async def test_reload_after_a_tick_does_not_announce_the_same_grant_again():
    twitch = _twitch()
    ledger = RewardLedger()
    old = _granted_build(1, twitch, ledger=ledger)
    old.apply_reward_progress({**GRANTED, "grantCount": 2}, _channel(twitch))
    await old.announce_new_grants()
    new = _granted_build(2, twitch, ledger=ledger)
    await new.announce_new_grants()
    assert twitch.print.call_count == 1


@pytest.mark.asyncio
async def test_reload_announces_a_grant_the_tick_did_not_see():
    twitch = _twitch()
    ledger = RewardLedger()
    _granted_build(1, twitch, ledger=ledger)
    new = _granted_build(2, twitch, ledger=ledger)
    await new.announce_new_grants()
    await new.announce_new_grants()
    assert twitch.print.call_count == 1 and _history_ids(twitch) == [f"{GROUP_ID}#2"]


def test_history_keeps_one_entry_per_grant_of_a_repeating_goal(tmp_path):
    from src.drop_history import DropHistory

    history = DropHistory(tmp_path)
    campaign = _granted_build(1)
    drop = _drop(campaign)
    for entry_id in (f"{GROUP_ID}#1", f"{GROUP_ID}#2", f"{GROUP_ID}#2"):
        history.record(drop, campaign, entry_id)
    assert history.total_count == 2


@pytest.mark.asyncio
async def test_reply_for_a_channel_that_is_no_longer_watched_is_not_applied():
    twitch = _twitch()
    campaign = _build({"status": "IN_PROGRESS", "currentMinutesWatched": 5}, twitch=twitch)
    twitch._campaigns[campaign.id] = campaign
    twitch.gql_request = _gql(_progress_response({"status": "IN_PROGRESS", "currentMinutesWatched": 20}))
    service = InventoryService(twitch)
    assert await service.refresh_reward_progress([campaign], _channel(twitch), lambda: False) is True
    assert _drop(campaign).real_current_minutes == 5 and _drop(campaign).extra_current_minutes == 0
    assert await service.refresh_reward_progress([campaign], _channel(twitch), lambda: True) is True
    assert _drop(campaign).real_current_minutes == 20


@pytest.mark.parametrize("flag", [True, False])
def test_can_progress_on_needs_the_drops_flag_except_for_reward_and_special_games(flag):
    twitch = _twitch()
    channel = _channel(twitch, drops_enabled=flag)
    assert _build().can_progress_on(channel) is True
    assert _regular(twitch, GTA).can_progress_on(channel) is flag


# A real claim of a repeating goal
async def _claim_with(drop, twitch):
    twitch.gql_request = AsyncMock(return_value={"data": {"claimDropRewards": {"status": "ELIGIBLE_FOR_ALL"}}})
    assert await drop.claim() is True


def _claimable(twitch, count, ledger=None):
    reading = {"status": "CLAIMABLE", "currentMinutesWatched": 60, "grantCount": count}
    return RewardCampaign.from_reward_campaign(
        twitch, _data(), {GROUP_ID: reading}, "uid", None, None, None, ledger
    )


@pytest.mark.asyncio
async def test_regular_drop_claim_is_recorded_without_an_entry_id():
    twitch = _twitch()
    regular = _regular(twitch, GTA)
    drop = next(iter(regular.drops))
    drop.claim_id = "regular-claim"
    await _claim_with(drop, twitch)
    twitch.drop_history.record.assert_called_once_with(drop, regular)


@pytest.mark.asyncio
async def test_fetch_inventory_reload_announces_a_grant_rise_once():
    from collections import deque

    twitch = _twitch()
    twitch._drops, twitch.inventory, twitch._mnt_triggers, twitch._mnt_task = {}, [], deque(), None
    twitch._state = State.IDLE
    twitch.gui.inv.add_campaign = AsyncMock()
    twitch._maintenance_service = SimpleNamespace(run_maintenance_task=AsyncMock())
    twitch.gql_request = AsyncMock(side_effect=lambda op: (
        {"data": {"currentUser": {"inventory": {"dropCampaignsInProgress": [], "gameEventDrops": []}}}}
        if op is GQL_OPERATIONS["Inventory"] else {"data": {"currentUser": {"dropCampaigns": []}}}
    ))
    service = InventoryService(twitch)
    ledger = service._reward_ledger
    builds = [_granted_build(n, twitch, ledger=ledger) for n in (1, 2, 2)]
    service.fetch_reward_campaigns = AsyncMock(side_effect=lambda: [builds.pop(0)])
    await service.fetch_inventory()  # first load: grants that already exist are not news
    twitch.print.assert_not_called()
    await service.fetch_inventory()  # the count rose between reloads
    assert twitch.print.call_count == 1 and _history_ids(twitch) == [f"{GROUP_ID}#2"]
    await service.fetch_inventory()  # unchanged
    assert twitch.print.call_count == 1
    await twitch._mnt_task


# Review round: grant bookkeeping is per group and survives reloads
@pytest.mark.asyncio
async def test_reload_onto_another_group_neither_invents_nor_misses_grants():
    twitch = _twitch()
    ledger = RewardLedger()
    await _granted_build(3, twitch, ledger=ledger, group="gB").announce_new_grants()
    # The reload now mines group gA, first seen with 0 grants; gB (3 grants) is not replayed.
    await _granted_build(0, twitch, ledger=ledger, group="gA").announce_new_grants()
    gb_again = _granted_build(3, twitch, ledger=ledger, group="gB")
    await gb_again.announce_new_grants()
    twitch.print.assert_not_called()
    # gA's first grant is not missed because gB was announced earlier.
    ga = _granted_build(1, twitch, ledger=ledger, group="gA")
    await ga.announce_new_grants()
    assert _history_ids(twitch) == ["gA#1"]


@pytest.mark.asyncio
async def test_a_failed_reward_load_does_not_lose_a_grant():
    twitch = _twitch()
    twitch.gql_request = AsyncMock(side_effect=GQLException("down"))
    service = InventoryService(twitch)
    first = _granted_build(1, twitch, ledger=service._reward_ledger)
    assert await service.fetch_reward_campaigns() == []
    second = _granted_build(2, twitch, ledger=service._reward_ledger)
    await second.announce_new_grants()
    assert first is not second and _history_ids(twitch) == [f"{GROUP_ID}#2"]


def test_a_grant_rise_is_a_grant_even_when_the_stored_window_looks_over():
    twitch = _twitch()
    campaign = _granted_build(1, twitch, windows={GROUP_ID: _iso(-timedelta(hours=20))})
    campaign.apply_reward_progress({**GRANTED, "grantCount": 2}, _channel(twitch))
    twitch.request_inventory_refresh.assert_called_once()
    campaign.apply_reward_progress({**GRANTED, "grantCount": 2}, _channel(twitch))
    twitch.request_inventory_refresh.assert_called_once()


def test_group_that_can_be_mined_now_beats_a_waiting_group():
    waiting = {"status": "CLAIMED", "nextEligibleAt": _iso(timedelta(hours=10))}
    mining = {"status": "IN_PROGRESS", "currentMinutesWatched": 3}
    picked = _pick(_pokemon(), {"lane0": waiting, "lane1": mining})
    assert list(picked.timed_drops) == ["lane1"]
    # Only a started lane counts, so a waiting started lane stays ahead of an unstarted one.
    assert list(_pick(_pokemon(), {"lane0": waiting}).timed_drops) == ["lane0"]
    assert list(_pick(_pokemon(), {"lane1": waiting}).timed_drops) == ["lane1"]
    # With nothing mineable the waiting group is shown, ahead of a granted one.
    done = {"status": "FULFILLED", "nextEligibleAt": None}
    assert list(_pick(_pokemon(2), {"lane0": done, "lane1": waiting}).timed_drops) == ["lane1"]


def test_malformed_entries_without_an_id_are_skipped_by_the_parsers():
    bad_group = {"self": {"currentWindow": {"expiresAt": "T"}}, "rewards": [
        {"dropDetail": {"badge": {}}}, {"id": 5, "dropDetail": {"badge": {}}},
        {"id": "r", "dropDetail": {"badge": {}}}]}
    good = {"id": "g", "self": {"currentWindow": {"expiresAt": "T"}}, "rewards": []}
    assert InventoryService._parse_reward_hints(_hints([bad_group, good])) == ({"g": "T"}, {"r"})
    response = {"data": {"currentUser": {"inventory": {"viewerRewardDropCampaignsInProgress": [
        {"id": "c1"}, {"id": "c2", "rewardGroups": [{"self": {"status": "X"}}, {"id": 7, "self": {}}]},
        {"id": "c3", "rewardGroups": [{"id": "g", "self": {"status": "IN_PROGRESS"}}]},
    ]}}}}
    assert InventoryService._parse_reward_progress(response) == (
        {"g": {"status": "IN_PROGRESS"}}, {"g": "c3"}
    )


@pytest.mark.asyncio
async def test_announcements_wait_until_the_inventory_is_rebuilt():
    twitch = _twitch()
    twitch._drops, twitch.inventory, twitch._mnt_triggers, twitch._mnt_task = {}, [], __import__("collections").deque(), None
    twitch._state = State.IDLE
    twitch.gui.inv.add_campaign = AsyncMock()
    twitch._maintenance_service = SimpleNamespace(run_maintenance_task=AsyncMock())
    twitch.gql_request = AsyncMock(side_effect=lambda op: (
        {"data": {"currentUser": {"inventory": {"dropCampaignsInProgress": [], "gameEventDrops": []}}}}
        if op is GQL_OPERATIONS["Inventory"] else {"data": {"currentUser": {"dropCampaigns": []}}}
    ))
    service = InventoryService(twitch)
    seen = []
    twitch.gui.inv.add_campaign = AsyncMock(side_effect=lambda *_: seen.append("added"))
    twitch.print = MagicMock(side_effect=lambda *_: seen.append("announced"))
    builds = [_granted_build(n, twitch, ledger=service._reward_ledger) for n in (1, 2)]
    service.fetch_reward_campaigns = AsyncMock(side_effect=lambda: [builds.pop(0)])
    await service.fetch_inventory()
    await service.fetch_inventory()
    assert seen == ["added", "added", "announced"]


@pytest.mark.asyncio
async def test_grant_message_shows_the_grant_number_not_a_claimed_count():
    twitch = _twitch()
    campaign = _granted_build(0, twitch)
    campaign.reward_grants = 1
    await campaign.announce_new_grants()
    text = twitch.print.call_args.args[0]
    assert "(#1)" in text and "(0/1)" not in text and "(1/1)" not in text


@pytest.mark.parametrize(("readings", "expected"), [
    # W = started, waiting for the next window; R = started, in progress; - = unstarted
    (("W", "-", "-"), "lane0"),
    (("-", "W", "-"), "lane1"),
    (("W", "R", "-"), "lane1"),
    (("G", "W", "-"), "lane1"),
    (("AT", "-", "-"), "lane0"),  # at the goal inside an open window
    (("F", "-", "-"), "lane1"),  # finished final reward, others unstarted
    (("-", "-", "R"), "lane2"),
    (("F", "F", "-"), "lane2"),
])
def test_lane_order_started_before_unstarted(readings, expected):
    table = {
        "W": {"status": "CLAIMED", "nextEligibleAt": _iso(timedelta(hours=10))},
        "R": {"status": "IN_PROGRESS", "currentMinutesWatched": 3},
        "AT": {"status": "IN_PROGRESS", "currentMinutesWatched": 20},
        "G": {"status": "CLAIMED", "nextEligibleAt": None},
        "F": {"status": "FULFILLED", "nextEligibleAt": None},
    }
    progress = {f"lane{n}": table[r] for n, r in enumerate(readings) if r != "-"}
    windows = {"lane0": _iso(timedelta(hours=3))} if readings[0] == "AT" else None
    assert list(_pick(_pokemon(), progress, windows).timed_drops) == [expected]


# Claim bookkeeping
def test_a_lower_reload_reading_does_not_reload_on_every_tick():
    twitch = _twitch()
    ledger = RewardLedger()
    ledger.announced[GROUP_ID] = 3
    campaign = _granted_build(1, twitch, {GROUP_ID: _iso(-timedelta(minutes=2))}, ledger)  # lower count, stale window
    for _ in range(3):
        campaign.apply_reward_progress({**GRANTED, "grantCount": 2}, _channel(twitch))
    twitch.request_inventory_refresh.assert_not_called()


# Announcement failures never escape
@pytest.mark.asyncio
async def test_announcement_errors_do_not_escape_a_reload_or_a_tick(caplog):
    twitch = _twitch()
    twitch.drop_history.record.side_effect = OSError("disk full")
    twitch._drops, twitch.inventory, twitch._mnt_triggers, twitch._mnt_task = {}, [], __import__("collections").deque(), None
    twitch._state = State.IDLE
    twitch.gui.inv.add_campaign = AsyncMock()
    twitch._maintenance_service = SimpleNamespace(run_maintenance_task=AsyncMock())
    twitch.gql_request = AsyncMock(side_effect=lambda op: (
        {"data": {"currentUser": {"inventory": {"dropCampaignsInProgress": [], "gameEventDrops": []}}}}
        if op is GQL_OPERATIONS["Inventory"] else {"data": {"currentUser": {"dropCampaigns": []}}}
    ))
    service = InventoryService(twitch)
    builds = [_granted_build(n, twitch, ledger=service._reward_ledger) for n in (1, 2)]
    service.fetch_reward_campaigns = AsyncMock(side_effect=lambda: [builds.pop(0)])
    await service.fetch_inventory()
    with caplog.at_level("WARNING", logger="TwitchDrops"):
        await service.fetch_inventory()
    assert "announcement" in caplog.text
    await twitch._mnt_task
    # Tick path
    campaign = _granted_build(2, twitch, ledger=service._reward_ledger)
    twitch._campaigns[campaign.id] = campaign
    twitch.gql_request = _gql(_progress_response({**GRANTED, "grantCount": 3}), windows={GROUP_ID: _iso(timedelta(hours=3))})
    assert await service.refresh_reward_progress([campaign], _channel(twitch)) is True


# Stale at-goal reading that reaches the extra-minute cutoff
def test_stale_reading_at_the_cutoff_is_remembered_until_the_reading_changes():
    twitch = _twitch()
    ledger = RewardLedger()
    stale_window = {GROUP_ID: _iso(-timedelta(minutes=2))}

    def build(reading):
        return RewardCampaign.from_reward_campaign(
            twitch, _data(), {GROUP_ID: reading}, "uid", stale_window, None, None, ledger
        )

    campaign = build(GRANTED)
    channel = _channel(twitch)
    for _ in range(MAX_EXTRA_MINUTES):
        campaign.apply_reward_progress(GRANTED, channel)
    twitch.request_inventory_refresh.assert_not_called()
    assert ledger.stale
    assert _drop(build(GRANTED)).is_claimed  # same stale reading: treated as granted
    # A changed reading (new grant count, then a minute below the goal) clears the memo.
    assert not _drop(build({**GRANTED, "grantCount": 2})).is_claimed and not ledger.stale
    campaign = build(GRANTED)
    for _ in range(MAX_EXTRA_MINUTES):
        campaign.apply_reward_progress(GRANTED, channel)
    assert ledger.stale
    campaign.apply_reward_progress({"status": "IN_PROGRESS", "currentMinutesWatched": 1, "grantCount": 1}, channel)
    assert not ledger.stale


# A group Twitch never credits: held after two cutoffs instead of looping every 30 minutes
BELOW = {"status": "IN_PROGRESS", "currentMinutesWatched": 10, "grantCount": 0}
BELOW_SIGNATURE = ("IN_PROGRESS", 0, 10, None)


def _uncredited_build(twitch, ledger, reading, data=None, windows=None):
    return RewardCampaign.from_reward_campaign(
        twitch, data or _data(), {} if reading is None else {GROUP_ID: reading}, "uid",
        windows, None, None, ledger,
    )


def _run_to_cutoff(campaign, twitch, reading):
    channel = _channel(twitch)
    for _ in range(MAX_EXTRA_MINUTES):
        campaign.apply_reward_progress(reading, channel)


def _hold(twitch, ledger, reading, **kwargs):
    """Two cutoffs, each on a rebuilt campaign like two inventory reloads."""
    for _ in range(2):
        _run_to_cutoff(_uncredited_build(twitch, ledger, reading, **kwargs), twitch, reading)


def _assert_held(campaign, at_least=timedelta(hours=5, minutes=55), at_most=timedelta(hours=6)):
    drop = _drop(campaign)
    assert not drop.can_earn()
    assert not drop.is_claimed
    assert at_least < drop.starts_at - datetime.now(timezone.utc) <= at_most


@pytest.mark.parametrize("reading", [BELOW, None])
def test_two_uncredited_cutoffs_hold_the_group_on_rebuild(reading):
    twitch, ledger = _twitch(), RewardLedger()
    _hold(twitch, ledger, reading)
    _assert_held(_uncredited_build(twitch, ledger, reading))
    assert GROUP_ID in ledger.uncredited and not ledger.stale


def test_one_uncredited_cutoff_is_mined_again():
    twitch, ledger = _twitch(), RewardLedger()
    _run_to_cutoff(_uncredited_build(twitch, ledger, BELOW), twitch, BELOW)
    assert _drop(_uncredited_build(twitch, ledger, BELOW)).can_earn()
    assert ledger.uncredited[GROUP_ID][2] is False


def test_the_hold_is_logged_once_on_the_second_cutoff(caplog):
    twitch, ledger = _twitch(), RewardLedger()
    with caplog.at_level("INFO", logger="TwitchDrops"):
        _run_to_cutoff(_uncredited_build(twitch, ledger, BELOW), twitch, BELOW)
        assert "did not credit" not in caplog.text
        campaign = _uncredited_build(twitch, ledger, BELOW)
        _run_to_cutoff(campaign, twitch, BELOW)
        _run_to_cutoff(campaign, twitch, BELOW)
    assert caplog.text.count("did not credit") == 1
    assert re.search(r"retrying after \d{4}-\d\d-\d\d \d\d:\d\d$", caplog.messages[-1])


def test_a_reading_that_rises_before_the_cutoff_records_nothing():
    twitch, ledger = _twitch(), RewardLedger()
    campaign = _uncredited_build(twitch, ledger, BELOW)
    channel = _channel(twitch)
    for _ in range(5):
        campaign.apply_reward_progress(BELOW, channel)
    risen = {**BELOW, "currentMinutesWatched": 11}
    for _ in range(MAX_EXTRA_MINUTES - 1):
        campaign.apply_reward_progress(risen, channel)
    assert not ledger.uncredited


@pytest.mark.parametrize("changed", [
    {**BELOW, "currentMinutesWatched": 11},
    {**BELOW, "grantCount": 1},
])
def test_a_changed_reading_clears_the_hold_on_rebuild(changed):
    twitch, ledger = _twitch(), RewardLedger()
    _hold(twitch, ledger, BELOW)
    assert _drop(_uncredited_build(twitch, ledger, changed)).can_earn()
    assert not ledger.uncredited


@pytest.mark.parametrize("cutoffs", [1, 2])
def test_a_changed_reading_on_a_tick_clears_pending_and_hold(cutoffs):
    twitch, ledger = _twitch(), RewardLedger()
    for _ in range(cutoffs - 1):
        _run_to_cutoff(_uncredited_build(twitch, ledger, BELOW), twitch, BELOW)
    campaign = _uncredited_build(twitch, ledger, BELOW)
    _run_to_cutoff(campaign, twitch, BELOW)
    assert ledger.uncredited
    campaign.apply_reward_progress({**BELOW, "currentMinutesWatched": 12}, _channel(twitch))
    assert not ledger.uncredited


def test_an_expired_hold_is_mined_again_and_can_hold_again(caplog):
    twitch, ledger = _twitch(), RewardLedger()
    _hold(twitch, ledger, BELOW)
    signature = ledger.uncredited[GROUP_ID][0]
    ledger.uncredited[GROUP_ID] = (signature, datetime.now(timezone.utc) - timedelta(minutes=1), True)
    assert _drop(_uncredited_build(twitch, ledger, BELOW)).can_earn()
    assert not ledger.uncredited
    with caplog.at_level("INFO", logger="TwitchDrops"):
        _hold(twitch, ledger, BELOW)
    assert caplog.text.count("did not credit") == 1
    _assert_held(_uncredited_build(twitch, ledger, BELOW))


def test_an_expired_hold_met_on_a_tick_gives_way_to_a_new_pending_cutoff():
    twitch, ledger = _twitch(), RewardLedger()
    campaign = _uncredited_build(twitch, ledger, BELOW)
    ledger.uncredited[GROUP_ID] = (BELOW_SIGNATURE, datetime.now(timezone.utc) - timedelta(minutes=1), True)
    _run_to_cutoff(campaign, twitch, BELOW)
    assert ledger.uncredited[GROUP_ID][2] is False


def test_setting_a_hold_prunes_expired_holds_of_other_groups():
    twitch, ledger = _twitch(), RewardLedger()
    past = datetime.now(timezone.utc) - timedelta(hours=1)
    ledger.uncredited["old"] = (BELOW_SIGNATURE, past, True)
    ledger.uncredited["old pending"] = (BELOW_SIGNATURE, past, False)
    _hold(twitch, ledger, BELOW)
    assert set(ledger.uncredited) == {GROUP_ID}


def test_the_retry_never_skips_the_last_finishable_attempt():
    twitch, ledger = _twitch(), RewardLedger()
    data = {**_data(), "endsAt": _iso(timedelta(hours=3))}
    _hold(twitch, ledger, BELOW, data=data)
    # 50 minutes are left to watch (10 of 60 done) plus one cutoff of slack: at most 1h55.
    _assert_held(
        _uncredited_build(twitch, ledger, BELOW, data),
        at_least=timedelta(hours=1, minutes=50), at_most=timedelta(hours=1, minutes=55),
    )


def test_no_hold_when_the_last_finishable_start_has_passed():
    twitch, ledger = _twitch(), RewardLedger()
    ledger.uncredited[GROUP_ID] = (BELOW_SIGNATURE, datetime.now(timezone.utc) + timedelta(hours=6), False)
    campaign = _uncredited_build(twitch, ledger, BELOW)
    campaign.ends_at = datetime.now(timezone.utc) + timedelta(minutes=30)
    # Called directly: a tick never gets here, because an unfinishable goal is not bumped.
    campaign._track_uncredited(_drop(campaign), BELOW_SIGNATURE, True, False)
    assert ledger.uncredited[GROUP_ID][2] is False
    assert _drop(_uncredited_build(twitch, ledger, BELOW)).can_earn()


def test_a_hold_survives_a_failed_hints_query():
    twitch, ledger = _twitch(), RewardLedger()
    window = {GROUP_ID: _iso(timedelta(hours=1))}
    for _ in range(2):
        campaign = _uncredited_build(twitch, ledger, BELOW, windows=window)
        campaign.window_end = window[GROUP_ID]
        _run_to_cutoff(campaign, twitch, BELOW)
    _assert_held(_uncredited_build(twitch, ledger, BELOW))


def test_a_hold_recorded_without_a_window_matches_a_rebuild_with_one():
    twitch, ledger = _twitch(), RewardLedger()
    _hold(twitch, ledger, BELOW)
    window = {GROUP_ID: _iso(timedelta(hours=1))}
    _assert_held(_uncredited_build(twitch, ledger, BELOW, windows=window))


def test_a_missing_reading_on_a_tick_of_a_campaign_built_with_one_records_nothing():
    twitch, ledger = _twitch(), RewardLedger()
    for _ in range(2):
        _run_to_cutoff(_uncredited_build(twitch, ledger, BELOW), twitch, None)
    assert not ledger.uncredited


def test_claimable_reaching_the_cutoff_records_nothing():
    twitch, ledger = _twitch(), RewardLedger()
    claimable = {"status": "CLAIMABLE", "currentMinutesWatched": 60, "grantCount": 0}
    campaign = _uncredited_build(twitch, ledger, claimable)
    _run_to_cutoff(campaign, twitch, claimable)
    assert not ledger.uncredited and not ledger.stale


def test_stale_at_goal_reading_records_only_the_stale_memo():
    twitch, ledger = _twitch(), RewardLedger()
    window = {GROUP_ID: _iso(-timedelta(minutes=2))}
    campaign = RewardCampaign.from_reward_campaign(
        twitch, _data(), {GROUP_ID: GRANTED}, "uid", window, None, None, ledger
    )
    _run_to_cutoff(campaign, twitch, GRANTED)
    assert ledger.stale and not ledger.uncredited


def test_a_hold_does_not_change_a_granted_state():
    twitch, ledger = _twitch(), RewardLedger()
    signature = RewardCampaign._reading_signature(GRANTED, None)
    ledger.uncredited[GROUP_ID] = (signature, datetime.now(timezone.utc) + timedelta(hours=6), True)
    drop = _drop(_uncredited_build(twitch, ledger, GRANTED))
    assert drop.is_claimed
    assert drop.starts_at < datetime.now(timezone.utc)


def test_a_hold_does_not_change_a_waiting_window():
    twitch, ledger = _twitch(), RewardLedger()
    window = {GROUP_ID: _iso(timedelta(hours=1))}
    signature = RewardCampaign._reading_signature(GRANTED, window[GROUP_ID])
    ledger.uncredited[GROUP_ID] = (signature, datetime.now(timezone.utc) + timedelta(hours=6), True)
    drop = _drop(_uncredited_build(twitch, ledger, GRANTED, windows=window))
    assert drop.starts_at - datetime.now(timezone.utc) < timedelta(hours=2)


def test_an_expired_pending_entry_is_replaced_by_a_new_one():
    twitch, ledger = _twitch(), RewardLedger()
    campaign = _uncredited_build(twitch, ledger, BELOW)
    stamp = datetime.now(timezone.utc) - timedelta(minutes=1)
    ledger.uncredited[GROUP_ID] = (BELOW_SIGNATURE, stamp, False)
    _run_to_cutoff(campaign, twitch, BELOW)
    # Without the expiry this second cutoff would turn the old pending entry into a hold.
    assert ledger.uncredited[GROUP_ID][2] is False
    assert ledger.uncredited[GROUP_ID][1] > datetime.now(timezone.utc)


@pytest.mark.parametrize("cutoffs", [1, 2])
def test_a_missing_reading_keeps_an_entry_recorded_from_a_real_one(cutoffs):
    twitch, ledger = _twitch(), RewardLedger()
    for _ in range(cutoffs):
        _run_to_cutoff(_uncredited_build(twitch, ledger, BELOW), twitch, BELOW)
    entry = ledger.uncredited[GROUP_ID]
    rebuilt = _uncredited_build(twitch, ledger, None)
    assert ledger.uncredited[GROUP_ID] == entry
    if cutoffs == 2:
        _assert_held(rebuilt)


def test_a_held_lane_stays_chosen_over_unstarted_lanes():
    twitch, ledger = _twitch(), RewardLedger()
    data = _data([_group(), _group("g2")])
    _hold(twitch, ledger, None, data=data)
    rebuilt = _uncredited_build(twitch, ledger, None, data)
    assert list(rebuilt.timed_drops) == [GROUP_ID]
    _assert_held(rebuilt)


def test_a_held_reward_campaign_is_not_a_wanted_game():
    twitch, ledger = _twitch(), RewardLedger()
    settings = _settings(
        GTA["displayName"], mining_benefits=dict.fromkeys(RewardPriority.TYPES, True)
    )
    fresh = _uncredited_build(twitch, ledger, None)
    assert [g.name for g in StreamSelector().get_wanted_games(settings, [fresh])]
    _hold(twitch, ledger, None)
    held = _uncredited_build(twitch, ledger, None)
    assert StreamSelector().get_wanted_games(settings, [held]) == []


# Malformed groups and parsers
@pytest.mark.parametrize("bad", [
    {"id": "bad1", "categories": [GTA], "rewards": [{"id": "x", "name": "x"}]},
    {"id": "bad2", "unlockRequirements": None, "categories": [GTA], "rewards": [{"id": "x", "name": "x"}]},
    {"id": "bad3", "unlockRequirements": {"minuteWatchedGoal": "60"}, "categories": [GTA], "rewards": [{"id": "x"}]},
    {"id": "bad4", "unlockRequirements": {"minuteWatchedGoal": None}, "categories": [GTA], "rewards": [{"id": "x"}]},
    {"id": "bad5", "unlockRequirements": {"minuteWatchedGoal": True}, "categories": [GTA], "rewards": [{"id": "x"}]},
    "junk",
])
def test_one_malformed_group_does_not_drop_the_campaign(bad):
    campaign = _build(data=_data([bad, _group()]))
    assert list(campaign.timed_drops) == [GROUP_ID]


def test_parsers_treat_empty_ids_and_non_list_collections_as_missing():
    groups = [
        {"id": "", "self": {"currentWindow": {"expiresAt": "T"}}, "rewards": [{"id": "", "dropDetail": {"badge": {}}}]},
        {"id": "g", "self": {"currentWindow": {"expiresAt": "T"}}, "rewards": "oops"},
    ]
    assert InventoryService._parse_reward_hints(_hints(groups)) == ({"g": "T"}, set())
    response = {"data": {"currentUser": {"inventory": {"viewerRewardDropCampaignsInProgress": [
        {"id": "c", "rewardGroups": {"id": "x"}},
        {"id": "c2", "rewardGroups": [{"id": "", "self": {}}, {"id": "g", "self": {"status": "X"}}]},
        "junk",
    ]}}}}
    assert InventoryService._parse_reward_progress(response) == ({"g": {"status": "X"}}, {"g": "c2"})
    assert InventoryService._parse_reward_hints(_hints("oops")) == ({}, set())


# A reward claim is quiet: grants are announced from grantCount rises only
@pytest.mark.asyncio
async def test_claim_then_the_grant_count_rise_announces_one_grant():
    twitch = _twitch()
    campaign = _claimable(twitch, 1)
    await _claim_with(_drop(campaign), twitch)
    twitch.print.assert_not_called()
    twitch.drop_history.record.assert_not_called()
    campaign.apply_reward_progress({**GRANTED, "grantCount": 2}, _channel(twitch))
    await campaign.announce_new_grants()
    assert twitch.print.call_count == 1 and _history_ids(twitch) == [f"{GROUP_ID}#2"]
    await campaign.announce_new_grants()
    assert twitch.print.call_count == 1


@pytest.mark.asyncio
async def test_repeated_claim_call_adds_nothing():
    twitch = _twitch()
    drop = _drop(_claimable(twitch, 1))
    await _claim_with(drop, twitch)
    await _claim_with(drop, twitch)
    twitch.print.assert_not_called()
    twitch.drop_history.record.assert_not_called()


@pytest.mark.asyncio
async def test_a_claim_twitch_does_not_count_in_grant_count_is_not_announced():
    twitch = _twitch()
    campaign = _claimable(twitch, 4)
    await _claim_with(_drop(campaign), twitch)
    campaign.apply_reward_progress({**GRANTED, "grantCount": 4}, _channel(twitch))
    await campaign.announce_new_grants()
    twitch.print.assert_not_called()


@pytest.mark.asyncio
async def test_rebuild_without_the_group_does_not_rebase_the_ledger():
    twitch = _twitch()
    ledger = RewardLedger()
    _granted_build(3, twitch, ledger=ledger)
    # Readings miss the group, or lack a usable grantCount: nothing is initialised or changed.
    missing = RewardCampaign.from_reward_campaign(twitch, _data(), {}, "uid", None, None, None, ledger)
    bad = RewardCampaign.from_reward_campaign(
        twitch, _data(), {GROUP_ID: {**GRANTED, "grantCount": None}}, "uid", None, None, None, ledger
    )
    assert missing is not None and bad is not None and ledger.announced == {GROUP_ID: 3}
    campaign = _granted_build(4, twitch, ledger=ledger)
    await campaign.announce_new_grants()
    assert _history_ids(twitch) == [f"{GROUP_ID}#4"] and twitch.print.call_count == 1


@pytest.mark.asyncio
async def test_first_reading_already_at_the_goal_is_not_announced():
    twitch = _twitch()
    ledger = RewardLedger()
    campaign = RewardCampaign.from_reward_campaign(twitch, _data(), {}, "uid", None, None, None, ledger)
    assert ledger.announced == {}
    campaign.apply_reward_progress({**GRANTED, "grantCount": 2}, _channel(twitch))
    await campaign.announce_new_grants()
    twitch.print.assert_not_called()


@pytest.mark.asyncio
async def test_first_grant_of_a_group_that_was_not_started_at_load_is_announced():
    twitch = _twitch()
    ledger = RewardLedger()
    build = lambda: RewardCampaign.from_reward_campaign(  # noqa: E731
        twitch, _data(), {}, "uid", None, None, None, ledger
    )
    campaign = build()
    for minutes in (0, 30, 59):
        campaign.apply_reward_progress(
            {"status": "IN_PROGRESS", "currentMinutesWatched": minutes, "grantCount": 0}, _channel(twitch)
        )
    campaign.apply_reward_progress({**GRANTED, "grantCount": 1}, _channel(twitch))
    twitch.request_inventory_refresh.assert_called_once()
    # The reload sees the group with its reading: setdefault must not swallow the grant.
    rebuilt = _granted_build(1, twitch, ledger=ledger)
    await rebuilt.announce_new_grants()
    await rebuilt.announce_new_grants()
    assert twitch.print.call_count == 1 and _history_ids(twitch) == [f"{GROUP_ID}#1"]


@pytest.mark.asyncio
async def test_first_grant_after_a_lane_switch_to_an_unstarted_group_is_announced():
    twitch = _twitch()
    ledger = RewardLedger()
    # Lane 0 is mined, lane 1 has no reading; the miner switches to lane 1.
    other = RewardCampaign.from_reward_campaign(
        twitch, _data([_group("lane1")]), {}, "uid", None, None, None, ledger
    )
    other.apply_reward_progress({"status": "IN_PROGRESS", "currentMinutesWatched": 1, "grantCount": 0}, _channel(twitch))
    assert ledger.announced["lane1"] == 0
    other.apply_reward_progress({**GRANTED, "grantCount": 1}, _channel(twitch))
    await other.announce_new_grants()
    assert twitch.print.call_count == 1 and _history_ids(twitch) == ["lane1#1"]


# Unfinishable goal is not mineable
def test_unfinishable_goal_is_not_mineable_not_wanted_and_not_earnable_within_the_hour():
    twitch = _twitch()
    data = {**_data(), "endsAt": _iso(timedelta(minutes=30))}
    campaign = RewardCampaign.from_reward_campaign(twitch, data, {}, "uid")
    drop = _drop(campaign)
    assert not drop.is_mineable and not drop._can_earn_within(datetime.now(timezone.utc) + timedelta(hours=1))
    assert drop.get_wanted_unclaimed_benefits({}) == []
    assert not campaign.mineable_watch_drops
    assert InventoryManager._serialize_drop(drop)["is_mineable"] is False


# Watch filter: a malformed group is skipped, not the campaign
@pytest.mark.parametrize("change", [
    {"rewards": [{"id": "x"}]},
    {"rewards": ["x"]},
    {"categories": "GTA"},
    {"categories": [{}]},
    {"rewards": {"id": "x", "name": "x"}},
])
def test_group_with_malformed_rewards_or_categories_is_skipped(change):
    bad = {**_group("bad"), **change}
    campaign = _build(data=_data([bad, _group()]))
    assert list(campaign.timed_drops) == [GROUP_ID]


def test_stale_memo_survives_one_failed_hints_query():
    twitch = _twitch()
    ledger = RewardLedger()
    stale_window = {GROUP_ID: _iso(-timedelta(minutes=2))}
    campaign = RewardCampaign.from_reward_campaign(twitch, _data(), {GROUP_ID: GRANTED}, "uid", stale_window, None, None, ledger)
    for _ in range(MAX_EXTRA_MINUTES):
        campaign.apply_reward_progress(GRANTED, _channel(twitch))
    assert ledger.stale
    rebuilt = RewardCampaign.from_reward_campaign(twitch, _data(), {GROUP_ID: GRANTED}, "uid", None, None, None, ledger)
    assert _drop(rebuilt).is_claimed and ledger.stale


@pytest.mark.parametrize("change", [
    {"categories": [{"id": "1", "slug": "gta"}]},
    {"categories": [{"id": [1], "displayName": "GTA"}]},
    {"categories": [{"id": "abc", "displayName": "GTA"}]},
    {"categories": [{"id": True, "displayName": "GTA"}]},
    {"rewards": [{"id": "x", "name": "x", "thumbnailImage": "u"}]},
    {"rewards": [{"id": "x", "name": "x", "thumbnailImage": []}]},
])
def test_group_that_game_or_the_drop_build_cannot_read_is_skipped(change):
    bad = {**_group("bad"), **change}
    assert list(_build(data=_data([bad, _group()])).timed_drops) == [GROUP_ID]


@pytest.mark.parametrize("change", [
    {"categories": [{"id": 1, "displayName": "GTA"}]},
    {"categories": [{"id": "1", "name": "GTA"}]},
    {"rewards": [{"id": "x", "name": "", "thumbnailImage": None}]},
    {"rewards": [{"id": "x", "name": "x"}]},
    {"unlockRequirements": {"minuteWatchedGoal": 60, "subsGoal": None}},
])
def test_valid_group_shapes_are_still_picked(change):
    group = {**_group("ok"), **change}
    assert list(_build(data=_data([group])).timed_drops) == ["ok"]


def test_a_load_after_the_ledger_is_cleared_announces_no_existing_grant():
    twitch, ledger = _twitch(), RewardLedger()
    ledger.announced[GROUP_ID] = 0
    for part in (ledger.announced, ledger.stale, ledger.uncredited):
        part.clear()
    campaign = _granted_build(2, twitch, ledger=ledger)
    asyncio.run(campaign.announce_new_grants())
    assert ledger.announced[GROUP_ID] == 2
    twitch.print.assert_not_called()


# Campaigns Twitch lists in completedRewardCampaigns
EMPTY_PROGRESS = {"data": {"currentUser": {"inventory": {"viewerRewardDropCampaignsInProgress": []}}}}


async def _load(progress=EMPTY_PROGRESS, *, data=None, service=None, **gql):
    twitch = service._twitch if service else _twitch()
    campaigns = {"data": {"rewardCampaignsAvailableToUser": [data or _data()]}}
    twitch.gql_request = _gql(progress, campaigns=campaigns, **gql)
    return await (service or InventoryService(twitch)).fetch_reward_campaigns()


@pytest.mark.parametrize("reading", [
    None,  # after the final grant Twitch drops the campaign from the in-progress list
    {"status": "FULFILLED", "grantCount": 2, "nextEligibleAt": None},
    {"status": "CLAIMED", "grantCount": 2, "nextEligibleAt": _iso(timedelta(hours=3))},
    GRANTED,
], ids=["missing", "fulfilled", "waiting", "granted"])
@pytest.mark.asyncio
async def test_completed_campaign_without_an_earnable_reading_is_not_built(reading):
    progress = EMPTY_PROGRESS if reading is None else _progress_response(reading)
    assert await _load(progress, completed=(CAMPAIGN_ID,)) == []


@pytest.mark.asyncio
async def test_completed_campaign_whose_reading_is_held_or_unfinishable_is_not_built():
    service = InventoryService(_twitch())
    until = datetime.now(timezone.utc) + timedelta(hours=1)
    service._reward_ledger.uncredited[GROUP_ID] = (BELOW_SIGNATURE, until, True)
    assert await _load(_progress_response(BELOW), service=service, completed=(CAMPAIGN_ID,)) == []
    ending = {**_data(), "endsAt": _iso(timedelta(minutes=30))}
    assert await _load(_progress_response(BELOW), data=ending, completed=(CAMPAIGN_ID,)) == []


@pytest.mark.parametrize("reading", [
    {"status": "IN_PROGRESS", "currentMinutesWatched": 5, "grantCount": 1},
    {"status": "CLAIMABLE", "currentMinutesWatched": 60},
    {"status": "CLAIMED", "grantCount": 1, "nextEligibleAt": _iso(-timedelta(minutes=5))},
], ids=["in-progress", "claimable", "next-window-open"])
@pytest.mark.asyncio
async def test_completed_campaign_with_an_earnable_reading_is_still_mined(reading):
    [campaign] = await _load(_progress_response(reading), completed=(CAMPAIGN_ID,))
    assert campaign.earnable_from_reading and not _drop(campaign).is_claimed


@pytest.mark.asyncio
async def test_campaign_not_listed_as_completed_is_mined_from_a_missing_reading():
    [campaign] = await _load(completed=("another-campaign",))
    assert _drop(campaign).current_minutes == 0 and _drop(campaign).can_earn(ignore_channel_status=True)


@pytest.mark.parametrize("status", [None, "ACTIVE", "completed", "EXPIRED"])
@pytest.mark.asyncio
async def test_only_status_completed_skips_a_campaign(status):
    twitch = _twitch()
    plain = _gql(EMPTY_PROGRESS)

    async def request(op):
        if "query" in op and "CompletedRewardCampaigns" in op["query"]:
            return _completed_response([{"id": CAMPAIGN_ID, "status": status}])
        return await plain(op)

    twitch.gql_request = request
    assert [c.id for c in await InventoryService(twitch).fetch_reward_campaigns()] == [CAMPAIGN_ID]


@pytest.mark.asyncio
async def test_a_failed_completed_list_leaves_loading_unchanged(caplog):
    with caplog.at_level("INFO", logger="TwitchDrops"):
        [campaign] = await _load(completed=None)
    assert _drop(campaign).current_minutes == 0 and not _drop(campaign).is_claimed
    [record] = [r for r in caplog.records if "Completed Open Reward Campaigns" in r.getMessage()]
    assert record.levelname == "INFO"  # like the hints: no warning on every reload
    assert "completedRewardCampaigns" not in caplog.text  # no error text or body


@pytest.mark.parametrize("response", [
    None, {}, {"data": None}, {"data": {"currentUser": None}},
    {"data": {"currentUser": {"inventory": None}}},
    {"data": {"currentUser": {"inventory": {}}}},
    _completed_response(None), _completed_response(5), _completed_response("x"),
])
@pytest.mark.asyncio
async def test_unusable_completed_list_skips_nothing(response):
    twitch = _twitch()
    twitch.gql_request = AsyncMock(return_value=response)
    assert await InventoryService(twitch)._fetch_completed_reward_campaigns() == set()


def test_completed_list_keeps_only_usable_completed_ids():
    entries = [None, "x", [CAMPAIGN_ID], {"id": ""}, {"id": 5, "status": "COMPLETED"},
               {"status": "COMPLETED"}, {"id": "no-status"}, {"id": "a", "status": "COMPLETED"}]
    assert InventoryService._parse_completed_reward_campaigns(_completed_response(entries)) == {"a"}


@pytest.mark.asyncio
async def test_hints_and_completed_list_load_concurrently(monkeypatch):
    end = _iso(timedelta(minutes=30))
    plain = _gql(_progress_response({"status": "IN_PROGRESS", "currentMinutesWatched": 5}),
                 windows={GROUP_ID: end})
    completed_sent = asyncio.Event()

    async def request(op):
        # The hints reply waits for the completed query, so it only arrives when both run at once.
        if "query" in op and "RewardCampaignHints" in op["query"]:
            await completed_sent.wait()
        if "query" in op and "CompletedRewardCampaigns" in op["query"]:
            completed_sent.set()
        return await plain(op)

    twitch = _twitch()
    twitch.gql_request = request
    real_wait_for = asyncio.wait_for
    monkeypatch.setattr(
        "src.services.inventory_service.asyncio.wait_for",
        lambda awaitable, timeout: real_wait_for(awaitable, {5: 1, 15: 5}[timeout]),
    )
    [campaign] = await InventoryService(twitch).fetch_reward_campaigns()
    assert campaign.window_end == end


@pytest.mark.asyncio
async def test_completed_campaign_with_a_stale_at_goal_reading_stays_built():
    # Boundary: 60/60 after its window ended reads as a window open now, so the completed list
    # does not drop it; the stale-reading ledger handles it as before.
    progress = _progress_response({"status": "IN_PROGRESS", "currentMinutesWatched": 60, "grantCount": 1})
    windows = {GROUP_ID: _iso(-timedelta(hours=1))}
    [listed] = await _load(progress, windows=windows, completed=(CAMPAIGN_ID,))
    [unlisted] = await _load(progress, windows=windows)
    assert listed.earnable_from_reading and not _drop(listed).is_claimed
    assert _drop(listed).current_minutes == _drop(unlisted).current_minutes == 0


@pytest.mark.asyncio
async def test_a_completed_campaign_leaves_the_inventory_and_the_wanted_games():
    from collections import deque

    twitch = _twitch()
    twitch._drops, twitch.inventory, twitch._mnt_triggers, twitch._mnt_task = {}, [], deque(), None
    twitch._state = State.IDLE
    twitch.gui.inv.add_campaign = AsyncMock()
    twitch._maintenance_service = SimpleNamespace(run_maintenance_task=AsyncMock())
    service = InventoryService(twitch)
    settings = _settings(GTA["displayName"], mining_benefits=dict.fromkeys(RewardPriority.TYPES, True))

    def load(completed):
        rewards = _gql(EMPTY_PROGRESS, completed=completed)

        async def request(op):
            if op is GQL_OPERATIONS["Inventory"]:
                return {"data": {"currentUser": {"inventory": {
                    "dropCampaignsInProgress": [], "gameEventDrops": []}}}}
            if isinstance(op, list) or "query" in op:
                return await rewards(op)
            return {"data": {"currentUser": {"dropCampaigns": []}}}

        twitch.gql_request = request
        return service.fetch_inventory()

    await load(())
    assert [c.id for c in twitch.inventory] == [CAMPAIGN_ID]
    assert [g.name for g in StreamSelector().get_wanted_games(settings, twitch.inventory)] == [
        GTA["displayName"]
    ]
    await load((CAMPAIGN_ID,))
    assert twitch.inventory == [] and CAMPAIGN_ID not in twitch._campaigns
    assert StreamSelector().get_wanted_games(settings, twitch.inventory) == []
    await twitch._mnt_task
