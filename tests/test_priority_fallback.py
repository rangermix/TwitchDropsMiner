"""Real campaign regressions for optional runtime priority-list fallback."""

import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.config import MAX_INT, State
from src.core.client import Twitch
from src.models.campaign import DropsCampaign
from src.models.channel import Channel, Stream
from src.services.channel_service import ChannelService
from src.services.stream_selector import StreamSelector
from src.services.watch_service import WatchService
from tests.test_watch_drop_filtering import _drop as watch_drop


def _settings(*games, priority_list_only=False, **overrides):
    return SimpleNamespace(
        games_to_watch=list(games),
        priority_list_only=priority_list_only,
        mining_benefits=overrides.pop("mining_benefits", {"DIRECT_ENTITLEMENT": True}),
        drop_name_blacklist=overrides.pop("drop_name_blacklist", []),
        allow_unlinked_campaigns=False,
        **overrides,
    )


def _drop(drop_id, *, benefit_type="DIRECT_ENTITLEMENT", has_benefit=True, **kwargs):
    data = watch_drop(drop_id, drop_id, kwargs.pop("required_minutes", 15), **kwargs)
    if has_benefit:
        data["benefitEdges"][0]["benefit"]["distributionType"] = benefit_type
    else:
        data["benefitEdges"] = []
    return data


def _campaign(
    campaign_id, game_name, *, game_id=1, settings=None, drops=None,
    starts_at=None, ends_at=None, linked=True, acl=(),
):
    now = datetime.now(timezone.utc)
    twitch = MagicMock()
    twitch.settings = settings or _settings()
    return DropsCampaign(
        twitch,
        {
            "id": campaign_id,
            "name": campaign_id,
            "game": {"id": str(game_id), "name": game_name},
            "self": {"isAccountConnected": linked},
            "accountLinkURL": "https://example.test/link",
            "startAt": (starts_at or now - timedelta(days=1)).isoformat(),
            "endAt": (ends_at or now + timedelta(days=1)).isoformat(),
            "status": "ACTIVE",
            "allow": {
                "channels": [{"id": str(channel_id), "name": f"channel{channel_id}"}
                             for channel_id in acl],
                "isEnabled": True,
            },
            "timeBasedDrops": drops if drops is not None else [_drop("Reward")],
        },
        {},
    )


def _names(tree):
    return [game["game_name"] for game in tree]


@pytest.mark.parametrize("strict", [True, False])
def test_saved_priority_order_is_case_insensitive_deduplicated_and_never_mutated(strict):
    settings = _settings("SECOND", "FIRST", "second", priority_list_only=strict)
    saved = list(settings.games_to_watch)
    inventory = [
        _campaign("extra", "Extra", game_id=3),
        _campaign("first", "First", game_id=1),
        _campaign("second", "Second", game_id=2),
    ]

    result = StreamSelector().get_wanted_games(settings, inventory)

    assert [game.name for game in result] == (
        ["Second", "First"] if strict else ["Second", "First", "Extra"]
    )
    assert settings.games_to_watch == saved


@pytest.mark.parametrize("strict", [True, False])
def test_empty_saved_list_only_uses_fallback_when_enabled(strict):
    settings = _settings(priority_list_only=strict)
    campaign = _campaign("available", "Available")

    result = StreamSelector().get_wanted_game_tree(settings, [campaign])

    assert _names(result) == ([] if strict else ["Available"])
    assert settings.games_to_watch == []


def test_fallback_order_uses_earliest_valid_campaign_then_case_insensitive_name():
    now = datetime.now(timezone.utc)
    early = now + timedelta(hours=3)
    later = now + timedelta(hours=8)
    settings = _settings("Priority")
    inventory = [
        _campaign("zulu", "Zulu", game_id=4, ends_at=early),
        _campaign("late-alpha", "alpha", game_id=2, ends_at=later),
        _campaign("invalid-beta", "Beta", game_id=3,
                  ends_at=now + timedelta(hours=1), linked=False),
        _campaign("valid-beta", "Beta", game_id=3, ends_at=later),
        _campaign("priority", "Priority", game_id=1, ends_at=later),
        _campaign("early-alpha", "alpha", game_id=2, ends_at=early),
    ]

    result = StreamSelector().get_wanted_game_tree(settings, inventory)

    assert _names(result) == ["Priority", "alpha", "Zulu", "Beta"]
    assert [campaign["id"] for campaign in result[1]["campaigns"]] == [
        "early-alpha", "late-alpha",
    ]
    assert [campaign["id"] for campaign in result[3]["campaigns"]] == ["valid-beta"]
    assert all(game["game_obj"] is None for game in result)


@pytest.mark.parametrize("selected", [True, False])
def test_wanted_subscription_reward_does_not_admit_unwanted_timed_rewards(selected):
    settings = _settings(
        *(["Mixed"] if selected else []),
        mining_benefits={"BADGE": True, "DIRECT_ENTITLEMENT": False},
    )
    campaign = _campaign("mixed", "Mixed", settings=settings, drops=[
        _drop("Subscribe", required_minutes=0, benefit_type="BADGE"),
        _drop("Unwanted watch reward"),
    ])

    assert StreamSelector().get_wanted_game_tree(settings, [campaign]) == []
    assert StreamSelector().get_wanted_games(settings, [campaign]) == []


@pytest.mark.parametrize("selected", [True, False])
def test_expired_wanted_reward_cannot_admit_an_empty_game_or_campaign(selected):
    now = datetime.now(timezone.utc)
    settings = _settings(
        *(["Mixed"] if selected else []),
        mining_benefits={"BADGE": True, "DIRECT_ENTITLEMENT": False},
    )
    campaign = _campaign("mixed", "Mixed", settings=settings, drops=[
        _drop("Expired badge", benefit_type="BADGE",
              ends_at=(now - timedelta(hours=1)).isoformat()),
        _drop("Unwanted watch reward"),
    ])

    assert campaign.can_earn_within(now + timedelta(hours=1)) is True
    assert StreamSelector().get_wanted_game_tree(settings, [campaign]) == []
    assert StreamSelector().get_wanted_games(settings, [campaign]) == []


def test_fallback_filters_expired_ignored_pruned_and_subscription_drops_together():
    now = datetime.now(timezone.utc)
    settings = _settings(drop_name_blacklist=["mask"])
    campaign = _campaign("mixed", "Fallback", settings=settings, drops=[
        _drop("Expired", ends_at=(now - timedelta(hours=1)).isoformat()),
        _drop("Subscribe", required_minutes=0),
        _drop("Unused prerequisite", has_benefit=False),
        _drop("Mask branch", precondition_drop_ids=["Unused prerequisite"]),
        _drop("Blocked child", precondition_drop_ids=["Mask branch"]),
        _drop("Shared prerequisite", has_benefit=False),
        _drop("Shared mask branch", precondition_drop_ids=["Shared prerequisite"]),
        _drop("Allowed branch", precondition_drop_ids=["Shared prerequisite"]),
        _drop("Active"),
        _drop("Upcoming", starts_at=(now + timedelta(minutes=30)).isoformat()),
        _drop("Sequential", precondition_drop_ids=["Active"]),
    ])

    result = StreamSelector().get_wanted_game_tree(settings, [campaign])

    assert _names(result) == ["Fallback"]
    assert [drop["name"] for drop in result[0]["campaigns"][0]["drops"]] == [
        "Allowed branch", "Active", "Upcoming", "Sequential",
    ]
    assert campaign.timed_drops["Shared prerequisite"].is_mineable is True
    assert campaign.timed_drops["Unused prerequisite"].is_mineable is False


def test_fallback_rejects_unlinked_out_of_horizon_and_all_expired_campaigns():
    now = datetime.now(timezone.utc)
    settings = _settings()
    inventory = [
        _campaign("unlinked", "Unlinked", linked=False),
        _campaign("too-far", "Future", game_id=2,
                  starts_at=now + timedelta(hours=2)),
        _campaign("expired-drops", "Expired", game_id=3, drops=[
            _drop("Expired", ends_at=(now - timedelta(hours=1)).isoformat()),
        ]),
        _campaign("upcoming", "Soon", game_id=4,
                  starts_at=now + timedelta(minutes=30), drops=[
                      _drop("Upcoming", starts_at=(now + timedelta(minutes=30)).isoformat()),
                  ]),
    ]

    assert _names(StreamSelector().get_wanted_game_tree(settings, inventory)) == ["Soon"]


def test_fallback_rejects_entirely_ignored_and_dependency_pruned_campaigns():
    settings = _settings(drop_name_blacklist=["mask"])
    campaign = _campaign("ignored", "Ignored", settings=settings, drops=[
        _drop("Unused prerequisite", has_benefit=False),
        _drop("Mask", precondition_drop_ids=["Unused prerequisite"]),
        _drop("Blocked child", precondition_drop_ids=["Mask"]),
    ])

    assert campaign.mineable_drop_ids == frozenset()
    assert StreamSelector().get_wanted_game_tree(settings, [campaign]) == []
    assert StreamSelector().get_wanted_games(settings, [campaign]) == []


def _channel(twitch, channel_id, game, *, online=True, drops_enabled=True):
    channel = Channel(twitch, id=channel_id, login=f"channel{channel_id}", acl_based=True)
    if online:
        channel._stream = Stream(channel, id=channel_id, game=game, viewers=10, title="Live")
        channel._stream.drops_enabled = drops_enabled
    return channel


def test_live_fallback_is_watchable_without_live_priority_stream_then_yields_to_priority():
    settings = _settings("Priority")
    selected = _campaign("priority", "Priority", settings=settings)
    fallback = _campaign("fallback", "Fallback", game_id=2, settings=settings)
    twitch = selected._twitch
    twitch.inventory = [fallback, selected]
    twitch.wanted_games = StreamSelector().get_wanted_games(settings, twitch.inventory)
    twitch._channel_service = ChannelService(twitch)
    service = WatchService(twitch)
    priority_channel = _channel(twitch, 100, None, online=False)
    fallback_channel = _channel(twitch, 200, {"id": "2", "name": "Fallback"})
    twitch.watching_channel.get_with_default.return_value = priority_channel

    assert [game.name for game in twitch.wanted_games] == ["Priority", "Fallback"]
    assert not service.can_watch(priority_channel)
    assert service.can_watch(fallback_channel)
    assert service.should_switch(fallback_channel)

    live_priority = _channel(twitch, 100, {"id": "1", "name": "Priority"})
    twitch.watching_channel.get_with_default.return_value = fallback_channel
    assert service.can_watch(live_priority)
    assert service.should_switch(live_priority)
    assert settings.games_to_watch == ["Priority"]


@pytest.mark.parametrize("special_id", [509663, 509672])
@pytest.mark.parametrize("playing", [{"id": "99", "name": "Other"}, None])
def test_selected_special_campaign_outranks_fallback_across_categories(special_id, playing):
    settings = _settings("SELECTED EVENT")
    selected = _campaign("special", "Selected Event", game_id=special_id,
                         settings=settings, acl=(100,))
    fallback = _campaign("fallback", "Fallback", game_id=2, settings=settings)
    twitch = selected._twitch
    twitch.inventory = [selected, fallback]
    twitch.wanted_games = StreamSelector().get_wanted_games(settings, twitch.inventory)
    twitch._channel_service = ChannelService(twitch)
    service = WatchService(twitch)
    selected_channel = _channel(twitch, 100, playing, drops_enabled=False)
    fallback_channel = _channel(twitch, 200, {"id": "2", "name": "Fallback"})

    assert service.can_watch(selected_channel)
    assert service.can_watch(fallback_channel)
    assert twitch._channel_service.get_priority(selected_channel) == MAX_INT
    ordered = sorted([fallback_channel, selected_channel],
                     key=twitch._channel_service.get_mining_priority)
    assert ordered == [selected_channel, fallback_channel]
    twitch.watching_channel.get_with_default.return_value = selected_channel
    assert service.should_switch(fallback_channel) is False
    twitch.watching_channel.get_with_default.return_value = fallback_channel
    assert service.should_switch(selected_channel) is True


@pytest.mark.parametrize("unavailable", ["offline", "claimed", "not_wanted"])
def test_unavailable_selected_campaign_allows_fallback(unavailable):
    settings = _settings("Selected Event")
    selected = _campaign("special", "Selected Event", game_id=509663,
                         settings=settings, acl=(100,))
    fallback = _campaign("fallback", "Fallback", game_id=2, settings=settings)
    twitch = selected._twitch
    twitch.inventory = [selected, fallback]
    twitch.wanted_games = [selected.game, fallback.game]
    twitch._channel_service = ChannelService(twitch)
    selected_channel = _channel(twitch, 100, None, online=unavailable != "offline")
    fallback_channel = _channel(twitch, 200, {"id": "2", "name": "Fallback"})
    if unavailable == "claimed":
        selected.timed_drops["Reward"].is_claimed = True
    elif unavailable == "not_wanted":
        twitch.wanted_games = [fallback.game]
    twitch.watching_channel.get_with_default.return_value = selected_channel

    assert twitch._channel_service.get_mining_priority(selected_channel)[0] == 1
    assert not WatchService(twitch).can_watch(selected_channel)
    assert WatchService(twitch).can_watch(fallback_channel)
    assert WatchService(twitch).should_switch(fallback_channel) is True


def test_strict_mode_preserves_streamed_category_priority_for_special_channels():
    settings = _settings("Selected Event", "Fallback", priority_list_only=True)
    selected = _campaign("special", "Selected Event", game_id=509663,
                         settings=settings, acl=(100,))
    fallback = _campaign("fallback", "Fallback", game_id=2, settings=settings)
    twitch = selected._twitch
    twitch.inventory = [selected, fallback]
    twitch.wanted_games = [selected.game, fallback.game]
    twitch._channel_service = ChannelService(twitch)
    selected_channel = _channel(twitch, 100, None, drops_enabled=False)
    fallback_channel = _channel(twitch, 200, {"id": "2", "name": "Fallback"})
    twitch.watching_channel.get_with_default.return_value = selected_channel

    assert twitch._channel_service.get_mining_priority(selected_channel) == (0, MAX_INT)
    assert twitch._channel_service.get_mining_priority(fallback_channel) == (0, 1)
    assert WatchService(twitch).should_switch(fallback_channel) is True


@pytest.mark.asyncio
@pytest.mark.parametrize("strict", [True, False])
@pytest.mark.parametrize("claim_state", [State.CHANNEL_SWITCH, State.INVENTORY_FETCH])
async def test_final_wanted_claim_recalculates_policy_and_releases_priority(strict, claim_state):
    settings = _settings(
        "Priority", priority_list_only=strict,
        mining_benefits={"BADGE": True, "DIRECT_ENTITLEMENT": False},
    )
    twitch = object.__new__(Twitch)
    twitch.settings = settings
    twitch.gui = MagicMock()
    twitch.drop_history = MagicMock()
    twitch._state = claim_state
    twitch._state_change = asyncio.Event()
    twitch._schedule_changed = asyncio.Event()
    twitch._games_update_pending = False
    twitch._inventory_loaded = True
    twitch.request_games_update = MagicMock(wraps=twitch.request_games_update)
    selected = _campaign("priority", "Priority", settings=settings, drops=[
        _drop("Wanted badge", benefit_type="BADGE"),
        _drop("Unwanted item"),
    ])
    fallback = _campaign("fallback", "Fallback", game_id=2, settings=settings, drops=[
        _drop("Fallback badge", benefit_type="BADGE"),
    ])
    for campaign in (selected, fallback):
        campaign._twitch = twitch
        for drop in campaign.drops:
            drop._twitch = twitch
    twitch.inventory = [selected, fallback]
    twitch._stream_selector = StreamSelector()
    twitch.wanted_games = twitch._stream_selector.get_wanted_games(settings, twitch.inventory)
    twitch._channel_service = ChannelService(twitch)
    twitch.watching_channel = MagicMock()
    current = _channel(twitch, 100, {"id": "1", "name": "Priority"})
    alternate = _channel(twitch, 200, {"id": "2", "name": "Fallback"})
    twitch.watching_channel.get_with_default.return_value = current
    service = WatchService(twitch)
    wanted_drop = selected.timed_drops["Wanted badge"]
    wanted_drop._claim = AsyncMock(side_effect=[False, True, True])
    wanted_drop._send_telegram_notification = AsyncMock()
    wanted_drop._send_discord_notification = AsyncMock()

    async def broadcast_after_queueing_update():
        assert twitch._games_update_pending is True
        assert twitch._state is claim_state  # The claim must not replace an active state step.

    twitch.gui.broadcast_wanted_items_now = AsyncMock(side_effect=broadcast_after_queueing_update)
    assert service.can_watch(current)
    assert service.should_switch(alternate) is False

    assert await wanted_drop.claim() is False
    assert twitch._games_update_pending is False
    twitch.request_games_update.assert_not_called()
    twitch.gui.broadcast_wanted_items_now.assert_not_awaited()

    assert await wanted_drop.claim() is True
    twitch.request_games_update.assert_called_once_with()
    assert twitch._schedule_changed.is_set()
    assert twitch._state_change.is_set()
    assert twitch._games_update_pending is True
    # Remaining unwanted rewards keep raw campaign eligibility true; a stale
    # wanted_games cache would therefore keep watching Priority indefinitely.
    assert selected.can_earn(current)
    assert service.can_watch(current)

    twitch._activate_pending_games_update()
    if claim_state is State.INVENTORY_FETCH:
        assert twitch._state is State.INVENTORY_FETCH
        twitch._state = State.CHANNEL_SWITCH
        twitch._activate_pending_games_update()
    assert twitch._state is State.GAMES_UPDATE
    # Apply the selector recalculation used by the real GAMES_UPDATE state step.
    twitch._games_update_pending = False
    twitch.wanted_games = twitch._stream_selector.get_wanted_games(settings, twitch.inventory)
    assert [game.name for game in twitch.wanted_games] == ([] if strict else ["Fallback"])
    assert service.can_watch(current) is False
    assert service.can_watch(alternate) is (not strict)
    if not strict:
        assert service.should_switch(alternate) is True
    assert settings.games_to_watch == ["Priority"]

    twitch._state_change.clear()
    twitch._schedule_changed.clear()
    assert await wanted_drop.claim() is True
    twitch.request_games_update.assert_called_once_with()
    assert twitch._games_update_pending is False
    assert not twitch._state_change.is_set()
    assert not twitch._schedule_changed.is_set()
    twitch.gui.broadcast_wanted_items_now.assert_awaited_once_with()
    twitch.drop_history.record.assert_called_once_with(wanted_drop, selected)
    wanted_drop._send_telegram_notification.assert_awaited_once_with()
    wanted_drop._send_discord_notification.assert_awaited_once_with()
