"""CurrentDrop responses must not overwrite observations received while polling."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.exceptions import GQLException
from src.models.campaign import DropsCampaign
from src.services.watch_service import WatchService
from src.utils.async_helpers import AwaitableValue


def make_campaign(twitch, campaign_id="campaign", minutes=10):
    return DropsCampaign(twitch, {
        "id": campaign_id,
        "name": "Campaign",
        "game": {"id": "1", "name": "Game"},
        "self": {"isAccountConnected": True},
        "accountLinkURL": "https://example.test/link",
        "startAt": "2000-01-01T00:00:00Z",
        "endAt": "2099-01-01T00:00:00Z",
        "status": "ACTIVE",
        "allow": {"channels": [], "isEnabled": True},
        "timeBasedDrops": [{
            "id": drop_id,
            "name": drop_id,
            "benefitEdges": [{"benefit": {
                "id": drop_id + "-benefit", "name": "Benefit",
                "distributionType": "DIRECT_ENTITLEMENT",
                "imageAssetURL": "https://example.test/reward.png",
            }}],
            "startAt": "2000-01-01T00:00:00Z",
            "endAt": "2099-01-01T00:00:00Z",
            "preconditionDrops": [],
            "requiredMinutesWatched": 60,
            "self": {
                "dropInstanceID": "claim-" + drop_id,
                "isClaimed": False, "currentMinutesWatched": minutes,
            },
        } for drop_id in ("drop", "sibling")],
    }, {})


@pytest.fixture
def mining_state():
    channel = MagicMock()
    channel.id = 123
    channel.online = True
    channel.banned = False
    channel.drops_enabled = True
    channel.send_watch = AsyncMock(return_value=True)
    selected = AwaitableValue()
    selected.set(channel)
    twitch = SimpleNamespace(
        settings=SimpleNamespace(
            drop_name_blacklist=[], telegram_bot_token="", telegram_chat_id="",
            discord_webhook_url="",
        ),
        gui=MagicMock(),
        is_mining_time=lambda: True,
        watching_channel=selected,
        request_games_update=MagicMock(),
        print=MagicMock(),
        drop_history=MagicMock(),
    )
    twitch.gui.broadcast_wanted_items_now = AsyncMock()
    campaign = make_campaign(twitch)
    channel.game = campaign.game
    twitch._drops = dict(campaign.timed_drops)
    twitch._inventory_service = SimpleNamespace(get_active_campaign=lambda watched: campaign)
    return twitch, channel, campaign


async def poll(twitch, on_request, *, until=30, returned_minutes=10, response="session"):
    clock = [0.0]
    requests = []

    async def gql_request(operation):
        requests.append(clock[0])
        await on_request(len(requests))
        if response == "error":
            raise GQLException("Test query failure")
        if response == "timeout":
            raise TimeoutError
        return {"data": {"currentUser": {"dropCurrentSession": {
            "dropID": "drop", "currentMinutesWatched": returned_minutes,
        } if response == "session" else None}}}

    async def advance(delay):
        clock[0] += delay
        if clock[0] >= until:
            raise asyncio.CancelledError

    twitch.gql_request = gql_request
    service = WatchService(twitch)
    with (
        patch("src.services.watch_service.monotonic", side_effect=lambda: clock[0]),
        patch.object(service, "watch_sleep", side_effect=advance),
        pytest.raises(asyncio.CancelledError),
    ):
        await service.watch_loop()
    return requests


@pytest.mark.asyncio
@pytest.mark.parametrize("source", ["drop", "sibling"])
@pytest.mark.parametrize("minutes", [9, 10, 11])
async def test_newer_observation_wins_even_without_a_value_change(mining_state, source, minutes):
    twitch, channel, campaign = mining_state

    async def observe(request):
        campaign.timed_drops[source].update_minutes(minutes)
        await asyncio.sleep(0)

    assert await poll(twitch, observe, returned_minutes=9 if minutes == 10 else 10) == [20]
    assert [drop.real_current_minutes for drop in campaign.drops] == [minutes, minutes]
    assert all(drop.extra_current_minutes == 0 for drop in campaign.drops)


@pytest.mark.asyncio
async def test_fresh_poll_can_still_correct_progress_downward(mining_state):
    twitch, channel, campaign = mining_state
    campaign.timed_drops["drop"].update_minutes(11)

    async def observe(request):
        await asyncio.sleep(0)

    assert await poll(twitch, observe) == [20]
    assert [drop.real_current_minutes for drop in campaign.drops] == [10, 10]
    assert all(drop.extra_current_minutes == 0 for drop in campaign.drops)


@pytest.mark.asyncio
async def test_discarding_stale_poll_does_not_disable_the_next_poll(mining_state):
    twitch, channel, campaign = mining_state

    async def observe(request):
        if request == 1:
            campaign.timed_drops["drop"].update_minutes(11)
        await asyncio.sleep(0)

    assert await poll(twitch, observe, until=90) == [20, 80]
    assert [drop.real_current_minutes for drop in campaign.drops] == [10, 10]
    assert all(drop.extra_current_minutes == 0 for drop in campaign.drops)


@pytest.mark.asyncio
async def test_inventory_replacement_rejects_old_poll_without_estimating(mining_state):
    twitch, channel, campaign = mining_state
    replacement = make_campaign(twitch, minutes=12)

    async def observe(request):
        twitch._drops = dict(replacement.timed_drops)
        await asyncio.sleep(0)

    assert await poll(twitch, observe) == [20]
    assert [drop.real_current_minutes for drop in replacement.drops] == [12, 12]
    assert all(drop.extra_current_minutes == 0 for drop in campaign.drops)
    assert all(drop.extra_current_minutes == 0 for drop in replacement.drops)


@pytest.mark.asyncio
async def test_removed_drop_rejects_old_poll_without_estimating(mining_state):
    twitch, channel, campaign = mining_state

    async def observe(request):
        del twitch._drops["drop"]
        await asyncio.sleep(0)

    assert await poll(twitch, observe) == [20]
    assert all(drop.real_current_minutes == 10 for drop in campaign.drops)
    assert all(drop.extra_current_minutes == 0 for drop in campaign.drops)


@pytest.mark.asyncio
async def test_other_campaign_observation_does_not_suppress_fresh_poll(mining_state):
    twitch, channel, campaign = mining_state
    other = make_campaign(twitch, campaign_id="other")
    campaign.timed_drops["drop"].update_minutes(11)

    async def observe(request):
        other.timed_drops["drop"].update_minutes(12)
        await asyncio.sleep(0)

    assert await poll(twitch, observe) == [20]
    assert [drop.real_current_minutes for drop in campaign.drops] == [10, 10]
    assert [drop.real_current_minutes for drop in other.drops] == [12, 12]


@pytest.mark.asyncio
@pytest.mark.parametrize("claim_succeeds", [True, False])
async def test_sibling_claim_during_poll(mining_state, claim_succeeds):
    twitch, channel, campaign = mining_state
    target = campaign.timed_drops["drop"]
    sibling = campaign.timed_drops["sibling"]
    target.update_minutes(11)
    sibling._claim = AsyncMock(return_value=claim_succeeds)

    async def observe(request):
        await sibling.claim()

    assert await poll(twitch, observe) == [20]
    assert target.real_current_minutes == (11 if claim_succeeds else 10)
    assert sibling.real_current_minutes == (60 if claim_succeeds else 10)
    assert sibling.is_claimed is claim_succeeds
    assert all(drop.extra_current_minutes == 0 for drop in campaign.drops)


@pytest.mark.asyncio
async def test_claim_supersedes_poll_before_notifications_finish(mining_state):
    twitch, channel, campaign = mining_state
    target = campaign.timed_drops["drop"]
    sibling = campaign.timed_drops["sibling"]
    target.update_minutes(11)
    sibling._claim = AsyncMock(return_value=True)
    entered, release = asyncio.Event(), asyncio.Event()
    tasks = []

    async def broadcast():
        entered.set()
        await release.wait()

    twitch.gui.broadcast_wanted_items_now = broadcast

    async def observe(request):
        tasks.append(asyncio.create_task(sibling.claim()))
        await entered.wait()
        assert sibling.is_claimed
        assert not tasks[0].done()

    try:
        assert await poll(twitch, observe) == [20]
        assert target.real_current_minutes == 11
        assert all(drop.extra_current_minutes == 0 for drop in campaign.drops)
    finally:
        release.set()
        await asyncio.gather(*tasks)
    assert sibling.real_current_minutes == 60


@pytest.mark.asyncio
async def test_repeated_claim_does_not_suppress_fresh_poll(mining_state):
    twitch, channel, campaign = mining_state
    target = campaign.timed_drops["drop"]
    sibling = campaign.timed_drops["sibling"]
    target.update_minutes(11)
    sibling._claim = AsyncMock(return_value=True)
    assert await sibling.claim()
    revision = campaign.progress_revision

    async def observe(request):
        assert await sibling.claim()
        assert campaign.progress_revision == revision

    assert await poll(twitch, observe) == [20]
    assert target.real_current_minutes == 10
    assert sibling.real_current_minutes == 60


@pytest.mark.asyncio
@pytest.mark.parametrize("response", ["null", "error", "timeout"])
@pytest.mark.parametrize("change", ["none", "observation", "unchanged", "replacement", "claim"])
async def test_fallback_uses_only_unchanged_campaign_state(mining_state, response, change):
    twitch, channel, campaign = mining_state
    current = [campaign]
    twitch._inventory_service.get_active_campaign = lambda watched: current[0]

    async def observe(request):
        if change == "observation":
            campaign.timed_drops["drop"].update_minutes(11)
        elif change == "unchanged":
            campaign.timed_drops["sibling"].update_minutes(10)
        elif change == "replacement":
            current[0] = make_campaign(twitch, minutes=12)
            twitch._drops = dict(current[0].timed_drops)
        elif change == "claim":
            sibling = campaign.timed_drops["sibling"]
            sibling._claim = AsyncMock(return_value=True)
            assert await sibling.claim()
        await asyncio.sleep(0)

    assert await poll(twitch, observe, response=response) == [20]
    expected = 11 if change == "observation" else 12 if change == "replacement" else 10
    assert current[0].timed_drops["drop"].real_current_minutes == expected
    assert all(drop.extra_current_minutes == (1 if change == "none" else 0)
               for drop in current[0].drops)
