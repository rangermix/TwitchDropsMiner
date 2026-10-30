"""Issue #206: optionally prefer channels the account follows, within a game only."""

import asyncio
import logging
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.config import GQLRawQuery
from src.exceptions import MinerException
from src.models.channel import Channel
from src.models.game import Game
from src.services.channel_service import ChannelService
from src.web.managers.settings import SettingsManager


WANTED = Game({"id": 1, "name": "Wanted"})
OTHER = Game({"id": 2, "name": "Other"})


def make_twitch(*, enabled=True, followed=(), streams=None, drops=None):
    """Mock Twitch whose GQL answers follows, stream info and AvailableDrops."""
    streams = streams or {}
    drops = drops or {}
    twitch = MagicMock()
    twitch.settings = SimpleNamespace(prefer_followed_channels=enabled, priority_list_only=True,
                                      reward_priority=[], games_to_watch=["Wanted", "Other"])
    twitch.wanted_games = [WANTED, OTHER]
    twitch.inventory = []
    twitch.game_order = None
    twitch._manual_target_channel = None
    campaign = MagicMock()
    campaign.can_earn.return_value = True
    twitch._campaigns = {"camp": campaign}

    async def gql_request(request):
        if isinstance(request, GQLRawQuery):
            nodes = [{"node": {"id": str(cid), "login": f"user{cid}", "displayName": f"User{cid}"}}
                     for cid in followed]
            return {"data": {"currentUser": {"id": "9", "followedLiveUsers": {"edges": nodes}}}}
        if isinstance(request, list):  # batched GetStreamInfo
            rows = []
            for op in request:
                cid = int(op["variables"]["channel"].removeprefix("user"))
                game = streams.get(cid)
                rows.append({"data": {"user": {
                    "id": str(cid),
                    "stream": {"id": str(cid * 10), "viewersCount": 5} if game else None,
                    "broadcastSettings": {"game": {"id": game.id, "name": game.name} if game else None,
                                          "title": "t"},
                }}})
            return rows
        cid = int(request["variables"]["channelID"])  # AvailableDrops
        if drops.get(cid) == "error":
            raise MinerException("boom")
        campaigns = [{"id": "camp"}] if drops.get(cid) else []
        return {"data": {"channel": {"viewerDropCampaigns": campaigns}}}

    twitch.gql_request = AsyncMock(side_effect=gql_request)
    return twitch


def channel(twitch, cid, game=WANTED):
    result = Channel(twitch, id=cid, login=f"user{cid}")
    result._stream = None if game is None else MagicMock(game=game, drops_enabled=True)
    return result


def test_disabled_keeps_existing_priority_and_skips_lookup():
    twitch = make_twitch(enabled=False, followed=[1])
    service = ChannelService(twitch)
    service._followed_ids = {1}
    assert asyncio.run(service.get_followed_live_channels()) == set()
    twitch.gql_request.assert_not_awaited()
    assert service._followed_ids == set()
    assert service.get_mining_priority(channel(twitch, 1)) == (0, 0)


def test_followed_wins_only_between_channels_of_equal_game_priority():
    twitch = make_twitch()
    service = ChannelService(twitch)
    service._followed_ids = {1, 3}
    followed_wanted, plain_wanted = channel(twitch, 1), channel(twitch, 2)
    followed_other = channel(twitch, 3, OTHER)
    assert service.get_mining_priority(followed_wanted) < service.get_mining_priority(plain_wanted)
    # Game order stays first: a followed channel on a lower game never jumps ahead.
    assert service.get_mining_priority(plain_wanted) < service.get_mining_priority(followed_other)


def test_lookup_adds_only_confirmed_drops_channels_and_skips_known_ones():
    twitch = make_twitch(followed=[1, 2, 3, 4, 5],
                         streams={1: WANTED, 2: OTHER, 3: None, 4: WANTED, 5: WANTED},
                         drops={1: True, 2: True, 4: False, 5: True})
    twitch.wanted_games = [WANTED]
    service = ChannelService(twitch)
    found = asyncio.run(service.get_followed_live_channels(known_ids={5}))
    # Offline, unwanted-game and drops-disabled channels are never tracked: a later
    # stream refresh resets drops_enabled to True, which would make them watchable.
    assert {ch.id for ch in found} == {1}
    assert service._followed_ids == {1, 2, 3, 4, 5}, "known followed channels still get the tie-break"
    checked = [call.args[0]["variables"]["channelID"] for call in twitch.gql_request.await_args_list
               if isinstance(call.args[0], dict) and "channelID" in call.args[0].get("variables", {})]
    assert "5" not in checked, "already tracked channels are not checked again"


def test_manual_mode_is_never_overridden_by_the_tie_break():
    twitch = make_twitch()
    service = ChannelService(twitch)
    service._followed_ids = {1}
    twitch._manual_target_channel = channel(twitch, 2)
    assert service.get_mining_priority(channel(twitch, 1)) == service.get_mining_priority(channel(twitch, 2))


def test_exit_during_drops_checks_propagates():
    from src.exceptions import ExitRequest
    twitch = make_twitch(followed=[1], streams={1: WANTED})
    original = twitch.gql_request.side_effect

    async def exiting(request):
        if isinstance(request, dict) and "channelID" in request.get("variables", {}):
            raise ExitRequest()
        return await original(request)
    twitch.gql_request.side_effect = exiting
    with pytest.raises(ExitRequest):
        asyncio.run(ChannelService(twitch).get_followed_live_channels())


@pytest.mark.parametrize("failure", ["transport", "malformed", "drops_error", "drops_malformed"])
def test_failures_add_nothing_and_never_log_details(failure, caplog):
    twitch = make_twitch(followed=[1], streams={1: WANTED}, drops={1: "error"})
    service = ChannelService(twitch)
    service._followed_ids = {7}
    if failure == "transport":
        twitch.gql_request.side_effect = MinerException("secret-body")
    elif failure == "malformed":
        twitch.gql_request.side_effect = AsyncMock(return_value={"data": {"currentUser": None}})
    elif failure == "drops_malformed":
        original = twitch.gql_request.side_effect

        async def bad_drops(request):
            if isinstance(request, dict) and "channelID" in request.get("variables", {}):
                return {"data": {"channel": None}}
            return await original(request)
        twitch.gql_request.side_effect = bad_drops
    with caplog.at_level(1, logger="TwitchDrops"):
        result = asyncio.run(service.get_followed_live_channels())
    if failure.startswith("drops_"):
        # A failed or malformed AvailableDrops check never adds the channel.
        assert result == set()
        assert service._followed_ids == {1}
    else:
        assert result == set()
        assert service._followed_ids == {7}, "a failed lookup keeps the previous follow set"
    assert "secret-body" not in caplog.text
    assert "user1" not in caplog.text and "User1" not in caplog.text, "never log followed names"


def test_account_reset_clears_follow_state():
    service = ChannelService(make_twitch())
    service._followed_ids = {1}
    service.clear_bans()
    assert service._followed_ids == set()


def _manager(enabled=False):
    stored = SimpleNamespace(prefer_followed_channels=enabled, save=MagicMock())
    on_change = MagicMock()
    manager = SettingsManager(SimpleNamespace(emit=AsyncMock()), stored, MagicMock(), on_change)
    return manager, stored, on_change


def test_setting_defaults_off_validates_and_refreshes_selection():
    from src.config.settings import default_settings
    assert default_settings["prefer_followed_channels"] is False
    manager, stored, on_change = _manager()
    with pytest.raises(ValueError):
        manager.update_settings({"prefer_followed_channels": "yes"})
    stored.save.assert_not_called()
    assert stored.prefer_followed_channels is False

    async def enable():
        return manager.update_settings({"prefer_followed_channels": True})
    asyncio.run(enable())
    assert stored.prefer_followed_channels is True
    stored.save.assert_called_once()
    on_change.assert_called_once()
