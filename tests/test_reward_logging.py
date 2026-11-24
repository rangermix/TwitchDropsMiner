"""Account-specific reward responses stay out of debug logs on reload and progress polls."""

import logging
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.api.gql_client import GQLClient
from src.config import ClientType, GQLRawQuery
from src.services.inventory_service import InventoryService
from tests.test_reward_campaigns import _channel, _gql, _progress_response, _twitch


@pytest.mark.asyncio
async def test_reward_queries_keep_account_data_out_of_debug_logs(caplog):
    twitch = _twitch()
    progress = _progress_response({
        "status": "IN_PROGRESS", "currentMinutesWatched": 12, "grantCount": 1,
        "unexpectedField": "private-reading-fixture",
    })
    progress["data"]["currentUser"]["id"] = "private-account-fixture"
    browser = SimpleNamespace(gql=_gql(progress))
    auth = SimpleNamespace(validate=AsyncMock(return_value=SimpleNamespace(
        browser_active=True, _twitch=SimpleNamespace(_browser=browser),
    )))
    twitch.gql_request = GQLClient(MagicMock(), auth, ClientType.WEB).request
    service = InventoryService(twitch)
    with caplog.at_level(logging.DEBUG, logger="TwitchDrops"):
        campaign, = await service.fetch_reward_campaigns()
        twitch._campaigns[campaign.id] = campaign
        assert await service.refresh_reward_progress([campaign], _channel(twitch))
        await twitch.gql_request(GQLRawQuery("query PublicFixture { test }"))

    assert "Reward inventory: 1 group readings" in caplog.text
    assert "private-account-fixture" not in caplog.text
    assert "private-reading-fixture" not in caplog.text
    assert "RewardCampaignHints" not in caplog.text
    assert "CompletedRewardCampaigns" not in caplog.text
    assert "PublicFixture" in caplog.text
