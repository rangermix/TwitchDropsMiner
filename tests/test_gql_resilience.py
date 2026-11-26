"""Transient or partial Twitch GQL failures must not stop the miner."""

from collections import deque
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.api import GQLClient
from src.config import GQL_OPERATIONS
from src.config.client_info import ClientType
from src.exceptions import GQLException, MinerException
from src.models.game import Game
from src.services.channel_service import ChannelService
from src.services.inventory_service import INVENTORY_RETRY_DELAY, InventoryService
from tests.test_watched_channel_recovery import GAME, OFFLINE, _channel, _reloading_miner, _run


SERVICE_ERROR = {"errors": [{"message": "service error"}]}  # no "extensions" key
OK = {"data": {"ok": True}}


def _gql(responses):
    browser = SimpleNamespace(gql=AsyncMock(side_effect=responses))
    auth = SimpleNamespace(validate=AsyncMock(return_value=SimpleNamespace(
        browser_active=True, _twitch=SimpleNamespace(_browser=browser),
    )))
    return GQLClient(MagicMock(), auth, ClientType.WEB), browser


@pytest.mark.asyncio
async def test_repeated_service_errors_without_extensions_are_retried():
    client, browser = _gql([SERVICE_ERROR, SERVICE_ERROR, OK])
    with patch("src.api.gql_client.asyncio.sleep", AsyncMock()):
        assert await client.request(GQL_OPERATIONS["Inventory"]) == OK
    assert browser.gql.await_count == 3


@pytest.mark.asyncio
async def test_service_error_retries_are_bounded():
    client, browser = _gql([SERVICE_ERROR] * 10)
    with patch("src.api.gql_client.asyncio.sleep", AsyncMock()), pytest.raises(GQLException):
        await client.request(GQL_OPERATIONS["Inventory"])
    assert browser.gql.await_count == 4


@pytest.mark.asyncio
@pytest.mark.parametrize("data", [{"game": None}, {"game": {"streams": None}}, {}])
async def test_directory_lookup_tolerates_a_null_game(data):
    twitch = MagicMock()
    twitch.gql_request = AsyncMock(return_value={"data": data})
    assert await ChannelService(twitch).get_live_streams(Game(GAME)) == []


@pytest.mark.asyncio
async def test_one_failing_game_directory_does_not_stop_discovery(tmp_path, monkeypatch):
    client, watched = _reloading_miner(tmp_path, monkeypatch, discovered=[], stream_status=[OFFLINE])
    broken, working = Game({"id": "2", "name": "Broken"}), Game(GAME)
    client.wanted_games = [broken, working]
    client.inventory = [SimpleNamespace(
        game=game, can_earn_within=lambda stamp: True, allowed_channels=[],
        can_earn=lambda channel: True, can_progress_on=lambda channel: channel.drops_enabled,
    ) for game in (broken, working)]
    found = _channel(client, 2)

    async def live_streams(game, *, drops_enabled):
        if game is broken:
            # As ChannelService.get_live_streams wraps a GQL failure.
            raise MinerException("Game: broken") from GQLException("PersistedQueryNotFound")
        return [found]

    client.get_live_streams = AsyncMock(side_effect=live_streams)
    await _run(client)
    assert client.watching_channel.get_with_default(None) is found


def _inventory_twitch(responses):
    twitch = SimpleNamespace(
        gui=MagicMock(), get_auth=AsyncMock(return_value=SimpleNamespace(user_id="uid")),
        _drops={}, inventory=[], _campaigns={}, _mnt_triggers=deque(), _mnt_task=None,
        _maintenance_service=SimpleNamespace(run_maintenance_task=AsyncMock()),
        gql_request=AsyncMock(side_effect=responses),
        settings=SimpleNamespace(minimum_refresh_interval_minutes=30),
    )
    return twitch


@pytest.mark.asyncio
async def test_null_campaign_details_are_skipped():
    twitch = _inventory_twitch([[
        {"data": {"user": {"dropCampaign": None}}},
        {"data": {"user": None}},
    ]])
    service = InventoryService(twitch)
    assert await service.fetch_campaigns({"a": {"id": "a"}, "b": {"id": "b"}}) == {}


@pytest.mark.asyncio
@pytest.mark.parametrize("failing", ["Inventory", "Campaigns"])
async def test_null_current_user_fails_the_fetch_and_schedules_a_quick_retry(failing):
    inventory = {"data": {"currentUser": {
        "inventory": {"dropCampaignsInProgress": [], "gameEventDrops": []},
    }}}
    responses = [{"data": {"currentUser": None}}] if failing == "Inventory" else [
        inventory, {"data": {"currentUser": None}},
    ]
    twitch = _inventory_twitch(responses)
    service = InventoryService(twitch)
    with pytest.raises(GQLException):
        await service.fetch_inventory()
    twitch._maintenance_service.run_maintenance_task.assert_called_once_with(INVENTORY_RETRY_DELAY)
    await twitch._mnt_task


@pytest.mark.asyncio
async def test_a_failing_details_batch_is_skipped():
    responses = [
        {"data": {"currentUser": {"inventory": {"dropCampaignsInProgress": [], "gameEventDrops": []}}}},
        {"data": {"currentUser": {"dropCampaigns": [{"id": "c1", "status": "ACTIVE"}]}}},
        GQLException("service error"),
    ]
    twitch = _inventory_twitch(responses)
    service = InventoryService(twitch)
    service.fetch_reward_campaigns = AsyncMock(return_value=[])
    await service.fetch_inventory()
    assert twitch.inventory == []
    twitch._maintenance_service.run_maintenance_task.assert_called_once_with(None)
    await twitch._mnt_task


@pytest.mark.asyncio
@pytest.mark.parametrize("loaded", [False, True])
async def test_failed_inventory_fetch_keeps_the_miner_running(tmp_path, monkeypatch, loaded):
    from src.config import State

    client, _ = _reloading_miner(tmp_path, monkeypatch, discovered=[], stream_status=[])
    client._state = State.INVENTORY_FETCH
    client._inventory_loaded = loaded
    client.inventory = []
    states = []
    original_change = client.change_state

    def record(state):
        # Stop right after the state chosen for the failed fetch.
        states.append(state)
        original_change(State.EXIT)

    client.change_state = record
    client.fetch_inventory = AsyncMock(side_effect=GQLException("Inventory returned no currentUser"))
    await _run(client)
    assert states[0] is (State.GAMES_UPDATE if loaded else State.IDLE)


INVENTORY_OK = {"data": {"currentUser": {
    "inventory": {"dropCampaignsInProgress": [], "gameEventDrops": []},
}}}


@pytest.mark.asyncio
@pytest.mark.parametrize("inventory", [None, {"dropCampaignsInProgress": [], "gameEventDrops": None}])
async def test_null_inventory_fields_fail_the_fetch_without_a_type_error(inventory):
    twitch = _inventory_twitch([{"data": {"currentUser": {"inventory": inventory}}}])
    with pytest.raises(GQLException):
        await InventoryService(twitch).fetch_inventory()
    await twitch._mnt_task


@pytest.mark.asyncio
async def test_retry_delay_backs_off_up_to_the_refresh_interval_and_resets():
    from datetime import timedelta

    failure = {"data": {"currentUser": None}}
    twitch = _inventory_twitch([failure] * 6 + [INVENTORY_OK, {"data": {"currentUser": {"dropCampaigns": []}}},
                                                failure])
    service = InventoryService(twitch)
    service.fetch_reward_campaigns = AsyncMock(return_value=[])
    for _ in range(6):
        with pytest.raises(GQLException):
            await service.fetch_inventory()
    await service.fetch_inventory()
    with pytest.raises(GQLException):
        await service.fetch_inventory()
    delays = [call.args[0] for call in twitch._maintenance_service.run_maintenance_task.call_args_list]
    assert delays == [timedelta(minutes=m) for m in (2, 4, 8, 16, 30, 30)] + [None, timedelta(minutes=2)]
    await twitch._mnt_task


@pytest.mark.asyncio
async def test_in_progress_campaign_without_details_is_skipped():
    in_progress = {"id": "c1", "name": "Partial", "game": {"id": "1", "name": "G"}}
    responses = [
        {"data": {"currentUser": {"inventory": {
            "dropCampaignsInProgress": [in_progress], "gameEventDrops": [],
        }}}},
        {"data": {"currentUser": {"dropCampaigns": [{"id": "c1", "status": "ACTIVE"}]}}},
        GQLException("service error"),
    ]
    twitch = _inventory_twitch(responses)
    service = InventoryService(twitch)
    service.fetch_reward_campaigns = AsyncMock(return_value=[])
    await service.fetch_inventory()  # would raise KeyError building from Inventory data alone
    assert twitch.inventory == []
    await twitch._mnt_task


@pytest.mark.asyncio
async def test_login_errors_from_a_directory_lookup_still_propagate(tmp_path, monkeypatch):
    from src.exceptions import LoginException

    client, _ = _reloading_miner(tmp_path, monkeypatch, discovered=[], stream_status=[OFFLINE])
    client.get_live_streams = AsyncMock(side_effect=LoginException("login"))
    with pytest.raises(LoginException):
        await _run(client)


@pytest.mark.asyncio
@pytest.mark.parametrize("reply", [{"data": {"currentUser": None}}, GQLException("down")])
async def test_claim_follow_up_survives_a_null_or_failed_current_drop(reply):
    from src.services.message_handlers import MessageHandlerService
    from src.utils.async_helpers import AwaitableValue

    channel = SimpleNamespace(id=7)
    watching = AwaitableValue()
    watching.set(channel)
    campaign = MagicMock()
    campaign.can_earn.return_value = True
    drop = MagicMock(is_claimed=False, claim_requires_account_link=False, campaign=campaign)
    drop.claim = AsyncMock(return_value=True)
    twitch = SimpleNamespace(
        _drops={"d1": drop}, watching_channel=watching, restart_watching=MagicMock(),
        request_inventory_refresh=MagicMock(), gql_request=AsyncMock(side_effect=[reply]),
    )
    message = {"type": "drop-claim", "data": {"drop_id": "d1", "drop_instance_id": "i1"}}
    with patch("src.services.message_handlers.asyncio.sleep", AsyncMock()):
        await MessageHandlerService(twitch).process_drops(1, message)
    twitch.restart_watching.assert_called_once()
