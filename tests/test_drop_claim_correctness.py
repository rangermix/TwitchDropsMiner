"""Subscription-gated drops, claims missed by the websocket, and duplicate claim announcements."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.models.campaign import DropsCampaign
from src.web.managers.inventory import InventoryManager


CLAIMED = {"data": {"claimDropRewards": {"status": "ELIGIBLE_FOR_ALL"}}}


def _twitch():
    twitch = SimpleNamespace(
        settings=SimpleNamespace(
            allow_unlinked_campaigns=False, drop_name_blacklist=[], telegram_bot_token="",
            telegram_chat_id="", discord_webhook_url="",
        ),
        gui=MagicMock(), print=MagicMock(), drop_history=MagicMock(),
        request_games_update=MagicMock(), claimed_drop_ids=set(),
        get_auth=AsyncMock(return_value=SimpleNamespace(user_id="42")),
        gql_request=AsyncMock(return_value=CLAIMED),
    )
    twitch.gui.broadcast_wanted_items_now = AsyncMock()
    return twitch


def _drop(drop_id, *, minutes=0, subs=None, instance=None):
    data = {
        "id": drop_id, "name": drop_id,
        "benefitEdges": [{"benefit": {
            "id": f"{drop_id}-b", "name": f"Reward {drop_id}",
            "distributionType": "DIRECT_ENTITLEMENT", "imageAssetURL": "https://example.test/r.png",
        }}],
        "startAt": "2000-01-01T00:00:00Z", "endAt": "2099-01-01T00:00:00Z",
        "preconditionDrops": [], "requiredMinutesWatched": 60,
        "self": {"dropInstanceID": instance, "isClaimed": False, "currentMinutesWatched": minutes},
    }
    if subs is not None:
        data["requiredSubs"] = subs
    return data


def _campaign(twitch, *drops, linked=True):
    return DropsCampaign(twitch, {
        "id": "camp", "name": "Campaign", "game": {"id": "1", "name": "Game"},
        "self": {"isAccountConnected": linked}, "accountLinkURL": "https://example.test/link",
        "startAt": "2000-01-01T00:00:00Z", "endAt": "2099-01-01T00:00:00Z", "status": "ACTIVE",
        "allow": {"channels": [], "isEnabled": True}, "timeBasedDrops": list(drops),
    }, {})


def test_subscription_gated_drop_is_not_mined_or_counted():
    twitch = _twitch()
    campaign = _campaign(twitch, _drop("watch"), _drop("sub", subs=1))
    watch, sub = campaign.timed_drops["watch"], campaign.timed_drops["sub"]
    assert sub.required_subs == 1 and not sub.is_watch_drop and not sub.can_earn()
    assert watch.is_watch_drop and watch.can_earn()
    assert campaign.total_drops == 1
    watch.is_claimed = True
    assert campaign.finished
    assert not campaign.can_earn_within(campaign.ends_at)


@pytest.mark.parametrize("value", [0, None, "1", True])
def test_missing_or_malformed_required_subs_keeps_a_watch_drop(value):
    campaign = _campaign(_twitch(), _drop("watch", subs=value))
    assert campaign.timed_drops["watch"].is_watch_drop


def test_subscription_gated_campaign_is_hidden_from_inventory():
    campaign = _campaign(_twitch(), _drop("sub", subs=1))
    manager = InventoryManager(MagicMock(), MagicMock())
    assert manager._serialize_campaign(campaign) is None


@pytest.mark.asyncio
async def test_twitch_reported_completion_is_claimed_without_the_claim_event():
    twitch = _twitch()
    campaign = _campaign(twitch, _drop("drop", minutes=50))
    drop = campaign.timed_drops["drop"]
    drop.report_twitch_minutes(60)
    assert drop.completion_reported and not drop.can_earn()
    twitch.request_games_update.assert_called_once()
    # GAMES_UPDATE: build the claim ID the missed websocket event would have carried.
    assert drop.claim_id is None and not drop.can_claim
    await drop.generate_claim()
    assert drop.claim_id == "42#camp#drop" and drop.can_claim
    assert await drop.claim() and drop.is_claimed and not drop.completion_reported


def test_sibling_minutes_and_partial_readings_do_not_report_completion():
    twitch = _twitch()
    campaign = _campaign(twitch, _drop("drop", minutes=50), _drop("sibling", minutes=50))
    drop, sibling = campaign.timed_drops["drop"], campaign.timed_drops["sibling"]
    drop.report_twitch_minutes(59)
    assert not drop.completion_reported
    drop.report_twitch_minutes(60)  # the campaign-wide delta also moves the sibling to 60
    assert sibling.real_current_minutes == 60
    assert not sibling.completion_reported and sibling.can_earn()


def test_completion_on_an_unlinked_campaign_still_waits_for_linking():
    twitch = _twitch()
    campaign = _campaign(twitch, _drop("drop", minutes=50), linked=False)
    twitch.settings.allow_unlinked_campaigns = True
    drop = campaign.timed_drops["drop"]
    drop.report_twitch_minutes(60)
    assert drop.awaiting_account_link and not drop.can_claim


def test_inventory_reading_at_full_time_reports_completion():
    campaign = _campaign(_twitch(), _drop("drop", minutes=60))
    assert campaign.timed_drops["drop"].completion_reported


@pytest.mark.asyncio
async def test_a_reloaded_claimed_drop_is_not_claimed_or_announced_again():
    twitch = _twitch()
    first = _campaign(twitch, _drop("drop", minutes=60, instance="claim-1")).timed_drops["drop"]
    assert await first.claim()
    # The reload right after the claim still lists it as unclaimed.
    reloaded = _campaign(twitch, _drop("drop", minutes=60, instance="claim-1")).timed_drops["drop"]
    assert await reloaded.claim() and reloaded.is_claimed
    assert twitch.gql_request.await_count == 1
    assert twitch.print.call_count == 1
    twitch.drop_history.record.assert_called_once()


@pytest.mark.asyncio
async def test_a_failed_claim_is_not_remembered():
    twitch = _twitch()
    twitch.gql_request = AsyncMock(return_value={"data": {"claimDropRewards": None}})
    drop = _campaign(twitch, _drop("drop", minutes=60, instance="claim-1")).timed_drops["drop"]
    assert not await drop.claim()
    assert twitch.claimed_drop_ids == set()
