"""Issue #206: optionally prefer channels the account follows, within a game only."""

import asyncio
import logging
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.api.gql_client import GQLClient
from src.config import ClientType, GQLRawQuery
from src.exceptions import MinerException
from src.models.channel import Channel
from src.models.game import Game
from src.services.channel_service import ChannelService
from src.services.watch_service import WatchService
from src.web.managers.settings import SettingsManager
from tests.test_web_auth import client  # noqa: F401 -- shared isolated production HTTP fixture


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
    twitch.watching_channel.get_with_default.return_value = None
    campaign = MagicMock(game=WANTED)
    campaign.can_earn.return_value = True
    twitch._campaigns = {"camp": campaign}

    async def gql_request(request):
        if isinstance(request, GQLRawQuery):
            if "PublicFixture" in request["query"]:
                return {"data": {"test": True}}
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
                    "displayName": f"User{cid}",
                    "stream": {"id": str(cid * 10), "viewersCount": 5} if game else None,
                    "broadcastSettings": {"game": {"id": game.id, "name": game.name} if game else None,
                                          "title": "t"},
                }}})
            return rows
        if request["operationName"] == "VideoPlayerStreamInfoOverlayChannel":
            rows = await gql_request([request])
            return rows[0]
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


def award(twitch, cid, game, eligible):
    drop = MagicMock(id=f"drop-{cid}")
    drop.can_earn.side_effect = lambda channel=None: channel in eligible
    campaign = MagicMock(id=f"campaign-{cid}", game=game, mineable_watch_drops=(drop,))
    campaign.can_earn.side_effect = lambda channel=None: channel in eligible
    twitch.inventory.append(campaign)


def test_followed_wins_only_between_channels_of_equal_game_priority():
    twitch = make_twitch()
    service = twitch._channel_service = ChannelService(twitch)
    service._followed_ids = {1, 3}
    followed_wanted, plain_wanted = channel(twitch, 1), channel(twitch, 2)
    followed_other = channel(twitch, 3, OTHER)
    award(twitch, 1, WANTED, {followed_wanted, plain_wanted})
    award(twitch, 2, OTHER, {followed_other})
    order = [plain_wanted, followed_wanted, followed_other]
    service.prioritize_followed(order)
    assert order == [followed_wanted, plain_wanted, followed_other]
    twitch.watching_channel.get_with_default.return_value = plain_wanted
    assert WatchService(twitch).should_switch(followed_wanted)
    assert not WatchService(twitch).should_switch(followed_other)



def test_lookup_adds_only_confirmed_drops_channels_and_skips_known_ones():
    twitch = make_twitch(followed=[1, 2, 3, 4, 5],
                         streams={1: WANTED, 2: OTHER, 3: None, 4: WANTED, 5: WANTED},
                         drops={1: True, 2: True, 4: False, 5: True})
    twitch.wanted_games = [WANTED]
    service = ChannelService(twitch)
    found = asyncio.run(service.get_followed_live_channels(known_ids={5}))
    # Offline, unwanted-game and drops-disabled channels are never added.
    assert {ch.id for ch in found} == {1}
    assert service._followed_ids == {1, 2, 3, 4, 5}, "known followed channels still get the tie-break"
    checked = [call.args[0]["variables"]["channelID"] for call in twitch.gql_request.await_args_list
               if isinstance(call.args[0], dict) and "channelID" in call.args[0].get("variables", {})]
    assert "5" not in checked, "already tracked channels are not checked again"


def test_acl_channels_keep_precedence_over_followed_ones():
    twitch = make_twitch()
    service = twitch._channel_service = ChannelService(twitch)
    service._followed_ids = {1}
    followed, acl, plain = channel(twitch, 1), channel(twitch, 2), channel(twitch, 3)
    acl.acl_based = True
    award(twitch, 1, WANTED, {followed, acl, plain})
    order = [acl, plain, followed]
    service.prioritize_followed(order)
    assert order == [acl, followed, plain]
    twitch.watching_channel.get_with_default.return_value = acl
    assert not WatchService(twitch).should_switch(followed)


@pytest.mark.parametrize("shared", [False, True])
def test_followed_cannot_displace_a_stream_with_different_rewards(shared):
    twitch = make_twitch()
    service = twitch._channel_service = ChannelService(twitch)
    service._followed_ids = {1}
    followed, with_badge, same_as_followed = (
        channel(twitch, 1), channel(twitch, 2), channel(twitch, 3),
    )
    # Equal-sized disjoint rewards and a strict superset are both distinct.
    eligible = {followed, same_as_followed}
    if shared:
        eligible.add(with_badge)
    award(twitch, 1, WANTED, eligible)
    award(twitch, 2, WANTED, {with_badge})
    order = [same_as_followed, with_badge, followed]
    service.prioritize_followed(order)
    assert order == [followed, with_badge, same_as_followed]
    twitch.watching_channel.get_with_default.return_value = with_badge
    assert not WatchService(twitch).should_switch(followed)
    twitch.watching_channel.get_with_default.return_value = same_as_followed
    assert WatchService(twitch).should_switch(followed)


def test_a_same_game_click_is_not_undone_by_the_tie_break():
    twitch = make_twitch()
    service = twitch._channel_service = ChannelService(twitch)
    service._followed_ids = {1}
    followed, picked = channel(twitch, 1), channel(twitch, 2)
    award(twitch, 1, WANTED, {followed, picked})
    service.picked_channel_id = 2
    twitch.watching_channel.get_with_default.return_value = picked
    order = [picked, followed]
    service.prioritize_followed(order)
    assert order == [picked, followed]
    assert not WatchService(twitch).should_switch(followed)
    WatchService(twitch).stop_watching()
    assert service.picked_channel_id is None
    service.prioritize_followed(order)
    assert order == [followed, picked]



def test_pick_protection_ends_when_another_channel_is_watched():
    service = ChannelService(make_twitch())
    picked, other = channel(service._twitch, 2), channel(service._twitch, 3)
    service.record_watch(picked, picked=True)
    service.record_watch(picked)  # the same channel staying watched keeps it
    assert service.picked_channel_id == 2
    service.record_watch(other)  # an automatic switch away ends it
    assert service.picked_channel_id is None
    service.record_watch(picked)  # an automatic return later is not a pick
    assert service.picked_channel_id is None


def test_every_watch_goes_through_record_watch():
    from src.services.watch_service import WatchService
    twitch = MagicMock()
    twitch.is_manual_mode.return_value = False
    target = channel(twitch, 5)
    WatchService(twitch).watch(target, update_status=False)
    twitch._channel_service.record_watch.assert_called_once_with(target)


def test_manual_mode_is_never_overridden_by_the_tie_break():
    twitch = make_twitch()
    service = ChannelService(twitch)
    service._followed_ids = {1}
    followed, picked = channel(twitch, 1), channel(twitch, 2)
    award(twitch, 1, WANTED, {followed, picked})
    twitch._manual_target_channel = picked
    twitch.watching_channel.get_with_default.return_value = picked
    assert not service.prefer_followed_switch(followed, picked)


@pytest.mark.parametrize("malformed", ["", {}, False])
def test_followed_channel_rechecks_drop_eligibility_on_later_stream_updates(malformed):
    twitch = make_twitch(followed=[1], streams={1: WANTED}, drops={1: True})
    service = ChannelService(twitch)

    async def run():
        found, = await service.get_followed_live_channels()
        assert found.drops_enabled
        # The same broadcast may cease to offer drops.
        original = twitch.gql_request.side_effect

        campaigns = malformed

        async def changed_drops(request):
            if isinstance(request, dict) and request.get("operationName") == "DropsHighlightService_AvailableDrops":
                return {"data": {"channel": {"viewerDropCampaigns": campaigns}}}
            return await original(request)

        twitch.gql_request.side_effect = changed_drops
        await found.refresh_stream()
        assert found.online and found.drops_enabled
        campaigns = []
        await found.refresh_stream()
        assert found.online and not found.drops_enabled
        discovered = Channel(twitch, id=1, login="user1")
        discovered.external_update({
            "stream": {"id": "10", "viewersCount": 100},
            "broadcastSettings": {"game": {"id": WANTED.id, "name": WANTED.name}, "title": "t"},
        }, [])
        assert discovered.drops_enabled
        discovered.sync_stream_from(found)
        assert discovered.online and not discovered.drops_enabled

    asyncio.run(run())


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["disable", "reset"])
async def test_discovery_drops_inflight_results_after_disable_or_reset(change):
    twitch = make_twitch(followed=[1], streams={1: WANTED}, drops={1: True})
    service = ChannelService(twitch)
    entered, release = asyncio.Event(), asyncio.Event()
    original = twitch.gql_request.side_effect

    async def pending(request):
        if isinstance(request, GQLRawQuery):
            entered.set()
            await release.wait()
        return await original(request)

    twitch.gql_request.side_effect = pending
    async with asyncio.TaskGroup() as tasks:
        run = tasks.create_task(service.get_followed_live_channels())
        await asyncio.wait_for(entered.wait(), 1)
        if change == "disable":
            twitch.settings.prefer_followed_channels = False
        else:
            service.clear_bans()
        release.set()
    assert run.result() == set()
    assert service._followed_ids == set()


@pytest.mark.parametrize("operation", ["follows", "stream", "malformed_stream", "malformed_edges"])
def test_partial_responses_do_not_replace_previous_follows(operation):
    twitch = make_twitch(followed=[1], streams={1: WANTED}, drops={1: True})
    service = ChannelService(twitch)
    service._followed_ids = {7}
    original = twitch.gql_request.side_effect

    async def partial(request):
        result = await original(request)
        if isinstance(request, GQLRawQuery) and operation == "follows":
            result["errors"] = [{"message": "server error"}]
        if isinstance(request, GQLRawQuery) and operation == "malformed_edges":
            result["data"]["currentUser"]["followedLiveUsers"]["edges"] = ""
        if isinstance(request, list) and operation == "stream":
            result[0]["errors"] = [{"message": "server error"}]
        if isinstance(request, list) and operation == "malformed_stream":
            result[0] = None
        return result

    twitch.gql_request.side_effect = partial
    assert asyncio.run(service.get_followed_live_channels()) == set()
    assert service._followed_ids == {7}


def test_real_graphql_logger_excludes_follows_and_descendant_lookups(caplog):
    twitch = make_twitch(followed=[321], streams={321: WANTED}, drops={321: True})
    browser = SimpleNamespace(gql=twitch.gql_request)
    auth = SimpleNamespace(validate=AsyncMock(return_value=SimpleNamespace(
        browser_active=True, _twitch=SimpleNamespace(_browser=browser),
    )))
    gql = GQLClient(MagicMock(), auth, ClientType.WEB)
    twitch.gql_request = gql.request
    with caplog.at_level(logging.DEBUG, logger="TwitchDrops.gql"):
        found = asyncio.run(ChannelService(twitch).get_followed_live_channels())
        asyncio.run(gql.request(GQLRawQuery("query PublicFixture { test }")))
    assert {channel.id for channel in found} == {321}
    assert "user321" not in caplog.text
    assert "User321" not in caplog.text
    assert "FollowedLiveChannels" not in caplog.text
    assert "VideoPlayerStreamInfoOverlayChannel" not in caplog.text
    assert "PublicFixture" in caplog.text


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


def test_slow_lookup_times_out_without_adding_or_forgetting(monkeypatch, caplog):
    twitch = make_twitch(followed=[1], streams={1: WANTED}, drops={1: True})

    async def hang(request):
        await asyncio.sleep(60)
    twitch.gql_request.side_effect = hang
    service = ChannelService(twitch)
    service._followed_ids = {7}
    monkeypatch.setattr(ChannelService, "FOLLOW_LOOKUP_TIMEOUT", 0.01)
    assert asyncio.run(service.get_followed_live_channels()) == set()
    assert service._followed_ids == {7}
    assert "lookup failed" in caplog.text


@pytest.mark.parametrize("value", ["yes", "true", 1, {"on": True}])
def test_api_rejects_non_boolean_values(client, value):  # noqa: F811
    response = client.post("/api/settings", json={"prefer_followed_channels": value})
    assert response.status_code == 422
    assert response.json() == {"detail": "invalid_request"}


def test_setting_round_trips_through_saved_settings(tmp_path, monkeypatch):
    from src.config.settings import Settings
    monkeypatch.setattr("src.config.settings.SETTINGS_PATH", tmp_path / "settings.json")
    settings = Settings()
    assert settings.prefer_followed_channels is False

    async def enable():
        SettingsManager(AsyncMock(), settings, MagicMock()).update_settings({"prefer_followed_channels": True})
    asyncio.run(enable())
    assert Settings().prefer_followed_channels is True


@pytest.mark.asyncio
async def test_mining_opt_ins_revision_persists_and_rolls_back(tmp_path, monkeypatch):
    from src.config.settings import Settings
    settings_path = tmp_path / "settings.json"
    monkeypatch.setattr("src.config.settings.SETTINGS_PATH", settings_path)
    settings_path.write_text('{"allow_unlinked_campaigns":true}', encoding="utf-8")
    settings = Settings()
    broadcaster = AsyncMock()
    manager = SettingsManager(broadcaster, settings, MagicMock())

    def snapshot(values):
        return (
            values["mining_opt_ins_revision"],
            values["allow_unlinked_campaigns"],
            values["prefer_followed_channels"],
        )

    assert snapshot(manager.get_settings()) == (0, True, False)
    first = manager.update_settings({"prefer_followed_channels": True})
    second = manager.update_settings({
        "allow_unlinked_campaigns": False,
        "prefer_followed_channels": False,
    })
    unchanged = manager.update_settings({"prefer_followed_channels": False, "dark_mode": True})
    await asyncio.sleep(0)
    assert snapshot(first) == (1, True, True)
    assert snapshot(second) == snapshot(unchanged) == (2, False, False)
    assert [snapshot(call.args[1]) for call in broadcaster.emit.await_args_list] == [
        (1, True, True), (2, False, False), (2, False, False),
    ]
    assert snapshot(vars(Settings())) == (2, False, False)

    monkeypatch.setattr(settings, "save", MagicMock(side_effect=OSError))
    with pytest.raises(OSError):
        manager.update_settings({"allow_unlinked_campaigns": True, "prefer_followed_channels": True})
    assert snapshot(manager.get_settings()) == snapshot(vars(Settings())) == (2, False, False)
    await asyncio.sleep(0)
    assert broadcaster.emit.await_count == 3
