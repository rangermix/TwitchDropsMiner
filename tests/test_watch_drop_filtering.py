import asyncio
from datetime import datetime, timedelta, timezone
from types import MethodType, SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.models.campaign import DropsCampaign
from src.services.stream_selector import StreamSelector
from src.web.gui_manager import WebGUIManager
from src.web.managers.inventory import InventoryManager


def _drop(
    drop_id: str,
    name: str,
    required_minutes: int,
    *,
    starts_at: str = "2026-01-01T00:00:00Z",
    ends_at: str = "2099-01-01T00:00:00Z",
    precondition_drop_ids: list[str] | None = None,
) -> dict:
    return {
        "id": drop_id,
        "name": name,
        "benefitEdges": [
            {
                "benefit": {
                    "id": f"benefit-{drop_id}",
                    "name": f"Reward {name}",
                    "distributionType": "DIRECT_ENTITLEMENT",
                    "imageAssetURL": f"https://example.test/{drop_id}.png",
                }
            }
        ],
        "startAt": starts_at,
        "endAt": ends_at,
        "preconditionDrops": [
            {"id": precondition_id} for precondition_id in (precondition_drop_ids or [])
        ],
        "requiredMinutesWatched": required_minutes,
    }


def _campaign(campaign_id: str, drops: list[dict]) -> DropsCampaign:
    return DropsCampaign(
        MagicMock(),
        {
            "id": campaign_id,
            "name": f"Campaign {campaign_id}",
            "game": {
                "id": "1",
                "name": "Test Game",
                "displayName": "Test Game",
                "boxArtURL": "https://example.test/game-{width}x{height}.jpg",
            },
            "self": {"isAccountConnected": True},
            "accountLinkURL": "https://example.test/link",
            "startAt": "2026-01-01T00:00:00Z",
            "endAt": "2099-01-01T00:00:00Z",
            "status": "ACTIVE",
            "allow": {"channels": [], "isEnabled": True},
            "timeBasedDrops": drops,
        },
        {},
    )


@pytest.mark.asyncio
async def test_inventory_hides_subscription_drops_and_sub_only_campaigns():
    broadcaster = MagicMock()
    broadcaster.emit = AsyncMock()
    manager = InventoryManager(broadcaster, MagicMock())
    sub_only = _campaign("sub-only", [_drop("sub", "Subscribe", 0)])
    mixed = _campaign(
        "mixed",
        [
            _drop("sub", "Subscribe", 0),
            _drop("watch", "Watch", 30),
        ],
    )

    await manager.add_campaign(sub_only)
    await manager.add_campaign(mixed)

    assert sub_only.total_drops == 0
    assert sub_only.claimed_drops == 0
    assert sub_only.remaining_drops == 0
    assert sub_only.required_minutes == 0
    assert sub_only.remaining_minutes == 0
    assert sub_only.progress == 0.0
    assert sub_only.availability == float("inf")
    assert sub_only.finished is True
    assert [campaign["id"] for campaign in manager.get_campaigns()] == ["mixed"]
    mixed_data = manager.get_campaigns()[0]
    assert [drop["id"] for drop in mixed_data["drops"]] == ["watch"]
    assert mixed_data["claimed_drops"] == 0
    assert mixed_data["total_drops"] == 1
    broadcaster.emit.assert_awaited_once_with("campaign_add", mixed_data)


@pytest.mark.asyncio
async def test_mixed_campaign_claim_counts_only_watch_drops():
    campaign = _campaign(
        "mixed",
        [
            _drop("sub", "Subscribe", 0),
            _drop("watch", "Watch", 30),
        ],
    )
    watch_drop = campaign.timed_drops["watch"]
    watch_drop._claim = AsyncMock(return_value=True)
    campaign._twitch.gui.broadcast_wanted_items_now = AsyncMock()

    assert campaign.total_drops == 1
    assert campaign.claimed_drops == 0
    assert campaign.remaining_drops == 1

    assert await watch_drop.claim() is True
    assert await watch_drop.claim() is True

    assert campaign.claimed_drops == 1
    assert campaign.remaining_drops == 0
    claim_message = campaign._twitch.print.call_args.args[0]
    assert "(1/1)" in claim_message
    campaign._twitch.gui.broadcast_wanted_items_now.assert_awaited_once_with()
    campaign._twitch.print.assert_called_once()


@pytest.mark.asyncio
async def test_failed_claim_does_not_refresh_wanted_queue():
    campaign = _campaign("claim-failed", [_drop("watch", "Watch", 30)])
    watch_drop = campaign.timed_drops["watch"]
    watch_drop._claim = AsyncMock(return_value=False)
    campaign._twitch.gui.broadcast_wanted_items_now = AsyncMock()

    assert await watch_drop.claim() is False

    campaign._twitch.gui.broadcast_wanted_items_now.assert_not_awaited()


@pytest.mark.asyncio
async def test_successful_claim_survives_wanted_queue_broadcast_failure():
    campaign = _campaign("broadcast-failed", [_drop("watch", "Watch", 30)])
    watch_drop = campaign.timed_drops["watch"]
    watch_drop._claim = AsyncMock(return_value=True)
    broadcaster = SimpleNamespace(emit=AsyncMock(side_effect=RuntimeError("socket down")))
    gui = SimpleNamespace(
        _broadcaster=broadcaster,
        get_wanted_game_tree=lambda: [],
        inv=MagicMock(),
    )
    gui.broadcast_wanted_items_now = MethodType(
        WebGUIManager.broadcast_wanted_items_now, gui
    )
    campaign._twitch.gui = gui

    assert await watch_drop.claim() is True

    assert watch_drop.is_claimed is True
    gui.inv.update_drop.assert_called_once_with(watch_drop)


@pytest.mark.asyncio
async def test_wanted_items_broadcast_preserves_synchronous_callers():
    broadcaster = SimpleNamespace(emit=AsyncMock())
    gui = SimpleNamespace(
        _broadcaster=broadcaster,
        get_wanted_game_tree=lambda: [{"game_name": "Test Game"}],
    )
    gui.broadcast_wanted_items_now = MethodType(
        WebGUIManager.broadcast_wanted_items_now, gui
    )
    gui.broadcast_wanted_items = MethodType(WebGUIManager.broadcast_wanted_items, gui)

    assert gui.broadcast_wanted_items() is None
    await asyncio.sleep(0)

    broadcaster.emit.assert_awaited_once_with(
        "wanted_items_update", [{"game_name": "Test Game"}]
    )


def test_wanted_queue_hides_subscription_drops_and_sub_only_campaigns():
    sub_only = _campaign("sub-only", [_drop("sub", "Subscribe", 0)])
    mixed = _campaign(
        "mixed",
        [
            _drop("sub", "Subscribe", 0),
            _drop("watch", "Watch", 30),
        ],
    )
    settings = SimpleNamespace(
        games_to_watch=["Test Game"],
        mining_benefits={"DIRECT_ENTITLEMENT": True},
    )

    result = StreamSelector().get_wanted_game_tree(settings, [sub_only, mixed])

    assert len(result) == 1
    assert [campaign["id"] for campaign in result[0]["campaigns"]] == ["mixed"]
    assert [drop["name"] for drop in result[0]["campaigns"][0]["drops"]] == ["Watch"]
    assert sub_only.can_earn_within(datetime.now(timezone.utc)) is False


def test_wanted_queue_hides_only_individually_expired_drops():
    now = datetime.now(timezone.utc)
    campaign = _campaign(
        "mixed-timeframes",
        [
            _drop(
                "expired",
                "Expired",
                30,
                ends_at=(now - timedelta(minutes=1)).isoformat(),
            ),
            _drop(
                "active",
                "Active",
                30,
                starts_at=(now - timedelta(minutes=30)).isoformat(),
                ends_at=(now + timedelta(hours=2)).isoformat(),
            ),
            _drop(
                "upcoming",
                "Upcoming",
                30,
                starts_at=(now + timedelta(minutes=30)).isoformat(),
                ends_at=(now + timedelta(hours=3)).isoformat(),
            ),
            _drop(
                "locked",
                "Sequential",
                30,
                starts_at=(now - timedelta(minutes=30)).isoformat(),
                ends_at=(now + timedelta(hours=3)).isoformat(),
                precondition_drop_ids=["active"],
            ),
        ],
    )
    settings = SimpleNamespace(
        games_to_watch=["Test Game"],
        mining_benefits={"DIRECT_ENTITLEMENT": True},
    )

    result = StreamSelector().get_wanted_game_tree(settings, [campaign])

    assert [drop["name"] for drop in result[0]["campaigns"][0]["drops"]] == [
        "Active",
        "Upcoming",
        "Sequential",
    ]


def test_wanted_queue_hides_campaign_when_all_individual_drops_expired():
    now = datetime.now(timezone.utc)
    campaign = _campaign(
        "expired",
        [
            _drop(
                "expired",
                "Expired",
                30,
                ends_at=(now - timedelta(minutes=1)).isoformat(),
            )
        ],
    )
    settings = SimpleNamespace(
        games_to_watch=["Test Game"],
        mining_benefits={"DIRECT_ENTITLEMENT": True},
    )

    assert StreamSelector().get_wanted_game_tree(settings, [campaign]) == []
