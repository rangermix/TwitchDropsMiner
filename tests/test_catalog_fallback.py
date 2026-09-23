from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

import src.services.inventory_service as inventory_service_module
from src.config import State
from src.services.inventory_service import InventoryService


# `src.services.catalog` is the module this change adds, so it is imported
# lazily: a module-level import would break collection on the pre-change tree
# and hide the regression test below behind a collection error.
try:
    from src.services.catalog import PublicCatalog
except ImportError:  # pragma: no cover - only on the pre-change tree
    PublicCatalog = None  # type: ignore[assignment, misc]

requires_catalog = pytest.mark.skipif(
    PublicCatalog is None, reason="src.services.catalog does not exist yet"
)


class _FakeResponse:
    def __init__(self, *, status: int = 200, payload: object = None, error: Exception | None = None):
        self.status = status
        self._payload = payload
        self._error = error

    async def json(self) -> object:
        if self._error is not None:
            raise self._error
        return self._payload


class _ResponseContext:
    def __init__(self, response: _FakeResponse):
        self._response = response

    async def __aenter__(self) -> _FakeResponse:
        return self._response

    async def __aexit__(self, exc_type, exc_value, traceback) -> None:
        return None


class _FakeSession:
    closed = False

    def __init__(self, response: _FakeResponse):
        self._response = response

    def get(self, url: str) -> _ResponseContext:
        return _ResponseContext(self._response)

    async def close(self) -> None:
        self.closed = True


def _catalog(response: _FakeResponse) -> PublicCatalog:
    catalog = PublicCatalog("https://catalog.invalid")
    catalog._session = _FakeSession(response)
    return catalog


@requires_catalog
@pytest.mark.asyncio
async def test_public_catalog_flattens_campaign_groups_in_order():
    catalog = _catalog(
        _FakeResponse(
            payload=[
                {"gameDisplayName": "First Game", "rewards": [{"id": "first"}, {"id": "second"}]},
                {"gameDisplayName": "Second Game", "rewards": [{"id": "third"}]},
            ]
        )
    )

    campaigns = await catalog.campaigns()

    assert [campaign["id"] for campaign in campaigns] == ["first", "second", "third"]


@requires_catalog
@pytest.mark.asyncio
async def test_public_catalog_defaults_self_without_overwriting_existing_self():
    existing_self = {"isAccountConnected": False, "extra": "preserved"}
    catalog = _catalog(
        _FakeResponse(
            payload=[
                {
                    "rewards": [
                        {"id": "missing-self"},
                        {"id": "existing-self", "self": existing_self},
                    ]
                }
            ]
        )
    )

    campaigns = await catalog.campaigns()

    assert campaigns[0]["self"] == {"isAccountConnected": True}
    assert campaigns[1]["self"] == existing_self


@requires_catalog
@pytest.mark.asyncio
async def test_public_catalog_normalizes_allow_defaults_and_preserves_channels():
    catalog = _catalog(
        _FakeResponse(
            payload=[
                {
                    "rewards": [
                        {"id": "missing-allow"},
                        {"id": "missing-channels", "allow": {"isEnabled": False}},
                        {
                            "id": "existing-channels",
                            "allow": {"channels": ["channel-a"], "isEnabled": False},
                        },
                    ]
                }
            ]
        )
    )

    campaigns = await catalog.campaigns()

    # `None` (not `[]`) matches what Twitch sends for a campaign without a
    # participating-channel list; an empty list collides with it in
    # GQLClient.merge_data and aborts the inventory fetch.
    assert campaigns[0]["allow"] == {"channels": None, "isEnabled": True}
    assert campaigns[1]["allow"] == {"channels": None, "isEnabled": False}
    assert campaigns[2]["allow"] == {"channels": ["channel-a"], "isEnabled": False}


@requires_catalog
@pytest.mark.asyncio
async def test_public_catalog_handles_invalid_payloads_and_skips_malformed_groups():
    invalid_responses = (
        _FakeResponse(status=503, payload=[]),
        _FakeResponse(error=ValueError("invalid JSON")),
        _FakeResponse(payload={"rewards": []}),
    )
    for response in invalid_responses:
        assert await _catalog(response).campaigns() == []

    campaigns = await _catalog(
        _FakeResponse(
            payload=[
                "not a group",
                {"rewards": "not a list"},
                {"rewards": ["not a reward", {"id": "valid"}]},
            ]
        )
    ).campaigns()

    assert [campaign["id"] for campaign in campaigns] == ["valid"]


@pytest.mark.asyncio
async def test_fetch_campaigns_skips_gated_campaign_and_returns_resolved_data(monkeypatch):
    monkeypatch.delenv("TDM_CATALOG_URL", raising=False)
    twitch = SimpleNamespace(
        get_auth=AsyncMock(return_value=SimpleNamespace(user_id="123")),
        gql_request=AsyncMock(
            return_value=[
                {"data": {"user": {"dropCampaign": None}}},
                {
                    "data": {
                        "user": {
                            "dropCampaign": {
                                "id": "resolved",
                                "details": "from-twitch",
                            }
                        }
                    }
                },
            ]
        ),
    )
    service = InventoryService(twitch)

    campaigns = await service.fetch_campaigns(
        [
            ("gated", {"id": "gated", "name": "overview-gated"}),
            ("resolved", {"id": "resolved", "name": "overview-resolved"}),
        ]
    )

    assert service._catalog is None
    assert campaigns["resolved"]["details"] == "from-twitch"


@requires_catalog
@pytest.mark.asyncio
async def test_inventory_service_uses_catalog_for_missing_campaigns_and_empty_inventory(
    monkeypatch,
):
    twitch_campaign = {"id": "twitch-id", "name": "Twitch list"}
    catalog_campaign = {"id": "catalog-id", "name": "Catalog detail", "catalog": True}
    duplicate_catalog_campaign = {
        "id": "twitch-id",
        "name": "Catalog duplicate",
        "catalog": True,
    }
    twitch = SimpleNamespace(
        get_auth=AsyncMock(return_value=SimpleNamespace(user_id="123")),
        gql_request=AsyncMock(
            return_value=[
                {
                    "data": {
                        "user": {
                            "dropCampaign": {
                                "id": "twitch-id",
                                "name": "Twitch detail",
                                "detail": "from-twitch",
                            }
                        }
                    }
                }
            ]
        ),
    )
    service = object.__new__(InventoryService)
    service._twitch = twitch
    service._catalog = SimpleNamespace(
        campaigns=AsyncMock(return_value=[twitch_campaign, catalog_campaign, duplicate_catalog_campaign])
    )

    campaigns = await service.fetch_campaigns(
        [
            ("twitch-id", twitch_campaign),
            ("catalog-id", {"id": "catalog-id", "name": "Twitch overview"}),
        ]
    )

    assert campaigns["twitch-id"]["name"] == "Twitch list"
    assert campaigns["twitch-id"]["detail"] == "from-twitch"
    assert "catalog" not in campaigns["twitch-id"]
    assert campaigns["catalog-id"]["catalog"] is True

    class _FakeCampaign:
        def __init__(self, twitch_client, data, claimed_benefits):
            self.id = data["id"]
            self.drops = []
            self.active = False
            self.upcoming = False
            self.starts_at = 0
            self.ends_at = 0
            self.eligible = False
            self.time_triggers = []

        def can_earn_within(self, when):
            return False

    monkeypatch.setattr(inventory_service_module, "DropsCampaign", _FakeCampaign)
    catalog_inventory_campaign = {
        "id": "catalog-inventory-id",
        "status": "ACTIVE",
        "game": {"id": "game-id"},
    }
    inventory_twitch = SimpleNamespace(
        gql_request=AsyncMock(
            side_effect=[
                {
                    "data": {
                        "currentUser": {
                            "inventory": {
                                "dropCampaignsInProgress": [],
                                "gameEventDrops": [],
                            }
                        }
                    }
                },
                {"data": {"currentUser": {"dropCampaigns": []}}},
            ]
        ),
        gui=SimpleNamespace(
            status=SimpleNamespace(update=MagicMock()),
            inv=SimpleNamespace(clear=MagicMock(), add_campaign=AsyncMock()),
        ),
        _drops={},
        _campaigns={},
        inventory=[],
        _mnt_triggers=[],
        _mnt_task=None,
        _state=State.IDLE,
        _maintenance_service=SimpleNamespace(run_maintenance_task=AsyncMock()),
    )
    inventory_service = object.__new__(InventoryService)
    inventory_service._twitch = inventory_twitch
    inventory_service._catalog = SimpleNamespace(
        campaigns=AsyncMock(return_value=[catalog_inventory_campaign])
    )
    inventory_service.fetch_campaigns = AsyncMock(
        return_value={catalog_inventory_campaign["id"]: catalog_inventory_campaign}
    )

    await inventory_service.fetch_inventory()
    await inventory_twitch._mnt_task

    inventory_service._catalog.campaigns.assert_awaited_once_with()
    inventory_service.fetch_campaigns.assert_awaited_once_with(
        [(catalog_inventory_campaign["id"], catalog_inventory_campaign)]
    )
