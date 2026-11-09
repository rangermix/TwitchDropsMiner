"""Unlinked game rewards are not claimed; Twitch accepts the claim but never stores it."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from tests.test_special_game_watch import TEST_GAME, _campaign


def make_drop(*, linked, benefit_type="DIRECT_ENTITLEMENT", override=False):
    twitch = MagicMock()
    twitch.settings = SimpleNamespace(
        allow_unlinked_campaigns=override,
        drop_name_blacklist=[],
        games_to_watch=["Test Game"],
        mining_benefits={"DIRECT_ENTITLEMENT": True, "BADGE": True, "EMOTE": True},
    )
    twitch.gql_request = AsyncMock(
        return_value={"data": {"claimDropRewards": {"status": "ELIGIBLE_FOR_ALL"}}}
    )
    campaign = _campaign(twitch, TEST_GAME, benefit_type=benefit_type)
    campaign.linked = linked
    drop = next(iter(campaign.drops))
    drop.claim_id = "user#campaign#drop"
    return twitch, drop


@pytest.mark.parametrize(
    ("linked", "benefit_type", "override", "expected"),
    [
        (True, "DIRECT_ENTITLEMENT", False, True),
        (False, "DIRECT_ENTITLEMENT", False, False),
        (False, "DIRECT_ENTITLEMENT", True, False),
        (False, "BADGE", False, True),
        (False, "EMOTE", False, True),
    ],
)
def test_can_claim_requires_link_for_game_rewards(linked, benefit_type, override, expected):
    _, drop = make_drop(linked=linked, benefit_type=benefit_type, override=override)
    assert drop.needs_account_link is not expected
    assert drop.can_claim is expected


@pytest.mark.asyncio
async def test_unlinked_claim_skips_request_and_does_not_mark_claimed():
    twitch, drop = make_drop(linked=False)
    assert await drop.claim() is False
    twitch.gql_request.assert_not_awaited()
    assert not drop.is_claimed
    twitch.print.assert_not_called()
    twitch.drop_history.record.assert_not_called()


@pytest.mark.asyncio
async def test_claim_proceeds_after_account_is_linked():
    twitch, drop = make_drop(linked=False)
    drop.campaign.linked = True
    twitch.gui.broadcast_wanted_items_now = AsyncMock()
    assert await drop.claim() is True
    twitch.gql_request.assert_awaited_once()
    assert drop.is_claimed
