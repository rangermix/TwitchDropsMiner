from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.models.campaign import DropsCampaign
from src.services.stream_selector import StreamSelector
from src.web.managers.inventory import InventoryManager


def _drop(drop_id: str, name: str, required_minutes: int) -> dict:
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
        "startAt": "2026-01-01T00:00:00Z",
        "endAt": "2099-01-01T00:00:00Z",
        "preconditionDrops": [],
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

    assert campaign.total_drops == 1
    assert campaign.claimed_drops == 0
    assert campaign.remaining_drops == 1

    await watch_drop.claim()

    assert campaign.claimed_drops == 1
    assert campaign.remaining_drops == 0
    claim_message = campaign._twitch.print.call_args.args[0]
    assert "(1/1)" in claim_message


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
