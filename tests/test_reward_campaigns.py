"""Watch-based sitewide Open Reward Campaigns are mined through the regular drop model."""

import asyncio
import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.config import State
from src.exceptions import ExitRequest, GQLException
from src.models import DropsCampaign
from src.models.channel import Channel, Stream
from src.models.game import Game
from src.services.inventory_service import InventoryService
from src.services.watch_service import WatchService
from src.utils.async_helpers import AwaitableValue
from src.web.managers.inventory import InventoryManager
from tests.test_watched_channel_recovery import OFFLINE, _reloading_miner


GTA = {"id": "1867279146", "displayName": "Grand Theft Auto VI", "slug": "grand-theft-auto-vi"}
OTHER = {"id": "7", "name": "Other Game"}
CAMPAIGN_ID = "b7669f9e-c69c-4a9b-a3b2-36266c1bdfc0"
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
    )
    twitch.gui.broadcast_wanted_items_now = AsyncMock()
    return twitch


def _build(self_data=None, *, data=None, twitch=None):
    progress = {} if self_data is None else {GROUP_ID: self_data}
    return DropsCampaign.from_reward_campaign(twitch or _twitch(), data or _data(), progress, "uid")


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


def _regular(twitch):
    return DropsCampaign(twitch, {
        "id": "regular", "name": "Regular", "game": OTHER, "status": "ACTIVE",
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


def _gql(progress, *, claim=None, campaigns=None):
    """Answer by operation: raw reward queries and the persisted ClaimDrop."""
    async def request(op):
        if "operationName" in op:
            assert op["operationName"] == "DropsPage_ClaimDropRewards"
            return claim or {"data": {"claimDropRewards": {"status": "ELIGIBLE_FOR_ALL"}}}
        if "RewardCampaignProgress" in op["query"]:
            return progress
        return campaigns if campaigns is not None else {"data": {"rewardCampaignsAvailableToUser": [_data()]}}
    return AsyncMock(side_effect=request)


def _claims(twitch):
    return [c for c in twitch.gql_request.await_args_list if "operationName" in c.args[0]]


# 1
def test_unstarted_group_becomes_one_earnable_drop():
    twitch = _twitch()
    campaign = _build(twitch=twitch)
    drop = _drop(campaign)
    assert campaign.id == CAMPAIGN_ID and campaign.is_reward
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
    assert await InventoryService(twitch).refresh_reward_progress(campaign) is True
    assert (drop.real_current_minutes, drop.extra_current_minutes) == (20, 0)


@pytest.mark.asyncio
async def test_refresh_claims_with_synthetic_id_once():
    twitch = _twitch()
    campaign = _build(twitch=twitch)
    twitch._campaigns[campaign.id] = campaign
    twitch.gql_request = _gql(_progress_response({"status": "CLAIMABLE", "currentMinutesWatched": 60}))
    service = InventoryService(twitch)
    assert await service.refresh_reward_progress(campaign) is True
    assert _drop(campaign).is_claimed
    (claim,) = _claims(twitch)
    assert claim.args[0]["variables"] == {"input": {"dropInstanceID": CLAIM_ID}}
    # Twitch may still report CLAIMABLE on the next tick; no second claim is sent.
    assert await service.refresh_reward_progress(campaign) is True
    assert len(_claims(twitch)) == 1


@pytest.mark.asyncio
async def test_missing_claim_status_counts_only_for_reward_drops():
    twitch = _twitch()
    reply = {"data": {"claimDropRewards": {"dropType": {"id": "x"}}}}
    twitch.gql_request = _gql(None, claim=reply)
    reward = _drop(_build({"status": "CLAIMABLE", "currentMinutesWatched": 60}, twitch=twitch))
    assert await reward._claim() is True
    regular = _regular(twitch).timed_drops["d1"]
    await regular.generate_claim()
    assert await regular._claim() is False


@pytest.mark.asyncio
async def test_refresh_finished_window_requests_inventory_reload():
    twitch = _twitch()
    campaign = _build(twitch=twitch)
    twitch._campaigns[campaign.id] = campaign
    twitch.gql_request = _gql(_progress_response(
        {"status": "CLAIMED", "nextEligibleAt": _iso(timedelta(hours=3))}))
    assert await InventoryService(twitch).refresh_reward_progress(campaign) is True
    twitch.request_inventory_refresh.assert_called_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("error", [GQLException("x"), KeyError("data"), TimeoutError()])
async def test_refresh_failure_returns_false(error):
    twitch = _twitch()
    campaign = _build(twitch=twitch)
    twitch._campaigns[campaign.id] = campaign
    twitch.gql_request = AsyncMock(side_effect=error)
    assert await InventoryService(twitch).refresh_reward_progress(campaign) is False


@pytest.mark.asyncio
@pytest.mark.parametrize("stale", ["revision", "replaced"])
async def test_refresh_discards_stale_reply(stale):
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
    assert await InventoryService(twitch).refresh_reward_progress(campaign) is True
    assert drop.current_minutes == 0


# 12
@pytest.mark.asyncio
@pytest.mark.parametrize("refreshed", [True, False])
async def test_watch_loop_replaces_current_drop_for_reward_campaign(refreshed):
    clock = [0.0]
    current_drop_calls = []
    channel = MagicMock(online=True, banned=False, id=1)
    channel.send_watch = AsyncMock(return_value=True)
    selected = AwaitableValue()
    selected.set(channel)
    campaign = MagicMock(is_reward=True)

    async def gql_request(op):
        current_drop_calls.append(op)
        return {"data": {"currentUser": {"dropCurrentSession": None}}}

    twitch = SimpleNamespace(
        is_mining_time=lambda: True, watching_channel=selected, gui=MagicMock(),
        gql_request=gql_request, _drops={},
        _inventory_service=SimpleNamespace(
            get_active_campaign=lambda watched: campaign,
            refresh_reward_progress=AsyncMock(return_value=refreshed),
        ),
    )
    service = WatchService(twitch)

    async def advance(delay):
        clock[0] += delay
        if clock[0] >= 40:
            raise asyncio.CancelledError

    with (
        patch("src.services.watch_service.monotonic", side_effect=lambda: clock[0]),
        patch.object(service, "watch_sleep", side_effect=advance),
        pytest.raises(asyncio.CancelledError),
    ):
        await service.watch_loop()

    twitch._inventory_service.refresh_reward_progress.assert_awaited_with(campaign)
    if refreshed:
        assert not current_drop_calls
        campaign.bump_minutes.assert_not_called()
    else:
        assert current_drop_calls
        campaign.bump_minutes.assert_called()


# 13
@pytest.mark.asyncio
@pytest.mark.parametrize("reward", [True, False])
async def test_channels_fetch_ignores_drops_flag_for_reward_game(tmp_path, monkeypatch, reward):
    client, _watched = _reloading_miner(tmp_path, monkeypatch, discovered=[2], stream_status=[OFFLINE])
    game = Game(GTA if reward else OTHER)
    client.wanted_games = [game]
    client.inventory = [SimpleNamespace(
        game=game, can_earn_within=lambda stamp: True, allowed_channels=[],
        can_earn=lambda channel: True, is_reward=reward, skips_drops_flag=reward,
    )]
    try:
        await asyncio.wait_for(client._run(), 1)
    finally:
        if client._watching_task is not None:
            client._watching_task.cancel()
    assert client.get_live_streams.await_args.kwargs["drops_enabled"] is (not reward)
    assert client._state is State.EXIT


# Dashboard
@pytest.mark.asyncio
async def test_dashboard_serializes_reward_campaign_without_images_or_link():
    campaign = _build({"status": "IN_PROGRESS", "currentMinutesWatched": 12})
    manager = InventoryManager(MagicMock(), MagicMock())
    serialized = manager._serialize_campaign(campaign)
    json.dumps(serialized)
    assert serialized["link_url"] == "" and serialized["game_name"] == "Grand Theft Auto VI"
    assert serialized["drops"][0]["benefits"] == []
    assert serialized["drops"][0]["current_minutes"] == 12
