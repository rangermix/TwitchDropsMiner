"""Channel points options of idle watching: defaults, validation, load salvage, save and the API."""

import asyncio
import json
import logging
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.config.settings import PointsSettings, Settings, default_settings
from src.web.managers.settings import SettingsManager
from tests.test_wanted_queue_order import client  # noqa: F401 (pytest fixture)


DEFAULTS = {
    "idle_priority": "list",
    "idle_streak_first": True,
    "streak_recovery": True,
    "follow_raids": False,
    "predictions_enabled": False,
    "prediction_strategy": "smart",
    "prediction_percent": 5,
    "prediction_max_points": 1000,
    "claim_moments": True,
    "join_chat": False,
}

VALID = [
    ("idle_priority", v) for v in ("list", "points_low", "points_high", "subscribed")
] + [
    ("prediction_strategy", v) for v in ("smart", "most_voted", "high_odds", "favourite")
] + [
    ("prediction_percent", 1), ("prediction_percent", 100),
    ("prediction_max_points", 10), ("prediction_max_points", 250000),
] + [(key, value) for key in PointsSettings.FLAGS for value in (True, False)]

INVALID = [
    ("idle_priority", "LIST"), ("idle_priority", "points"), ("idle_priority", ""), ("idle_priority", 1),
    ("idle_priority", ["list"]),
    ("prediction_strategy", "favorite"), ("prediction_strategy", "Smart"), ("prediction_strategy", " smart"),
    ("prediction_percent", 0), ("prediction_percent", 101), ("prediction_percent", -5),
    ("prediction_percent", True), ("prediction_percent", 5.0), ("prediction_percent", "5"),
    ("prediction_max_points", 9), ("prediction_max_points", 250001), ("prediction_max_points", False),
    ("prediction_max_points", 1000.0),
] + [(key, value) for key in PointsSettings.FLAGS for value in (1, 0, "true", None)]


def test_keys_and_defaults(tmp_path, monkeypatch):
    assert set(PointsSettings.KEYS) == set(DEFAULTS)
    monkeypatch.setattr("src.config.settings.SETTINGS_PATH", tmp_path / "settings.json")
    settings = Settings()
    assert {key: getattr(settings, key) for key in DEFAULTS} == DEFAULTS
    for key, value in DEFAULTS.items():
        assert PointsSettings.normalize(key, value) == value


@pytest.mark.parametrize("key,value", VALID, ids=repr)
def test_normalize_accepts(key, value):
    assert PointsSettings.normalize(key, value) == value


@pytest.mark.parametrize("key,value", INVALID, ids=repr)
def test_normalize_rejects(key, value):
    with pytest.raises(ValueError, match=f"invalid_{key}"):
        PointsSettings.normalize(key, value)


def test_unknown_key_is_rejected():
    with pytest.raises(ValueError):
        PointsSettings.normalize("claim_points", True)


def _load(tmp_path, monkeypatch, caplog, raw):
    path = tmp_path / "settings.json"
    path.write_text(json.dumps(raw), encoding="utf-8")
    monkeypatch.setattr("src.config.settings.SETTINGS_PATH", path)
    with caplog.at_level(logging.WARNING, logger="TwitchDrops"):
        return Settings()


def test_invalid_hand_edited_values_load_as_defaults_with_one_warning_each(tmp_path, monkeypatch, caplog):
    raw = {"idle_priority": "loudest", "prediction_percent": 0, "prediction_max_points": True,
           "prediction_strategy": "favorite", "join_chat": "yes", "claim_moments": 0}
    settings = _load(tmp_path, monkeypatch, caplog, raw)
    assert {key: getattr(settings, key) for key in DEFAULTS} == DEFAULTS
    assert sorted(r.getMessage() for r in caplog.records if r.levelno == logging.WARNING) == [
        "Ignoring invalid claim_moments in settings; using the default (on)",
        "Ignoring invalid idle_priority in settings; using the default (list)",
        "Ignoring invalid join_chat in settings; using the default (off)",
        "Ignoring invalid prediction_max_points in settings; using the default (1000)",
        "Ignoring invalid prediction_percent in settings; using the default (5)",
        "Ignoring invalid prediction_strategy in settings; using the default (smart)",
    ]


def test_valid_values_load_without_warning(tmp_path, monkeypatch, caplog):
    raw = {"idle_priority": "subscribed", "prediction_percent": 100, "prediction_max_points": 10,
           "prediction_strategy": "high_odds", "join_chat": True, "idle_streak_first": False}
    settings = _load(tmp_path, monkeypatch, caplog, raw)
    assert {key: getattr(settings, key) for key in raw} == raw
    assert not caplog.records


def test_options_survive_save_and_reload_and_an_invalid_one_is_not_saved(tmp_path, monkeypatch):
    path = tmp_path / "settings.json"
    monkeypatch.setattr("src.config.settings.SETTINGS_PATH", path)
    settings = Settings()
    changed = {"idle_priority": "points_low", "idle_streak_first": False, "streak_recovery": False,
               "follow_raids": True, "predictions_enabled": True, "prediction_strategy": "favourite",
               "prediction_percent": 100, "prediction_max_points": 250000, "claim_moments": False,
               "join_chat": True}
    for key, value in changed.items():
        setattr(settings, key, value)
    settings.save()
    reloaded = Settings()
    assert {key: getattr(reloaded, key) for key in changed} == changed
    saved = path.read_text(encoding="utf-8")
    reloaded.prediction_percent = 101
    with pytest.raises(ValueError, match="invalid_prediction_percent"):
        reloaded.save()
    assert path.read_text(encoding="utf-8") == saved


def _manager():
    stored = SimpleNamespace(**{key: default_settings[key] for key in DEFAULTS}, save=MagicMock())
    manager = SettingsManager(SimpleNamespace(emit=AsyncMock()), stored, MagicMock(), MagicMock())
    manager._on_idle_channels_change = MagicMock()
    return manager, stored


def _update(manager, data):
    async def run():
        return manager.update_settings(data)

    return asyncio.run(run())


# None means "not sent" in an update
@pytest.mark.parametrize("key,value", [c for c in INVALID if c[1] is not None], ids=repr)
def test_manager_rejects_before_any_field_changes(key, value):
    manager, stored = _manager()
    with pytest.raises(ValueError, match=f"invalid_{key}"):
        # the valid option sent with it must not be applied either
        _update(manager, {"idle_priority": "points_high", key: value})
    assert {k: getattr(stored, k) for k in DEFAULTS} == DEFAULTS
    stored.save.assert_not_called()
    manager._on_idle_channels_change.assert_not_called()


def test_manager_saves_and_reruns_the_idle_step_without_a_games_update():
    manager, stored = _manager()
    response = _update(manager, {"idle_priority": "points_high", "prediction_percent": 100})
    assert stored.idle_priority == "points_high"
    assert stored.prediction_percent == 100
    assert response["idle_priority"] == "points_high"
    stored.save.assert_called_once()
    manager._on_change.assert_not_called()
    assert manager._on_idle_channels_change.call_count == 2
    manager._on_idle_channels_change.reset_mock()
    _update(manager, {"idle_priority": "points_high"})  # unchanged
    manager._on_idle_channels_change.assert_not_called()


def test_manager_restores_every_option_when_the_save_fails():
    manager, stored = _manager()
    # the rollback snapshot covers keys in default_settings, which the stub carries as attributes
    stored.save.side_effect = OSError
    with pytest.raises(OSError):
        _update(manager, {"idle_priority": "subscribed", "join_chat": True, "prediction_max_points": 10})
    assert {k: getattr(stored, k) for k in DEFAULTS} == DEFAULTS
    manager._on_idle_channels_change.assert_not_called()


class TestAPI:
    @pytest.mark.parametrize("body", [
        {"prediction_percent": True}, {"prediction_percent": "5"}, {"prediction_percent": 0},
        {"prediction_percent": 101}, {"prediction_max_points": 9}, {"prediction_max_points": 5.5},
        {"idle_priority": "loudest"}, {"prediction_strategy": "favorite"}, {"join_chat": 1},
        {"follow_raids": "true"},
    ])
    def test_rejects_invalid_options(self, client, body):  # noqa: F811
        browser, _twitch = client
        reply = browser.post("/api/settings", json=body)
        assert reply.status_code == 422
        assert reply.json() == {"detail": "invalid_request"}
