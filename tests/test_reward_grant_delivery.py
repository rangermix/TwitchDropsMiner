import asyncio
from collections import deque
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.config import GQL_OPERATIONS, State
from src.drop_history import DropHistory
from src.models import RewardCampaign
from src.models.reward_campaign import GRANTS_PER_UPDATE, RewardLedger
from src.services.inventory_service import InventoryService
from src.web.managers.inventory import InventoryManager
from tests.test_reward_campaigns import (
    CAMPAIGN_ID, GROUP_ID, NOW, _channel, _data, _gql, _granted_build, _group, _progress_response,
    _twitch,
)


def _inventory_service(tmp_path):
    twitch = _twitch()
    twitch.drop_history = DropHistory(tmp_path)
    twitch._drops, twitch.inventory, twitch._mnt_triggers, twitch._mnt_task = {}, [], deque(), None
    twitch._state = State.IDLE
    twitch.gui.inv.add_campaign = AsyncMock()
    twitch._maintenance_service = SimpleNamespace(run_maintenance_task=AsyncMock())
    return InventoryService(twitch), twitch


async def _reload(service, twitch, data, reading, completed=(), *, sibling_readings=None):
    progress = _progress_response(reading)
    if sibling_readings:
        [campaign] = progress["data"]["currentUser"]["inventory"]["viewerRewardDropCampaignsInProgress"]
        campaign["rewardGroups"].extend(
            {"id": gid, "self": reading} for gid, reading in sibling_readings.items()
        )
    rewards = _gql(
        progress,
        campaigns={"data": {"rewardCampaignsAvailableToUser": [data]}}, completed=completed,
    )

    async def request(op):
        if op is GQL_OPERATIONS["Inventory"]:
            return {"data": {"currentUser": {"inventory": {
                "dropCampaignsInProgress": [], "gameEventDrops": [],
            }}}}
        if op is GQL_OPERATIONS["Campaigns"]:
            return {"data": {"currentUser": {"dropCampaigns": []}}}
        return await rewards(op)

    twitch.gql_request = request
    await service.fetch_inventory()
    await twitch._mnt_task


@pytest.mark.asyncio
@pytest.mark.parametrize("switch_lane", [False, True], ids=["completed-campaign", "another-lane"])
async def test_final_count_survives_removal_or_lane_change(tmp_path, switch_lane):
    service, twitch = _inventory_service(tmp_path)
    groups = [_group()]
    if switch_lane:
        groups.append(_group("other"))
        groups[1]["rewards"][0]["name"] = "Replacement lane"
    data = _data(groups)
    active = {"status": "IN_PROGRESS", "currentMinutesWatched": 5, "grantCount": 1}
    completed = () if switch_lane else (CAMPAIGN_ID,)
    final = {"status": "FULFILLED", "grantCount": 2}

    await _reload(service, twitch, data, active)
    twitch.print.assert_not_called()
    await _reload(service, twitch, data, final, completed)
    if switch_lane:
        assert list(twitch.inventory[0].timed_drops) == ["other"]
    else:
        assert not twitch.inventory
    await _reload(service, twitch, data, final, completed)

    [entry] = DropHistory(tmp_path).get_entries()
    assert entry["id"] == f"{GROUP_ID}#2"
    assert entry["benefits"] == [groups[0]["rewards"][0]["name"]]
    twitch.print.assert_called_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("interrupt", [False, True], ids=["gui-error", "cancelled"])
async def test_interrupted_delivery_keeps_history_and_later_alerts(tmp_path, interrupt, caplog):
    service, twitch = _inventory_service(tmp_path)
    ledger = service._reward_ledger
    _granted_build(1, twitch, ledger=ledger)
    updated = _granted_build(4, twitch, ledger=ledger)
    entered, release = asyncio.Event(), asyncio.Event()

    async def broadcast():
        if not entered.is_set():
            entered.set()
            await release.wait()
            raise RuntimeError("private.example/secret-marker")

    twitch.gui.broadcast_wanted_items_now = AsyncMock(side_effect=broadcast)
    delivery = asyncio.create_task(updated.announce_new_grants())
    await asyncio.wait_for(entered.wait(), timeout=2)
    assert [e["id"] for e in DropHistory(tmp_path).get_entries()] == [
        f"{GROUP_ID}#{n}" for n in (4, 3, 2)
    ]
    # The next complete inventory can drop this campaign altogether; it must still drain
    # untouched alerts, and concurrent deliveries must never send a grant twice.
    concurrent = asyncio.create_task(_reload(service, twitch, _data(), None, (CAMPAIGN_ID,)))
    if interrupt:
        delivery.cancel()
        with pytest.raises(asyncio.CancelledError):
            await delivery
    else:
        release.set()
        await delivery
    await concurrent
    assert not twitch.inventory
    assert [call.args[0].split("(#")[-1] for call in twitch.print.call_args_list] == ["2)", "3)", "4)"]
    assert twitch.gui.broadcast_wanted_items_now.await_count == 3
    assert "secret-marker" not in caplog.text


@pytest.mark.parametrize("reading", [None, {"status": "IN_PROGRESS", "grantCount": "bad"}])
def test_inventory_preserves_known_grant_count_on_missing_reading(reading):
    twitch = _twitch()
    ledger = RewardLedger()
    _granted_build(2, twitch, ledger=ledger)
    progress = {} if reading is None else {GROUP_ID: reading}
    campaign = RewardCampaign.from_reward_campaign(twitch, _data(), progress, "uid", ledger=ledger)
    manager = InventoryManager(MagicMock(), MagicMock())
    assert manager._serialize_campaign(campaign)["drops"][0]["reward_grants"] == 2
    assert not ledger.pending_history


@pytest.mark.asyncio
async def test_impossible_grant_jump_does_not_replay_history_or_swallow_next_real_grant(tmp_path):
    service, twitch = _inventory_service(tmp_path)
    ledger = service._reward_ledger
    _granted_build(1, twitch, ledger=ledger)
    await _granted_build(2**31 - 1, twitch, ledger=ledger).announce_new_grants()
    await _granted_build(2, twitch, ledger=ledger).announce_new_grants()
    assert [e["id"] for e in DropHistory(tmp_path).get_entries()] == [f"{GROUP_ID}#2"]
    twitch.print.assert_called_once()


@pytest.mark.asyncio
async def test_failed_atomic_history_write_retries_without_duplicate_alerts(tmp_path):
    service, twitch = _inventory_service(tmp_path)
    ledger = service._reward_ledger
    _granted_build(1, twitch, ledger=ledger)
    updated = _granted_build(2, twitch, ledger=ledger)
    blocked_temp_file = tmp_path / "drop_history.json.tmp"
    blocked_temp_file.mkdir()
    await updated.announce_new_grants()
    assert DropHistory(tmp_path).total_count == 0
    twitch.print.assert_not_called()

    blocked_temp_file.rmdir()
    await ledger.announce_pending()
    await ledger.announce_pending()
    assert [e["id"] for e in DropHistory(tmp_path).get_entries()] == [f"{GROUP_ID}#2"]
    twitch.print.assert_called_once()


@pytest.mark.asyncio
async def test_large_plausible_rise_keeps_a_compact_backlog_and_bounded_delivery(tmp_path):
    service, twitch = _inventory_service(tmp_path)
    ledger = service._reward_ledger
    _granted_build(0, twitch, ledger=ledger)
    upper = GRANTS_PER_UPDATE * 3
    await _granted_build(upper, twitch, ledger=ledger).announce_new_grants()
    assert twitch.print.call_count == GRANTS_PER_UPDATE
    pending = ledger.pending_history[GROUP_ID]
    assert (pending.first, pending.last) == (GRANTS_PER_UPDATE + 1, upper)

    await _granted_build(upper + 1, twitch, ledger=ledger).announce_new_grants()
    assert twitch.print.call_count == GRANTS_PER_UPDATE * 2
    await ledger.announce_pending()
    await ledger.announce_pending()
    assert not ledger.pending_history
    assert twitch.print.call_count == upper + 1
    assert DropHistory(tmp_path).total_count == upper + 1


@pytest.mark.asyncio
async def test_first_selection_of_previously_observed_sibling_announces_only_its_new_grant(tmp_path):
    service, twitch = _inventory_service(tmp_path)
    data = _data([_group(), _group("sibling")])
    data["rewardGroups"][1]["rewards"][0]["name"] = "Sibling reward"
    active = {"status": "IN_PROGRESS", "currentMinutesWatched": 8, "grantCount": 1}
    await _reload(service, twitch, data, active, sibling_readings={"sibling": active})
    assert list(twitch.inventory[0].timed_drops) == [GROUP_ID]
    twitch.print.assert_not_called()
    finished = {"status": "FULFILLED", "grantCount": 1}
    await _reload(service, twitch, data, finished, sibling_readings={"sibling": {**active, "grantCount": 2}})
    assert list(twitch.inventory[0].timed_drops) == ["sibling"]
    [entry] = DropHistory(tmp_path).get_entries()
    assert (entry["id"], entry["benefits"]) == ("sibling#2", ["Sibling reward"])
    twitch.print.assert_called_once()


@pytest.mark.asyncio
async def test_invalid_first_and_tick_counts_do_not_poison_later_observations(tmp_path):
    service, twitch = _inventory_service(tmp_path)
    ledger = service._reward_ledger
    bad = {"status": "IN_PROGRESS", "currentMinutesWatched": 5, "grantCount": 2**31 - 1}
    data = _data([_group(), _group("sibling")])
    await _reload(service, twitch, data, bad, sibling_readings={"sibling": bad})
    [campaign] = twitch.inventory
    assert campaign.reward_grants == 0 and not ledger.announced
    channel = _channel(twitch)
    campaign.apply_reward_progress(bad, channel)
    assert campaign.reward_grants == 0 and not ledger.announced
    campaign.apply_reward_progress({**bad, "grantCount": 1}, channel)
    campaign.apply_reward_progress(bad, channel)
    assert campaign.reward_grants == 1 and ledger.announced == {GROUP_ID: 1}
    campaign.apply_reward_progress({**bad, "grantCount": 2}, channel)
    await campaign.announce_new_grants()
    assert [e["id"] for e in DropHistory(tmp_path).get_entries()] == [f"{GROUP_ID}#2"]


@pytest.mark.parametrize(("extra_seconds", "expected"), [(0, 0), (1, 2)])
def test_short_campaign_count_boundary(extra_seconds, expected):
    data = _data()
    start = NOW - timedelta(minutes=30)
    data.update(startsAt=start.isoformat(), endsAt=(start + timedelta(minutes=60, seconds=extra_seconds)).isoformat())
    reading = {GROUP_ID: {"status": "FULFILLED", "grantCount": 2}}
    campaign = RewardCampaign.from_reward_campaign(_twitch(), data, reading, "uid")
    assert campaign.reward_grants == expected
