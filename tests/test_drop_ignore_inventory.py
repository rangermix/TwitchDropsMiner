from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.web.managers.inventory import InventoryManager
from tests.test_drop_ignore_policy import _campaign, _drop


def _drops_by_id(campaign_data: dict) -> dict[str, dict]:
    return {drop["id"]: drop for drop in campaign_data["drops"]}


@pytest.mark.asyncio
async def test_inventory_serializes_ignored_blocked_and_skipped_drop_state():
    campaign = _campaign(
        [
            _drop("unused", "Unused prerequisite", has_benefit=False),
            _drop("direct", "Mask Reward", preconditions=("unused",)),
            _drop("blocked", "Blocked Reward", preconditions=("direct",)),
        ],
        blacklist=["mask"],
    )
    broadcaster = MagicMock()
    broadcaster.emit = AsyncMock()
    manager = InventoryManager(broadcaster, MagicMock())

    await manager.add_campaign(campaign)

    campaign_data = manager.get_campaigns()[0]
    drops = _drops_by_id(campaign_data)
    assert campaign_data["claimed_drops"] == 0
    assert campaign_data["total_drops"] == 3
    assert campaign_data["ignored_drops"] == 2
    assert campaign_data["skipped_drops"] == 1
    assert campaign_data["finished"] is False
    assert campaign_data["mining_finished"] is True
    assert {
        key: drops["unused"][key]
        for key in (
            "is_ignored",
            "is_mineable",
            "is_skipped",
            "ignored_reason",
            "ignored_keyword",
            "ignored_precondition",
        )
    } == {
        "is_ignored": False,
        "is_mineable": False,
        "is_skipped": True,
        "ignored_reason": None,
        "ignored_keyword": None,
        "ignored_precondition": None,
    }
    assert drops["direct"]["is_ignored"] is True
    assert drops["direct"]["is_mineable"] is False
    assert drops["direct"]["is_skipped"] is False
    assert drops["direct"]["ignored_reason"] == "keyword"
    assert drops["direct"]["ignored_keyword"] == "mask"
    assert drops["direct"]["ignored_precondition"] is None
    assert drops["blocked"]["is_ignored"] is True
    assert drops["blocked"]["ignored_reason"] == "precondition"
    assert drops["blocked"]["ignored_keyword"] is None
    assert drops["blocked"]["ignored_precondition"] == "Mask Reward"


@pytest.mark.asyncio
async def test_drop_update_reserializes_the_full_dynamic_dependency_graph():
    campaign = _campaign(
        [
            _drop("prerequisite", "Skip prerequisite"),
            _drop("reward", "Final Reward", preconditions=("prerequisite",)),
        ],
        blacklist=["skip"],
    )
    broadcaster = MagicMock()
    broadcaster.emit = AsyncMock()
    manager = InventoryManager(broadcaster, MagicMock())
    prerequisite = campaign.timed_drops["prerequisite"]

    await manager.add_campaign(campaign)
    broadcaster.emit.reset_mock()
    prerequisite.is_claimed = True

    manager.update_drop(prerequisite)
    await asyncio.sleep(0)

    campaign_data = manager.get_campaigns()[0]
    drops = _drops_by_id(campaign_data)
    assert campaign_data["claimed_drops"] == 1
    assert campaign_data["total_drops"] == 2
    assert campaign_data["ignored_drops"] == 0
    assert campaign_data["skipped_drops"] == 0
    assert campaign_data["finished"] is False
    assert campaign_data["mining_finished"] is False
    assert drops["prerequisite"]["is_claimed"] is True
    assert drops["prerequisite"]["is_ignored"] is False
    assert drops["reward"]["is_ignored"] is False
    assert drops["reward"]["is_mineable"] is True
    broadcaster.emit.assert_awaited_once_with(
        "drop_update",
        {
            "campaign_id": campaign.id,
            "campaign": {
                "claimed_drops": 1,
                "total_drops": 2,
                "ignored_drops": 0,
                "skipped_drops": 0,
                "finished": False,
                "mining_finished": False,
            },
            "drop": drops["prerequisite"],
            "drops": campaign_data["drops"],
        },
    )


@pytest.mark.asyncio
async def test_policy_setting_refresh_replaces_all_inventory_graph_metadata():
    campaign = _campaign(
        [
            _drop("prerequisite", "Skip prerequisite"),
            _drop("reward", "Final Reward", preconditions=("prerequisite",)),
        ],
        blacklist=["skip"],
    )
    broadcaster = MagicMock()
    broadcaster.emit = AsyncMock()
    manager = InventoryManager(broadcaster, MagicMock())

    await manager.add_campaign(campaign)
    broadcaster.emit.reset_mock()
    campaign._twitch.settings.drop_name_blacklist = []

    manager.refresh_campaigns([campaign])
    await asyncio.sleep(0)

    campaign_data = manager.get_campaigns()[0]
    drops = _drops_by_id(campaign_data)
    assert campaign_data["ignored_drops"] == 0
    assert campaign_data["skipped_drops"] == 0
    assert campaign_data["mining_finished"] is False
    assert drops["prerequisite"]["is_mineable"] is True
    assert drops["reward"]["is_mineable"] is True
    assert all(drop["ignored_reason"] is None for drop in drops.values())
    broadcaster.emit.assert_awaited_once_with(
        "inventory_batch_update", {"campaigns": [campaign_data]}
    )


@pytest.mark.asyncio
async def test_hidden_zero_minute_drop_update_refreshes_visible_dependents():
    campaign = _campaign(
        [
            _drop("hidden", "Subscription prerequisite", required_minutes=0),
            _drop("reward", "Visible Reward", preconditions=("hidden",)),
        ]
    )
    broadcaster = MagicMock()
    broadcaster.emit = AsyncMock()
    manager = InventoryManager(broadcaster, MagicMock())
    hidden = campaign.timed_drops["hidden"]

    await manager.add_campaign(campaign)
    before = manager.get_campaigns()[0]
    assert [drop["id"] for drop in before["drops"]] == ["reward"]
    assert before["drops"][0]["is_mineable"] is False
    assert before["mining_finished"] is True
    broadcaster.emit.reset_mock()

    hidden.is_claimed = True
    manager.update_drop(hidden)
    await asyncio.sleep(0)

    after = manager.get_campaigns()[0]
    assert after["drops"][0]["is_mineable"] is True
    assert after["mining_finished"] is False
    broadcaster.emit.assert_awaited_once_with(
        "inventory_batch_update", {"campaigns": [after]}
    )
