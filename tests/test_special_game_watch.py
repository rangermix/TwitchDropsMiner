"""Regression coverage for issue #103 and the upstream special-category exceptions."""

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

import pytest

from src.config import MAX_INT
from src.models.campaign import DropsCampaign
from src.models.channel import Channel, Stream
from src.models.game import Game
from src.services.channel_service import ChannelService
from src.services.inventory_service import InventoryService
from src.services.watch_service import WatchService


SPECIAL_EVENTS = {"id": "509663", "name": "Special Events"}
IRL = {"id": "509672", "name": "IRL"}
TEST_GAME = {"id": "1", "name": "Test Game"}
OTHER_GAME = {"id": "2", "name": "Other Game"}
CHANNEL_ID = 100


@pytest.fixture
def twitch():
    client = MagicMock()
    client.settings.drop_name_blacklist = []
    client.settings.telegram_bot_token = ""
    client.settings.telegram_chat_id = ""
    client.watching_channel.get_with_default.side_effect = lambda default: default
    return client


def _campaign(twitch, game, *, acl=True, acl_enabled=True, benefit_type="EMOTE"):
    now = datetime.now(timezone.utc)
    start = (now - timedelta(days=1)).isoformat()
    end = (now + timedelta(days=1)).isoformat()
    campaign = DropsCampaign(
        twitch,
        {
            "id": f"campaign-{game['id']}",
            "name": "Event campaign",
            "game": game,
            "self": {"isAccountConnected": False},
            "accountLinkURL": "https://example.test/link",
            "startAt": start,
            "endAt": end,
            "status": "ACTIVE",
            "allow": {
                "channels": [{"id": str(CHANNEL_ID), "name": "eventchannel"}] if acl else [],
                "isEnabled": acl_enabled,
            },
            "timeBasedDrops": [
                {
                    "id": "event-drop",
                    "name": "Event Reward",
                    "benefitEdges": [
                        {
                            "benefit": {
                                "id": "event-benefit",
                                "name": "Event Reward",
                                "distributionType": benefit_type,
                                "imageAssetURL": "https://example.test/reward.png",
                            }
                        }
                    ],
                    "startAt": start,
                    "endAt": end,
                    "preconditionDrops": [],
                    "requiredMinutesWatched": 30,
                }
            ],
        },
        {},
    )
    twitch.inventory = [campaign]
    twitch.wanted_games = [campaign.game]
    return campaign


def _channel(twitch, playing=TEST_GAME, *, id=CHANNEL_ID, online=True, drops_enabled=True):
    channel = Channel(twitch, id=id, login=f"channel{id}", acl_based=True)
    if online:
        channel._stream = Stream(channel, id=1, game=playing, viewers=10, title="live")
        channel._stream.drops_enabled = drops_enabled
    return channel


@pytest.mark.parametrize(
    ("game", "expected"), [(SPECIAL_EVENTS, True), (IRL, True), (TEST_GAME, False)]
)
def test_special_categories_are_identified_by_id(game, expected):
    assert Game({**game, "name": "Localized category name"}).is_special() is expected


@pytest.mark.parametrize("game", [SPECIAL_EVENTS, IRL])
@pytest.mark.parametrize("playing", [TEST_GAME, None])
@pytest.mark.parametrize("drops_enabled", [True, False])
def test_special_campaign_progresses_on_live_acl_channel(twitch, game, playing, drops_enabled):
    campaign = _campaign(twitch, game)
    channel = _channel(twitch, playing, drops_enabled=drops_enabled)
    drop = campaign.timed_drops["event-drop"]

    assert campaign.can_earn(channel)
    assert WatchService(twitch).can_watch(channel)
    # The watch loop and websocket updates use the same campaign/drop eligibility.
    assert InventoryService(twitch).get_active_campaign(channel) is campaign
    assert drop.can_earn(channel)


@pytest.mark.parametrize("game", [SPECIAL_EVENTS, IRL])
def test_special_campaign_still_requires_live_channel(twitch, game):
    campaign = _campaign(twitch, game)
    channel = _channel(twitch, online=False)

    assert not campaign.can_earn(channel)
    assert not campaign.timed_drops["event-drop"].can_earn(channel)
    assert not WatchService(twitch).can_watch(channel)
    # Discovery can explicitly check eligibility before stream status is populated.
    assert campaign.can_earn(channel, ignore_channel_status=True)
    assert campaign.can_earn()


@pytest.mark.parametrize("game", [SPECIAL_EVENTS, IRL])
@pytest.mark.parametrize("ignore_channel_status", [False, True])
def test_special_campaign_rejects_channel_outside_acl(twitch, game, ignore_channel_status):
    campaign = _campaign(twitch, game)
    channel = _channel(twitch, game, id=CHANNEL_ID + 1)

    assert not campaign.can_earn(channel, ignore_channel_status=ignore_channel_status)
    assert not WatchService(twitch).can_watch(channel)


@pytest.mark.parametrize("game", [SPECIAL_EVENTS, IRL])
@pytest.mark.parametrize(("acl", "acl_enabled"), [(False, True), (True, False)])
def test_special_campaign_without_enabled_acl_requires_matching_game(
    twitch, game, acl, acl_enabled
):
    campaign = _campaign(twitch, game, acl=acl, acl_enabled=acl_enabled)
    channel = _channel(twitch)

    assert not campaign.can_earn(channel)
    assert not WatchService(twitch).can_watch(channel)
    matching_channel = _channel(twitch, game, drops_enabled=False)
    assert campaign.can_earn(matching_channel)
    assert WatchService(twitch).can_watch(matching_channel)


@pytest.mark.parametrize("wanted", [[], [TEST_GAME]])
def test_special_campaign_must_be_wanted_even_if_streamed_game_is_wanted(twitch, wanted):
    campaign = _campaign(twitch, SPECIAL_EVENTS)
    twitch.wanted_games = [Game(game) for game in wanted]
    channel = _channel(twitch)

    assert campaign.can_earn(channel)
    assert not WatchService(twitch).can_watch(channel)


def test_unwanted_special_campaign_does_not_make_unearnable_wanted_campaign_watchable(twitch):
    special = _campaign(twitch, SPECIAL_EVENTS)
    regular = _campaign(twitch, TEST_GAME)
    regular.timed_drops["event-drop"].is_claimed = True
    twitch.inventory = [special, regular]

    assert not WatchService(twitch).can_watch(_channel(twitch))


@pytest.mark.parametrize("acl", [False, True])
@pytest.mark.parametrize(
    ("playing", "drops_enabled", "expected"),
    [
        (TEST_GAME, True, True),
        (TEST_GAME, False, False),
        (OTHER_GAME, True, False),
        (None, True, False),
    ],
)
def test_regular_campaign_keeps_game_and_drops_enabled_checks(
    twitch, acl, playing, drops_enabled, expected
):
    _campaign(twitch, TEST_GAME, acl=acl)

    assert (
        WatchService(twitch).can_watch(_channel(twitch, playing, drops_enabled=drops_enabled))
        is expected
    )


@pytest.mark.parametrize(
    "blocker",
    [
        "expired_campaign",
        "upcoming_campaign",
        "invalid_campaign",
        "expired_drop",
        "upcoming_drop",
        "claimed",
        "ignored",
        "unlinked",
        "missing_precondition",
        "subscription",
    ],
)
def test_special_campaign_preserves_earning_requirements(twitch, blocker):
    campaign = _campaign(
        twitch,
        SPECIAL_EVENTS,
        benefit_type="DIRECT_ENTITLEMENT" if blocker == "unlinked" else "EMOTE",
    )
    drop = campaign.timed_drops["event-drop"]
    now = datetime.now(timezone.utc)
    match blocker:
        case "expired_campaign":
            campaign.ends_at = now - timedelta(seconds=1)
        case "upcoming_campaign":
            campaign.starts_at = now + timedelta(hours=1)
        case "invalid_campaign":
            campaign._valid = False
        case "expired_drop":
            drop.ends_at = now - timedelta(seconds=1)
        case "upcoming_drop":
            drop.starts_at = now + timedelta(hours=1)
        case "claimed":
            drop.is_claimed = True
        case "ignored":
            twitch.settings.drop_name_blacklist = ["Event Reward"]
        case "missing_precondition":
            drop.precondition_drops = ["missing-drop"]
        case "subscription":
            drop.required_minutes = 0

    channel = _channel(twitch)
    assert not campaign.can_earn(channel)
    assert not drop.can_earn(channel)
    assert not WatchService(twitch).can_watch(channel)


def test_special_only_channel_keeps_fallback_priority(twitch):
    special = _campaign(twitch, SPECIAL_EVENTS)
    regular = _campaign(twitch, TEST_GAME)
    twitch.inventory = [special, regular]
    twitch.wanted_games = [special.game, regular.game]
    special_channel = _channel(twitch, OTHER_GAME)
    regular_channel = _channel(twitch, TEST_GAME)
    twitch._channel_service = ChannelService(twitch)
    watch_service = WatchService(twitch)

    assert watch_service.can_watch(special_channel)
    assert twitch._channel_service.get_priority(special_channel) == MAX_INT
    assert watch_service.should_switch(special_channel)
    twitch.watching_channel.get_with_default.side_effect = lambda default: regular_channel
    assert not watch_service.should_switch(special_channel)
    twitch.watching_channel.get_with_default.side_effect = lambda default: special_channel
    assert watch_service.should_switch(regular_channel)


@pytest.mark.parametrize("reason", ["offline", "expired_campaign", "deselected_campaign"])
def test_live_special_participant_replaces_unwatchable_current_channel(twitch, reason):
    candidate_campaign = _campaign(twitch, SPECIAL_EVENTS)
    current_campaign = _campaign(twitch, IRL)
    candidate = _channel(twitch, TEST_GAME)
    current = _channel(twitch, OTHER_GAME, id=CHANNEL_ID + 1)
    current_campaign.allowed_channels = [current]
    twitch.inventory = [candidate_campaign, current_campaign]
    twitch.wanted_games = [candidate_campaign.game, current_campaign.game]
    twitch._channel_service = ChannelService(twitch)
    twitch.watching_channel.get_with_default.side_effect = lambda default: current
    watch_service = WatchService(twitch)

    assert watch_service.can_watch(current)
    assert watch_service.can_watch(candidate)
    assert twitch._channel_service.get_priority(current) == MAX_INT
    assert twitch._channel_service.get_priority(candidate) == MAX_INT
    # Equal-priority participants should not disrupt a healthy current stream.
    assert not watch_service.should_switch(candidate)

    if reason == "offline":
        current._stream = None
    elif reason == "expired_campaign":
        current_campaign.ends_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    else:
        twitch.wanted_games = [candidate_campaign.game]

    assert not watch_service.can_watch(current)
    assert watch_service.can_watch(candidate)
    assert watch_service.should_switch(candidate)
