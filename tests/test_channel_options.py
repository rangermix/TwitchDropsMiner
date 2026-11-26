"""Per-channel options of idle watching: validation, load, manager, API, may_act, pick and the streak share."""

import asyncio
import json
import logging
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.config.settings import ChannelOptionSettings, Settings, default_settings
from src.services.idle_points import WatchPriority
from src.services.idle_watch import Visit
from src.web.managers.settings import SettingsManager
from tests.test_idle_points import setup as points_setup
from tests.test_idle_streak_share import setup as share_setup
from tests.test_idle_streak_share import shares
from tests.test_points_runtime import priority_service
from tests.test_wanted_queue_order import client  # noqa: F401 (pytest fixture)


VALID = [{"a": {key: value}} for key in ChannelOptionSettings.FLAGS for value in (True, False)] + [
    {"a": {"points_limit": 1}}, {"a": {"points_limit": 100_000_000}},
    {"a": {"favorite": True, "points_limit": 500, "streaks": False}}, {"a_b_1": {"chat": True}}, {},
]
INVALID = [
    {"a": {"unknown": True}}, {"a": {"bonus": 1}}, {"a": {"bonus": "true"}}, {"a": {"bonus": None}},
    {"a": {"favorite": 0}}, {"a": {"streak_only": "yes"}},
    {"a": {"points_limit": 0}}, {"a": {"points_limit": 100_000_001}}, {"a": {"points_limit": True}},
    {"a": {"points_limit": 5.0}}, {"a": {"points_limit": "5"}}, {"a": {"points_limit": None}},
    {"A": {"bonus": True}}, {"a b": {"bonus": True}}, {"": {"bonus": True}}, {"a" * 26: {"bonus": True}},
    {"a": []}, {"a": "bonus"}, {"a": None}, [], "a", 1,
]


@pytest.mark.parametrize("value", VALID, ids=repr)
def test_normalize_accepts(value):
    assert ChannelOptionSettings.normalize(value) == value


@pytest.mark.parametrize("value", [*INVALID, None], ids=repr)
def test_normalize_rejects(value):
    with pytest.raises(ValueError, match="invalid_idle_channel_options"):
        ChannelOptionSettings.normalize(value)


def test_an_empty_per_channel_object_is_left_out():
    assert ChannelOptionSettings.normalize({"a": {}, "b": {"chat": False}}) == {"b": {"chat": False}}


def _load(tmp_path, monkeypatch, caplog, raw):
    path = tmp_path / "settings.json"
    path.write_text(json.dumps(raw), encoding="utf-8")
    monkeypatch.setattr("src.config.settings.SETTINGS_PATH", path)
    with caplog.at_level(logging.WARNING, logger="TwitchDrops"):
        return Settings()


def test_default_is_empty(tmp_path, monkeypatch):
    monkeypatch.setattr("src.config.settings.SETTINGS_PATH", tmp_path / "settings.json")
    assert default_settings["idle_channel_options"] == {}
    assert Settings().idle_channel_options == {}


def test_load_salvages_valid_entries_with_one_count_only_warning(tmp_path, monkeypatch, caplog):
    raw = {"idle_channels": ["a", "b"], "idle_channel_options": {
        "a": {"bonus": False, "points_limit": 0, "nope": True, "favorite": True}, "b": "x", "Bad Login": {"chat": True},
    }}
    settings = _load(tmp_path, monkeypatch, caplog, raw)
    assert settings.idle_channel_options == {"a": {"bonus": False, "favorite": True}}
    [record] = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert record.getMessage() == "Ignoring 4 invalid idle_channel_options entries in settings"


def test_load_with_a_wrong_type_uses_the_default(tmp_path, monkeypatch, caplog):
    assert _load(tmp_path, monkeypatch, caplog, {"idle_channel_options": [1]}).idle_channel_options == {}
    assert len(caplog.records) == 1


def test_load_drops_options_of_unlisted_logins_without_a_warning(tmp_path, monkeypatch, caplog):
    raw = {"idle_channels": ["a"], "idle_channel_options": {"a": {"chat": True}, "gone": {"chat": True}}}
    settings = _load(tmp_path, monkeypatch, caplog, raw)
    assert settings.idle_channel_options == {"a": {"chat": True}}
    assert not caplog.records


def test_save_reload_and_pruning(tmp_path, monkeypatch):
    monkeypatch.setattr("src.config.settings.SETTINGS_PATH", tmp_path / "settings.json")
    settings = Settings()
    settings.idle_channels = ["a", "b"]
    settings.idle_channel_options = {"a": {"favorite": True, "points_limit": 9}, "b": {}, "c": {"chat": True}}
    settings.save()
    assert settings.idle_channel_options == {"a": {"favorite": True, "points_limit": 9}}
    assert Settings().idle_channel_options == {"a": {"favorite": True, "points_limit": 9}}
    settings.idle_channel_options = {"a": {"points_limit": 0}}
    with pytest.raises(ValueError, match="invalid_idle_channel_options"):
        settings.save()


def _manager(channels=("a", "b"), options=None):
    stored = SimpleNamespace(idle_channels=list(channels), idle_channel_options=options or {}, save=MagicMock())
    manager = SettingsManager(SimpleNamespace(emit=AsyncMock()), stored, MagicMock(), MagicMock())
    manager._on_idle_channels_change = MagicMock()
    return manager, stored


def _update(manager, data):
    async def run():
        return manager.update_settings(data)

    return asyncio.run(run())


def test_manager_saves_options_and_reruns_the_idle_step():
    manager, stored = _manager()
    response = _update(manager, {"idle_channel_options": {"a": {"favorite": True}, "b": {}}})
    assert stored.idle_channel_options == {"a": {"favorite": True}}
    assert response["idle_channel_options"] == {"a": {"favorite": True}}
    stored.save.assert_called_once()
    manager._on_idle_channels_change.assert_called_once()


def test_manager_prunes_options_of_a_login_that_is_not_listed():
    manager, stored = _manager()
    _update(manager, {"idle_channel_options": {"a": {"chat": True}, "zzz": {"chat": True}}})
    assert stored.idle_channel_options == {"a": {"chat": True}}


def test_manager_removing_a_channel_removes_its_options():
    manager, stored = _manager(options={"a": {"chat": True}, "b": {"favorite": True}})
    _update(manager, {"idle_channels": ["b"]})
    assert stored.idle_channel_options == {"b": {"favorite": True}}
    # the new list and the options can come in one request
    data = {"idle_channels": ["b", "c"], "idle_channel_options": {"c": {"streak_only": True}, "a": {"chat": True}}}
    _update(manager, data)
    assert stored.idle_channel_options == {"c": {"streak_only": True}}


@pytest.mark.parametrize("value", INVALID, ids=repr)
def test_manager_rejects_before_any_field_changes(value):
    manager, stored = _manager(options={"a": {"chat": True}})
    with pytest.raises(ValueError):
        _update(manager, {"idle_channels": ["a"], "idle_channel_options": value})
    assert stored.idle_channel_options == {"a": {"chat": True}}
    stored.save.assert_not_called()


def test_manager_restores_the_options_when_the_save_fails():
    manager, stored = _manager(options={"a": {"chat": True}})
    stored.save.side_effect = OSError
    with pytest.raises(OSError):
        _update(manager, {"idle_channel_options": {"b": {"favorite": True}}})
    assert stored.idle_channel_options == {"a": {"chat": True}}
    manager._on_idle_channels_change.assert_not_called()


class TestAPI:
    @pytest.mark.parametrize("value", INVALID, ids=repr)
    def test_rejects_invalid_options(self, client, value):  # noqa: F811
        browser, _twitch = client
        reply = browser.post("/api/settings", json={"idle_channel_options": value})
        assert reply.status_code == 422
        assert reply.json() == {"detail": "invalid_request"}


# --- may_act ---

FEATURES = list(ChannelOptionSettings.OVERRIDES.items())


@pytest.mark.parametrize(("key", "setting"), FEATURES)
@pytest.mark.parametrize("global_value", [True, False])
@pytest.mark.parametrize("override", [True, False, None])
def test_may_act_resolves_the_override_against_the_global_setting(key, setting, global_value, override):
    svc, tw, watched = points_setup()
    setattr(tw.settings, setting, global_value)
    tw.settings.idle_channel_options = {} if override is None else {"a": {key: override}}
    assert svc.may_act(watched, setting) is (global_value if override is None else override)
    # an option of another channel does not leak
    tw.settings.idle_channel_options = {"b": {key: not global_value}}
    assert svc.may_act(watched, setting) is global_value


@pytest.mark.parametrize(("key", "setting"), FEATURES)
def test_an_override_on_keeps_the_other_may_act_checks(key, setting):
    svc, tw, watched = points_setup()
    setattr(tw.settings, setting, False)
    tw.settings.idle_channel_options = {"a": {key: True}}
    assert svc.may_act(watched, setting) is True
    svc.points_paused = True
    assert svc.may_act(watched, setting) is False
    svc.points_paused = False
    tw.is_mining_time.return_value = False
    assert svc.may_act(watched, setting) is False
    tw.is_mining_time.return_value = True
    tw.can_watch.return_value = True
    assert svc.may_act(watched, setting) is False
    tw.can_watch.return_value = False
    watched.online = False
    assert svc.may_act(watched, setting) is False
    watched.online = True
    watched.banned = True
    assert svc.may_act(watched, setting) is False
    watched.banned = False
    tw.settings.idle_channels = ["b"]
    assert svc.may_act(watched, setting) is False
    tw.settings.idle_channels = ["a", "b"]
    svc._task = MagicMock(done=lambda: True)
    assert svc.may_act(watched, setting) is False


def test_a_global_setting_without_an_override_key_is_used_as_is():
    svc, tw, watched = points_setup()
    tw.settings.share_watch_streaks = True
    tw.settings.idle_channel_options = {"a": {"bonus": False}}
    assert svc.may_act(watched, "share_watch_streaks") is True


# --- watch priority and pick ---


def ch(cid, login):
    return SimpleNamespace(id=cid, login=login)


def test_favorites_go_first_inside_every_mode():
    live = [ch(1, "a"), ch(2, "b"), ch(3, "c"), ch(4, "d")]
    balances = {1: 50, 2: 10, 3: 30, 4: 20}
    factors = {3: (0.5,), 4: (0.2,)}

    def ids(mode, favorites):
        return [c.id for c in WatchPriority.order(live, mode, balances, factors, favorites)]

    assert ids("list", ["c", "d"]) == [3, 4, 1, 2]
    assert ids("points_low", ["a", "c"]) == [3, 1, 2, 4]
    assert ids("points_high", ["b", "d"]) == [4, 2, 1, 3]
    assert ids("subscribed", ["a"]) == [1, 3, 4, 2]
    assert ids("list", []) == [1, 2, 3, 4]


def test_the_favorite_gets_the_first_visit_and_the_after_visit_pick():
    svc, live = priority_service()
    svc._twitch.settings.idle_channel_options = {"c3": {"favorite": True}}
    assert svc.pick(live, 0).id == 3
    finished(svc, live)
    assert svc.pick(live, 1).id == 3


def finished(svc, live):
    svc.visited = {c.broadcast_id: c.id for c in live}
    svc.visit = None


def test_streak_only_gets_its_visit_but_is_not_the_after_visit_pick():
    svc, live = priority_service()
    svc._twitch.settings.idle_channel_options = {"c1": {"streak_only": True}}
    assert svc.pick(live, 0).id == 1
    assert svc.visit.channel_id == 1
    finished(svc, live)
    assert svc.pick(live, 502).id == 2
    svc._twitch.settings.idle_channel_options = {"c1": {"streak_only": True}, "c2": {"streak_only": True}}
    assert svc.pick(live, 503).id == 3


@pytest.mark.parametrize(("balance", "stays"), [(99, True), (100, False), (101, False), (None, True)])
def test_points_limit_at_below_above_and_unknown(balance, stays):
    svc, live = priority_service()
    svc._twitch.settings.idle_channel_options = {"c1": {"points_limit": 100}}
    finished(svc, live)
    if balance is not None:
        svc.balances = {1: balance}
    assert (svc.pick(live, 0).id == 1) is stays


def test_points_limit_still_runs_the_streak_visit():
    svc, live = priority_service()
    svc._twitch.settings.idle_channel_options = {"c1": {"points_limit": 100}}
    svc.balances = {1: 5000}
    assert svc.pick(live, 0).id == 1
    assert svc.visit.channel_id == 1


def test_a_watched_channel_over_its_limit_is_left():
    svc, live = priority_service()
    svc._twitch.settings.idle_channel_options = {"c1": {"points_limit": 100}}
    finished(svc, live)
    svc.balances = {1: 50}
    svc.watched = live[0]
    assert svc.pick(live, 0).id == 1
    svc.balances = {1: 150}
    assert svc.pick(live, 1).id == 2


def test_streaks_off_per_channel_means_no_visit_and_no_recovery():
    svc, live = priority_service()
    svc._twitch.settings.idle_channel_options = {"c1": {"streaks": False}}
    assert svc.pick(live, 0).id == 2  # c1 gets no first visit
    svc.visit = None
    svc.visited = {20: 2, 30: 3}
    assert svc.pick(live, 1).id == 1  # nothing to visit: the after-visit pick, list order
    assert svc.visit is None
    svc._recovery = {10: (1, 0, 0.0), 20: (2, 0, 0.0)}
    assert svc.pick(live, 5000).id == 2
    assert svc.visit.recovery
    assert svc.visit.channel_id == 2


def test_a_visit_in_progress_is_dropped_when_streaks_go_off_for_it():
    svc, live = priority_service()
    svc.visit = Visit(1, 10, 0)
    svc._twitch.settings.idle_channel_options = {"c1": {"streaks": False}}
    svc.pick(live, 1)
    assert svc.visit.channel_id == 2
    assert 10 not in svc.visited


def test_streaks_on_per_channel_overrides_a_global_off():
    svc, live = priority_service(idle_streak_first=False)
    svc._twitch.settings.idle_channel_options = {"c2": {"streaks": True}}
    assert svc.pick(live, 0).id == 2
    assert svc.visit.channel_id == 2


def test_everything_filtered_out_watches_nothing():
    svc, live = priority_service()
    svc._twitch.settings.idle_channel_options = {
        "c1": {"streak_only": True}, "c2": {"points_limit": 10}, "c3": {"streak_only": True},
    }
    svc.balances = {2: 10}
    finished(svc, live)
    assert svc.pick(live, 0) is None


# --- streak share ---


@pytest.mark.asyncio
async def test_no_streak_share_for_a_channel_with_streaks_off():
    svc, tw = share_setup()
    tw.settings.idle_channel_options = {"a": {"streaks": False}}
    await svc.share_streaks()
    assert shares(tw) == []
    assert tw.gql_request.call_count == 0  # not even read


@pytest.mark.asyncio
async def test_streak_share_is_sent_when_the_channel_turns_streaks_on_over_a_global_off():
    svc, tw = share_setup()
    tw.settings.idle_streak_first = False
    tw.settings.idle_channel_options = {"a": {"streaks": True}}
    await svc.share_streaks()
    assert len(shares(tw)) == 1


@pytest.mark.asyncio
async def test_streak_share_without_an_override_follows_share_watch_streaks_alone():
    # as before per-channel options: the global idle_streak_first off does not block a share
    svc, tw = share_setup()
    tw.settings.idle_streak_first = False
    await svc.share_streaks()
    assert len(shares(tw)) == 1


@pytest.mark.asyncio
async def test_streaks_override_off_blocks_the_share_also_with_the_global_option_on():
    svc, tw = share_setup()
    tw.settings.idle_streak_first = True
    tw.settings.idle_channel_options = {"a": {"streaks": False, "raids": True}}
    await svc.share_streaks()
    assert shares(tw) == []


def test_streak_share_is_rechecked_right_before_sending():
    svc, tw = share_setup()
    channel = tw.channels[123]
    assert svc._may_share(channel) is True
    tw.settings.idle_channel_options = {"a": {"streaks": False}}
    assert svc._may_share(channel) is False
