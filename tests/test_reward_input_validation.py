"""Invalid reward metadata is isolated before it can affect the shared inventory."""

from datetime import datetime, timedelta, timezone

import pytest

from src.models import RewardCampaign
from src.models.reward_campaign import NO_READING, RewardLedger
from src.services.inventory_service import InventoryService
from src.web.managers.inventory import InventoryManager
from tests.test_reward_campaigns import (
    EMPTY_PROGRESS,
    GROUP_ID,
    _channel,
    _data,
    _gql,
    _group,
    _iso,
    _twitch,
)


@pytest.mark.asyncio
@pytest.mark.parametrize("override", [
    {"id": []}, {"id": " "}, {"name": None}, {"name": ""},
    {"startsAt": "2025-01-01"}, {"endsAt": "2028-01-01"},
    {"startsAt": "9999-12-31T24:00Z"}, {"endsAt": "9999-12-31T23:59:59Z"},
    {"endsAt": "0001-01-01T00:00:00+12:00"}, {"startsAt": _iso(timedelta(days=20))},
    {"isSitewide": "false"},
])
async def test_invalid_campaign_does_not_enter_shared_inventory(override):
    twitch = _twitch()
    valid = {**_data(), "id": "valid"}
    malformed = {**_data(), **override}
    twitch.gql_request = _gql(EMPTY_PROGRESS, campaigns={
        "data": {"rewardCampaignsAvailableToUser": [malformed, valid]},
    })
    campaigns = await InventoryService(twitch).fetch_reward_campaigns()
    assert [c.id for c in campaigns] == ["valid"]
    assert campaigns[0].active
    assert campaigns[0].mineable_drop_ids == {GROUP_ID}


@pytest.mark.parametrize("override", [
    {"id": ""},
    {"categories": [{"id": 1, "displayName": {"x": 1}, "name": "valid"}]},
    {"categories": [{"id": 1, "displayName": " "}]},
    {"categories": [{"id": 1, "displayName": "GTA", "slug": []}]},
    {"unlockRequirements": {"minuteWatchedGoal": 10**30}},
    {"unlockRequirements": {"minuteWatchedGoal": 60, "subsGoal": "1"}},
    {"rewards": [{"id": [], "name": "X"}]},
    {"rewards": [{"id": "x", "name": "X", "thumbnailImage": {"image1xURL": []}}]},
])
def test_invalid_lane_does_not_mask_usable_lane(override):
    bad = {**_group("invalid"), **override}
    campaign = RewardCampaign.from_reward_campaign(_twitch(), _data([bad, _group()]), {}, "uid")
    assert list(campaign.timed_drops) == [GROUP_ID]


def test_overflowing_window_does_not_mask_usable_lane():
    bad = _group("bad")
    readings = {"bad": {"status": "CLAIMED", "nextEligibleAt": "9999-12-31T24:00Z"}}
    campaign = RewardCampaign.from_reward_campaign(_twitch(), _data([bad, _group()]), readings, "uid")
    assert list(campaign.timed_drops) == [GROUP_ID]


@pytest.mark.parametrize(("reading", "mineable", "claimed"), [
    (None, False, False),
    ({"status": "IN_PROGRESS", "currentMinutesWatched": 30}, False, False),
    ({"status": "IN_PROGRESS", "currentMinutesWatched": 55}, True, False),
    ({"status": "CLAIMABLE"}, True, False),
    ({"status": "FULFILLED"}, False, True),
    ({"status": "FULFILLED", "nextEligibleAt": _iso(timedelta(minutes=5))}, False, True),
])
def test_available_watch_time_starts_at_campaign_open(reading, mineable, claimed):
    data = {**_data(), "startsAt": _iso(timedelta(minutes=50)), "endsAt": _iso(timedelta(minutes=70))}
    readings = {GROUP_ID: reading} if reading else {}
    campaign = RewardCampaign.from_reward_campaign(_twitch(), data, readings, "uid")
    drop = campaign.timed_drops[GROUP_ID]
    assert drop.is_mineable is mineable
    assert drop.is_claimed is claimed


def test_started_unfinishable_lane_yields_to_a_finishable_unstarted_lane():
    data = {**_data([_group("long", goal=120), _group("short", goal=15)]),
            "endsAt": _iso(timedelta(minutes=30))}
    readings = {"long": {"status": "IN_PROGRESS", "currentMinutesWatched": 1}}
    campaign = RewardCampaign.from_reward_campaign(_twitch(), data, readings, "uid")
    assert campaign.mineable_drop_ids == {"short"}


def test_a_held_unstarted_lane_survives_reordered_groups():
    ledger = RewardLedger()
    retry_at = datetime.now(timezone.utc) + timedelta(hours=1)
    ledger.uncredited["held"] = (NO_READING, retry_at, True)
    data = _data([_group("other"), _group("held")])
    campaign = RewardCampaign.from_reward_campaign(_twitch(), data, {}, "uid", ledger=ledger)
    assert list(campaign.timed_drops) == ["held"]
    assert campaign.timed_drops["held"].starts_at == retry_at


@pytest.mark.parametrize("claim_first", [True, False])
def test_a_claimable_lane_precedes_a_watchable_lane(claim_first):
    groups = [_group("claim"), _group("watch")]
    if not claim_first:
        groups.reverse()
    readings = {"claim": {"status": "CLAIMABLE"},
                "watch": {"status": "IN_PROGRESS", "currentMinutesWatched": 1}}
    campaign = RewardCampaign.from_reward_campaign(_twitch(), _data(groups), readings, "uid")
    assert list(campaign.timed_drops) == ["claim"]
    assert campaign.timed_drops["claim"].can_claim


def test_ignore_names_cover_each_reward_and_event_without_cross_name_matches():
    twitch = _twitch()
    group = _group()
    group["rewards"].append({"id": "bonus", "name": "Bonus Badge"})
    campaign = RewardCampaign.from_reward_campaign(twitch, _data([group]), {}, "uid")
    drop = campaign.timed_drops[GROUP_ID]
    channel = _channel(twitch)
    assert drop.name == campaign.name
    for keyword in ("dvd chest", "BONUS", "Launch Celebration"):
        twitch.settings.drop_name_blacklist = [keyword]
        assert drop.ignore_reason.detail == keyword
        assert not campaign.can_earn(channel)
    twitch.settings.drop_name_blacklist = ["chest bonus"]
    assert campaign.can_earn(channel)
    assert not InventoryManager._serialize_drop(drop)["is_ignored"]
    twitch.settings.drop_name_blacklist = ["badge", "chest"]
    assert drop.ignore_reason.detail == "badge"
