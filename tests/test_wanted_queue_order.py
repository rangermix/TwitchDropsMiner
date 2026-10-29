"""Temporary dashboard queue order: runtime-only, ahead of the saved Mining list default."""

import asyncio
import importlib
from collections import deque
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient

from src.core.client import Twitch
from src.services.channel_service import ChannelService
from src.services.game_order import TemporaryGameOrder
from src.services.inventory_service import InventoryService
from src.services.stream_selector import StreamSelector
from src.web.managers.settings import SettingsManager
from src.web.origin import DashboardOrigin
from tests.test_priority_fallback import _campaign, _channel, _names, _settings


web = importlib.import_module("src.web.app")


def ordered(*names):
    order = TemporaryGameOrder()
    order.set(list(names))
    return order


class TestTemporaryGameOrder:
    def test_normalize_strips_drops_blanks_and_case_insensitive_duplicates(self):
        assert TemporaryGameOrder.normalize([" Rust ", "", "rust", "Minecraft", "  "]) == ["Rust", "Minecraft"]

    @pytest.mark.parametrize("value", [
        "Rust", None, [1], ["ok", None], ["x" * 201], ["g%d" % i for i in range(201)],
    ])
    def test_normalize_rejects_invalid_input(self, value):
        with pytest.raises(ValueError):
            TemporaryGameOrder.normalize(value)

    def test_rank_listed_first_then_everything_else(self):
        order = ordered("Second", "First")
        assert [order.rank(name) for name in ("first", "SECOND", "Other", None)] == [1, 0, 2, 2]
        assert order.info() == {"active": True, "games": ["Second", "First"]}
        assert order.clear() is True
        assert order.clear() is False
        assert order.info() == {"active": False, "games": []}


class TestQueueOrder:
    def campaigns(self, settings):
        return [_campaign("a", "Alpha", game_id=1, settings=settings),
                _campaign("b", "Beta", game_id=2, settings=settings),
                _campaign("c", "Gamma", game_id=3, settings=settings)]

    def test_temporary_order_reorders_without_changing_saved_list(self):
        settings = _settings("Alpha", "Beta", "Gamma", priority_list_only=True)
        campaigns = self.campaigns(settings)
        tree = StreamSelector().get_wanted_game_tree(settings, campaigns, ordered("gamma", "Alpha"))
        assert _names(tree) == ["Gamma", "Alpha", "Beta"]
        assert settings.games_to_watch == ["Alpha", "Beta", "Gamma"]
        assert _names(StreamSelector().get_wanted_game_tree(settings, campaigns)) == ["Alpha", "Beta", "Gamma"]

    def test_order_never_adds_games_outside_the_queue(self):
        settings = _settings("Alpha", priority_list_only=True)
        campaigns = self.campaigns(settings)
        games = StreamSelector().get_wanted_games(settings, campaigns, ordered("Gamma", "Unknown", "Alpha"))
        assert [game.name for game in games] == ["Alpha"]

    def test_order_can_move_fallback_games_and_overrides_reward_ranking(self):
        settings = _settings("Alpha", priority_list_only=False,
                             reward_priority=["BADGE", "EMOTE", "DIRECT_ENTITLEMENT", "UNKNOWN"])
        campaigns = self.campaigns(settings)
        default = _names(StreamSelector().get_wanted_game_tree(settings, campaigns))
        assert default[0] == "Alpha"
        tree = StreamSelector().get_wanted_game_tree(settings, campaigns, ordered("Gamma"))
        assert _names(tree) == ["Gamma"] + [name for name in default if name != "Gamma"]


class TestMiningSelection:
    def setup(self, order):
        settings = _settings("Alpha", "Beta", priority_list_only=True)
        alpha = _campaign("a", "Alpha", game_id=1, settings=settings, acl=(100,))
        beta = _campaign("b", "Beta", game_id=2, settings=settings, acl=(200,))
        twitch = alpha._twitch
        twitch.settings = settings
        twitch.inventory = [alpha, beta]
        twitch.game_order = order
        twitch.wanted_games = StreamSelector().get_wanted_games(settings, twitch.inventory, order)
        service = ChannelService(twitch)
        channels = (_channel(twitch, 100, {"id": "1", "name": "Alpha"}),
                    _channel(twitch, 200, {"id": "2", "name": "Beta"}))
        return twitch, service, channels

    def test_inactive_order_keeps_existing_priority_keys(self):
        _twitch, service, (alpha, beta) = self.setup(TemporaryGameOrder())
        assert service.get_mining_priority(alpha) == (0, 0)
        assert service.get_mining_priority(beta) == (0, 1)

    def test_active_order_ranks_channels_first(self):
        twitch, service, (alpha, beta) = self.setup(ordered("Beta"))
        assert sorted([alpha, beta], key=service.get_mining_priority) == [beta, alpha]
        assert service.get_mining_priority(beta)[0] == 0
        assert service.get_mining_priority(_channel(twitch, 300, None, online=False))[0] == 1

    def test_switching_follows_an_active_order(self):
        from src.services.watch_service import WatchService

        twitch, service, (alpha, beta) = self.setup(TemporaryGameOrder())
        twitch._channel_service = service
        watch = WatchService(twitch)
        twitch.watching_channel.get_with_default.return_value = beta
        assert watch.should_switch(alpha) is True
        twitch.game_order = ordered("Beta")
        twitch.wanted_games = StreamSelector().get_wanted_games(twitch.settings, twitch.inventory, twitch.game_order)
        assert watch.should_switch(alpha) is False
        twitch.watching_channel.get_with_default.return_value = alpha
        assert watch.should_switch(beta) is True

    @pytest.mark.parametrize("special_id", [509663, 509672])
    @pytest.mark.parametrize(
        ("alpha_acl", "drops_enabled"),
        [((100,), True), ((200,), False)],
        ids=["outside-category-acl", "category-drops-disabled"],
    )
    def test_channel_only_inherits_order_from_watchable_campaigns(
        self, special_id, alpha_acl, drops_enabled,
    ):
        from src.services.watch_service import WatchService

        settings = _settings("Gamma", "Alpha", "Special", priority_list_only=True)
        alpha = _campaign("alpha", "Alpha", game_id=1, settings=settings, acl=alpha_acl)
        special = _campaign("special", "Special", game_id=special_id, settings=settings, acl=(200,))
        gamma = _campaign("gamma", "Gamma", game_id=3, settings=settings, acl=(300,))
        twitch = alpha._twitch
        twitch.settings = settings
        twitch.inventory = [alpha, special, gamma]
        twitch.game_order = ordered("Alpha", "Gamma", "Special")
        twitch.wanted_games = StreamSelector().get_wanted_games(
            settings, twitch.inventory, twitch.game_order,
        )
        service = ChannelService(twitch)
        twitch._channel_service = service
        watch = WatchService(twitch)
        special_channel = _channel(
            twitch, 200, {"id": "1", "name": "Alpha"}, drops_enabled=drops_enabled,
        )
        gamma_channel = _channel(twitch, 300, {"id": "3", "name": "Gamma"})

        # The streamed category cannot progress here: it either excludes this
        # channel or requires its missing drops flag. The special campaign can.
        assert alpha.can_earn(special_channel) is (200 in alpha_acl)
        assert special.can_earn(special_channel)
        assert watch.can_watch(special_channel)
        assert watch.can_watch(gamma_channel)
        assert [game.name for game in twitch.wanted_games] == ["Alpha", "Gamma", "Special"]
        assert sorted([special_channel, gamma_channel], key=service.get_mining_priority) == [
            gamma_channel, special_channel,
        ]
        twitch.watching_channel.get_with_default.return_value = special_channel
        assert watch.should_switch(gamma_channel)
        twitch.watching_channel.get_with_default.return_value = gamma_channel
        assert not watch.should_switch(special_channel)

        # A participating, drops-enabled Alpha channel still takes first place.
        alpha_channel = _channel(twitch, alpha_acl[0], {"id": "1", "name": "Alpha"})
        assert watch.can_watch(alpha_channel)
        assert watch.should_switch(alpha_channel)

    def test_active_campaign_follows_the_order_on_a_shared_channel(self):
        settings = _settings("Alpha", "Beta", priority_list_only=True)
        alpha = _campaign("a", "Alpha", game_id=1, settings=settings, acl=(100,))
        beta = _campaign("b", "Beta", game_id=509663, settings=settings, acl=(100,))
        twitch = alpha._twitch
        twitch.settings = settings
        twitch.inventory = [alpha, beta]
        twitch.wanted_games = [alpha.game, beta.game]
        channel = _channel(twitch, 100, {"id": "1", "name": "Alpha"})
        twitch.watching_channel.get_with_default.return_value = channel
        service = InventoryService(twitch)
        twitch.game_order = TemporaryGameOrder()
        default = service.get_active_campaign(channel)
        other = beta if default is alpha else alpha
        twitch.game_order = ordered(other.game.name)
        assert service.get_active_campaign(channel) is other


class TestLifecycle:
    def test_set_and_clear_recompute_and_broadcast(self):
        twitch = Twitch.__new__(Twitch)
        twitch.game_order = TemporaryGameOrder()
        twitch.gui = MagicMock()
        twitch.request_games_update = MagicMock(return_value=True)
        twitch.set_game_order(["Beta", "beta", "Alpha"])
        assert twitch.game_order.names == ["Beta", "Alpha"]
        twitch.request_games_update.assert_called_once()
        twitch.gui.broadcast_wanted_items.assert_called_once()
        twitch.gui.broadcast_game_order.assert_called_with({"active": True, "games": ["Beta", "Alpha"]})
        twitch.clear_game_order()
        assert not twitch.game_order.active
        twitch.request_games_update.reset_mock()
        twitch.clear_game_order()
        twitch.request_games_update.assert_not_called()

    @pytest.mark.asyncio
    async def test_saved_mining_list_change_clears_temporary_order_only_after_saving(self):
        cleared = MagicMock()
        settings = SimpleNamespace(games_to_watch=["Alpha", "Beta"], save=MagicMock())
        manager = SettingsManager(MagicMock(emit=AsyncMock()), settings, MagicMock(),
                                  on_games_change=cleared)
        manager.update_settings({"games_to_watch": ["Alpha", "Beta"]})
        cleared.assert_not_called()
        settings.save.side_effect = OSError
        with pytest.raises(OSError):
            manager.update_settings({"games_to_watch": ["Beta", "Alpha"]})
        cleared.assert_not_called()
        settings.save.side_effect = None
        manager.update_settings({"games_to_watch": ["Beta", "Alpha"]})
        cleared.assert_called_once()


@pytest.fixture
def client(tmp_path, monkeypatch):
    auth = web.web_auth
    for name, value in {"path": tmp_path / "web_auth.json", "password_hash": "", "sessions": {},
                        "lock": asyncio.Lock(), "attempts": deque(), "origin": DashboardOrigin()}.items():
        monkeypatch.setattr(auth, name, value)
    twitch = SimpleNamespace(game_order=TemporaryGameOrder())
    twitch.set_game_order = twitch.game_order.set
    monkeypatch.setattr(web, "twitch_client", twitch)
    with TestClient(web.socket_app, headers={"X-TDM-Request": "1"}) as browser:
        yield browser, twitch


class TestAPI:
    def test_sets_and_resets_the_order(self, client):
        browser, twitch = client
        reply = browser.post("/api/wanted-order", json={"games": ["Beta", " beta ", "Alpha"]})
        assert reply.json() == {"success": True, "active": True, "games": ["Beta", "Alpha"]}
        reply = browser.post("/api/wanted-order", json={"games": []})
        assert reply.json() == {"success": True, "active": False, "games": []}

    @pytest.mark.parametrize("body", [{}, {"games": "Beta"}, {"games": [1]}, {"games": ["x" * 201]}])
    def test_rejects_invalid_input_without_echo(self, client, body):
        browser, twitch = client
        twitch.game_order.set(["Kept"])
        reply = browser.post("/api/wanted-order", json=body)
        assert reply.status_code == 422
        assert reply.json() == {"detail": "invalid_request"}
        assert twitch.game_order.names == ["Kept"]

    def test_unavailable_without_client(self, client, monkeypatch):
        browser, _twitch = client
        monkeypatch.setattr(web, "twitch_client", None)
        assert browser.post("/api/wanted-order", json={"games": []}).status_code == 503
