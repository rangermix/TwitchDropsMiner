"""Watch-based sitewide Open Reward Campaigns are mined through the regular drop model."""

import asyncio
import json
from contextlib import suppress
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.config import GQL_OPERATIONS, MAX_EXTRA_MINUTES, State
from src.exceptions import ExitRequest, GQLException
from src.models import DropsCampaign, RewardCampaign
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


def _gql(progress, *, campaigns=None, windows=NO_WINDOWS, badges=()):
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
        return campaigns if campaigns is not None else {"data": {"rewardCampaignsAvailableToUser": [_data()]}}
    return AsyncMock(side_effect=request)


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
async def test_refresh_apply_failure_returns_false_and_exit_propagates():
    twitch = _twitch()
    campaign = _build(twitch=twitch)
    twitch._campaigns[campaign.id] = campaign
    twitch.gql_request = _gql(_progress_response({"status": "IN_PROGRESS", "currentMinutesWatched": 5}))
    service = InventoryService(twitch)
    for error, outcome in ((ValueError("x"), False), (ExitRequest(), ExitRequest)):
        with patch.object(RewardCampaign, "apply_reward_progress", side_effect=error):
            if outcome is False:
                assert await service.refresh_reward_progress([campaign], _channel(twitch)) is False
            else:
                with pytest.raises(ExitRequest):
                    await service.refresh_reward_progress([campaign], _channel(twitch))


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
    for campaign, reward in zip(client.inventory, campaigns):
        campaign.configure_mock(
            game=game, can_earn_within=lambda stamp: True, allowed_channels=[],
            can_earn=lambda channel: True, skips_drops_flag=reward,
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
    ([True], [True, False]),
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
    assert serialized["drops"][0]["benefits"] == []
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


# Daily watch goal and lane campaigns, shaped like the public snapshot
DVD, IGUANA, SUB = GROUP_ID, "c7e4cd08-0000-0000-0000-000000000001", "sub-1"


def _gtavi(order=(DVD, IGUANA)):
    groups = {
        DVD: {**_group(DVD), "rewards": [{"id": "dvd", "name": "GTA DVD Chest", "thumbnailImage": None}]},
        IGUANA: {**_group(IGUANA), "rewards": [{"id": "ig", "name": "Golden Iguana", "thumbnailImage": None}]},
        SUB: _group(SUB, goal=0, subs=1),
    }
    return _data([groups[key] for key in (*order, SUB)])


def _pokemon(lanes=3):
    return _data([_group(f"lane{n}", goal=20, categories=(GTA, OTHER)) for n in range(lanes)])


def _pick(data, progress=None, windows=None):
    return RewardCampaign.from_reward_campaign(_twitch(), data, progress or {}, "uid", windows or {})


def test_started_group_is_picked():
    campaign = _pick(_gtavi(), {DVD: {"status": "IN_PROGRESS", "currentMinutesWatched": 54}})
    assert list(campaign.timed_drops) == [DVD] and _drop(campaign).current_minutes == 54


def test_without_progress_the_first_watch_goal_is_kept():
    assert list(_pick(_gtavi()).timed_drops) == [DVD]
    assert list(_pick(_gtavi((IGUANA, DVD))).timed_drops) == [IGUANA]
    assert list(_pick(_pokemon()).timed_drops) == ["lane0"]


def test_unstarted_first_lane_of_evolutions_is_mined():
    live = {"lane0": {"status": "IN_PROGRESS", "currentMinutesWatched": 0, "grantCount": 0, "nextEligibleAt": None}}
    assert list(_pick(_pokemon(), live).timed_drops) == ["lane0"]


def test_finished_final_reward_falls_back_to_the_first_goal():
    done = {IGUANA: {"status": "FULFILLED", "nextEligibleAt": None}}
    assert list(_pick(_gtavi(), done).timed_drops) == [DVD]


def test_granted_daily_goal_yields_to_the_goal_in_progress():
    progress = {
        DVD: {"status": "CLAIMED", "nextEligibleAt": None},
        IGUANA: {"status": "IN_PROGRESS", "currentMinutesWatched": 3},
    }
    assert list(_pick(_gtavi(), progress).timed_drops) == [IGUANA]


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
    batch, hints = (call.args[0] for call in twitch.gql_request.await_args_list)
    assert [op["query"].split()[1] for op in batch] == ["RewardCampaignProgress", "RewardCampaigns"]
    assert "RewardCampaignHints" in hints["query"] and twitch.gql_request.await_count == 2


# Goals that cannot be finished before the campaign ends
def _state(progress, goal=60, *, ends_in):
    return RewardCampaign.reward_state(progress, goal, datetime.now(timezone.utc) + ends_in)


def test_next_window_is_checked_from_now_not_from_a_past_wait_time():
    # The wait time passed an hour ago, so a full goal no longer fits in the 30 minutes left.
    past = {"status": "CLAIMED", "nextEligibleAt": _iso(-timedelta(hours=1))}
    assert _state(past, ends_in=timedelta(minutes=30)) == (60, True, None)
    assert _state(past, ends_in=timedelta(minutes=90)) == (0, False, None)


@pytest.mark.parametrize(("minutes", "ends_in", "expected"), [
    (30, timedelta(minutes=29), (60, True, None)),
    (30, timedelta(minutes=31), (30, False, None)),
    (59, timedelta(seconds=30), (60, True, None)),
    (59, timedelta(minutes=2), (59, False, None)),
])
def test_reading_that_cannot_reach_the_goal_before_the_end_is_finished(minutes, ends_in, expected):
    reading = {"status": "IN_PROGRESS", "currentMinutesWatched": minutes}
    assert _state(reading, ends_in=ends_in) == expected


def test_unstarted_goal_that_no_longer_fits_is_finished():
    assert _state(None, ends_in=timedelta(minutes=59)) == (60, True, None)
    assert _state(None, ends_in=timedelta(minutes=61)) == (0, False, None)
