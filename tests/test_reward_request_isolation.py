"""Failures and cancellation in optional reward queries stay isolated."""

import asyncio
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock

import aiohttp
import pytest

from src.exceptions import ExitRequest, MinerException
from src.models import RewardCampaign
from src.services.inventory_service import InventoryService
from src.services.watch_service import WatchService
from src.utils.async_helpers import AwaitableValue
from tests.test_reward_campaigns import (
    EMPTY_PROGRESS,
    GROUP_ID,
    GTA,
    _build,
    _channel,
    _channels_fetch_client,
    _data,
    _drop,
    _group,
    _gql,
    _progress_response,
    _regular,
    _run_client,
    _twitch,
)
from tests.test_special_game_watch import CHANNEL_ID, SPECIAL_EVENTS, _campaign


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", [MinerException(), aiohttp.ClientError(), KeyError("streams"), "stall"])
async def test_optional_directory_failure_keeps_regular_channel(tmp_path, monkeypatch, failure):
    client, _ = _channels_fetch_client(tmp_path, monkeypatch, [False, True])
    regular_lookup = client.get_live_streams
    lookups = []
    monkeypatch.setattr("src.core.client.REWARD_DIRECTORY_TIMEOUT", 0.01)

    async def directory(game, *, drops_enabled):
        lookups.append(drops_enabled)
        if drops_enabled:
            return await regular_lookup(game, drops_enabled=True)
        if failure == "stall":
            await asyncio.Event().wait()
        raise failure

    client.get_live_streams = directory
    await _run_client(client)
    assert lookups == [True, False]
    assert client.watching_channel.get_with_default(None) is client.channels[2]
    assert client.channels[2].drops_enabled


@pytest.mark.asyncio
@pytest.mark.parametrize("stop", [ExitRequest, asyncio.CancelledError])
async def test_optional_directory_preserves_shutdown(tmp_path, monkeypatch, stop):
    client, _ = _channels_fetch_client(tmp_path, monkeypatch, [True])
    client.get_live_streams = AsyncMock(side_effect=stop())
    with pytest.raises(stop):
        await _run_client(client)


@pytest.mark.asyncio
async def test_rejected_reward_query_drains_sibling_before_exit():
    twitch = _twitch()
    twitch.gql_request = _gql(EMPTY_PROGRESS)
    service = InventoryService(twitch)
    running = asyncio.Event()
    stopped = asyncio.Event()

    async def slow_query():
        try:
            running.set()
            await asyncio.Event().wait()
        finally:
            stopped.set()

    async def request_exit():
        await running.wait()
        raise ExitRequest()

    service._fetch_reward_hints = slow_query
    service._fetch_completed_reward_campaigns = request_exit
    with pytest.raises(ExitRequest):
        await service.fetch_reward_campaigns()
    assert stopped.is_set()


@pytest.mark.asyncio
async def test_switch_during_first_announcement_discards_rest_of_batch():
    twitch = _twitch()
    first = _build({"status": "IN_PROGRESS", "currentMinutesWatched": 1, "grantCount": 0}, twitch=twitch)
    second = _build(twitch=twitch, data={**_data([_group("other-group")]), "id": "second"})
    twitch._campaigns = {campaign.id: campaign for campaign in (first, second)}
    twitch.gql_request = _gql(_progress_response({
        "status": "IN_PROGRESS", "currentMinutesWatched": 9, "grantCount": 1,
    }))
    channel = _channel(twitch)
    current = channel
    service = InventoryService(twitch)

    async def switch_channel():
        nonlocal current
        current = None

    twitch.gui.broadcast_wanted_items_now.side_effect = switch_channel
    assert await service.refresh_reward_progress([first, second], channel, lambda: current is channel)
    assert current is None
    assert _drop(first).real_current_minutes == 9
    assert second.timed_drops["other-group"].current_minutes == 0


@pytest.mark.asyncio
async def test_reward_transport_failure_leaves_inventory_and_tick_fallback_available():
    twitch = _twitch()
    twitch.gql_request = AsyncMock(side_effect=aiohttp.ClientError())
    service = InventoryService(twitch)
    assert await service.fetch_reward_campaigns() == []
    assert not await service.refresh_reward_progress([_build(twitch=twitch)], _channel(twitch))


@pytest.mark.asyncio
async def test_optional_query_transport_failure_keeps_catalog():
    twitch = _twitch()
    normal_request = _gql(EMPTY_PROGRESS)

    async def request(ops):
        if isinstance(ops, list):
            return await normal_request(ops)
        raise aiohttp.ClientError()

    twitch.gql_request = request
    [campaign] = await InventoryService(twitch).fetch_reward_campaigns()
    assert campaign.id == _data()["id"]


def test_fallback_preserves_wanted_special_campaign_on_an_unselected_reward_game():
    twitch = _twitch()
    special = _campaign(twitch, SPECIAL_EVENTS)
    reward = _build({"status": "IN_PROGRESS", "currentMinutesWatched": 59}, twitch=twitch)
    twitch.inventory.append(reward)
    twitch.watching_channel = AwaitableValue()
    channel = _channel(twitch, channel_id=CHANNEL_ID)

    assert special.can_earn(channel) and reward.can_earn(channel)
    service = InventoryService(twitch)
    assert service.get_active_campaign(channel) is special
    twitch.wanted_games.append(reward.game)
    assert service.get_active_campaign(channel) is reward


@pytest.mark.asyncio
async def test_disabled_reward_does_not_select_flagless_channel_or_hide_real_grant():
    twitch = _twitch(mining_benefits={"DIRECT_ENTITLEMENT": True, "UNKNOWN": False})
    reward = _build({"status": "IN_PROGRESS", "currentMinutesWatched": 1, "grantCount": 0}, twitch=twitch)
    regular = _regular(twitch, GTA)
    twitch.inventory = [regular, reward]
    twitch.wanted_games = [regular.game]
    twitch.watching_channel = AwaitableValue()
    flagless = _channel(twitch)
    tagged = _channel(twitch, drops_enabled=True)
    service = InventoryService(twitch)
    watch = WatchService(twitch)
    next_hour = datetime.now(timezone.utc) + timedelta(hours=1)

    assert not watch.can_watch(flagless)
    assert not reward.can_earn_within(next_hour)
    assert service.get_active_campaign(flagless) is None
    assert watch.can_watch(tagged) and service.get_active_campaign(tagged) is regular

    # The inventory may still report a grant earned alongside an enabled reward.
    reward.apply_reward_progress({"status": "IN_PROGRESS", "currentMinutesWatched": 60, "grantCount": 1}, tagged)
    await reward.announce_new_grants()
    twitch.drop_history.record.assert_called_once_with(_drop(reward), reward, f"{GROUP_ID}#1")
    assert _drop(reward).is_mineable  # Inventory does not label a type preference as a skipped prerequisite.
    twitch.settings.mining_benefits["UNKNOWN"] = True
    assert watch.can_watch(flagless)
    assert reward.can_earn_within(next_hour)


def test_one_enabled_benefit_keeps_mixed_reward_watchable():
    twitch = _twitch(mining_benefits={"BADGE": True, "UNKNOWN": False})
    group = _group()
    group["rewards"].append({"id": "badge", "name": "Badge"})
    campaign = RewardCampaign.from_reward_campaign(twitch, _data([group]), {}, "uid", badge_rewards={"badge"})
    assert campaign.can_earn(_channel(twitch))
    twitch.settings.mining_benefits["BADGE"] = False
    assert not campaign.can_earn(_channel(twitch))
