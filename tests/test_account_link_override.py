"""Account-link override changes eligibility without inventing linkage or progress."""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from src.services.stream_selector import StreamSelector
from src.services.watch_service import WatchService
from src.web.managers.inventory import InventoryManager
from tests.test_special_game_watch import OTHER_GAME, TEST_GAME, _campaign, _channel


def make_campaign(override=False, benefit_type="DIRECT_ENTITLEMENT"):
    twitch = MagicMock()
    twitch.settings = SimpleNamespace(
        allow_unlinked_campaigns=override,
        drop_name_blacklist=[],
        games_to_watch=["Test Game"],
        mining_benefits={"DIRECT_ENTITLEMENT": True, "BADGE": True, "EMOTE": True},
    )
    campaign = _campaign(twitch, TEST_GAME, benefit_type=benefit_type)
    return twitch, campaign, _channel(twitch)


@pytest.mark.parametrize(
    ("linked", "benefit_type", "override", "expected"),
    [
        (False, "DIRECT_ENTITLEMENT", False, False),
        (False, "DIRECT_ENTITLEMENT", True, True),
        (True, "DIRECT_ENTITLEMENT", False, True),
        (False, "BADGE", False, True),
        (False, "EMOTE", False, True),
    ],
)
def test_link_status_eligibility_and_selection(linked, benefit_type, override, expected):
    twitch, campaign, channel = make_campaign(override, benefit_type)
    campaign.linked = linked
    assert campaign.eligible is expected
    assert campaign.can_earn(channel) is expected
    assert WatchService(twitch).can_watch(channel) is expected
    assert bool(StreamSelector().get_wanted_games(twitch.settings, [campaign])) is expected
    serialized = InventoryManager(MagicMock(), MagicMock())._serialize_campaign(campaign)
    assert serialized["linked"] is linked
    assert campaign.linked is linked


@pytest.mark.parametrize("value", [False, None, "true", "false", 1, [], MagicMock()])
def test_override_requires_explicit_true(value):
    twitch, campaign, channel = make_campaign(value)
    assert not campaign.eligible
    assert not WatchService(twitch).can_watch(channel)


@pytest.mark.parametrize(
    "blocker",
    ["offline", "wrong_category", "not_in_acl", "no_drops", "unwanted_game",
     "expired_campaign", "upcoming_campaign", "expired_drop", "upcoming_drop",
     "prerequisite", "ignored", "claimed", "subscription"],
)
def test_override_keeps_other_mining_guards(blocker):
    twitch, campaign, channel = make_campaign(True)
    drop = next(iter(campaign.drops))
    now = datetime.now(timezone.utc)
    if blocker == "offline":
        channel = _channel(twitch, online=False)
    elif blocker == "wrong_category":
        channel = _channel(twitch, OTHER_GAME)
    elif blocker == "not_in_acl":
        channel = _channel(twitch, id=999)
    elif blocker == "no_drops":
        channel = _channel(twitch, drops_enabled=False)
    elif blocker == "unwanted_game":
        twitch.wanted_games = []
    elif blocker == "expired_campaign":
        campaign.ends_at = now - timedelta(seconds=1)
    elif blocker == "upcoming_campaign":
        campaign.starts_at = now + timedelta(hours=2)
    elif blocker == "expired_drop":
        drop.ends_at = now - timedelta(seconds=1)
    elif blocker == "upcoming_drop":
        drop.starts_at = now + timedelta(hours=2)
    elif blocker == "prerequisite":
        drop.precondition_drops = ["not-claimed"]
    elif blocker == "ignored":
        twitch.settings.drop_name_blacklist = [drop.name]
    elif blocker == "claimed":
        drop.is_claimed = True
    elif blocker == "subscription":
        drop.required_minutes = 0
    assert campaign.eligible
    assert not WatchService(twitch).can_watch(channel)
    assert not campaign.linked


def test_enabling_and_disabling_updates_existing_campaign_without_reload():
    twitch, campaign, channel = make_campaign()
    drop = next(iter(campaign.drops))
    assert not WatchService(twitch).can_watch(channel)
    twitch.settings.allow_unlinked_campaigns = True
    assert WatchService(twitch).can_watch(channel)
    drop.update_minutes(5)
    assert drop.real_current_minutes == 5
    campaign.bump_minutes(channel)
    assert drop.current_minutes == 6
    assert drop.real_current_minutes == 5
    twitch.settings.allow_unlinked_campaigns = False
    assert not WatchService(twitch).can_watch(channel)
    assert StreamSelector().get_wanted_games(twitch.settings, [campaign]) == []
    assert drop.current_minutes == 6
    assert not campaign.linked
