import asyncio
import json
import logging
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.config.settings import IdleChannelSettings, Settings
from src.web.managers.settings import SettingsManager
from tests.test_diagnostics import diagnostic_miner


def test_normalize_lowercases_strips_and_dedupes():
    assert IdleChannelSettings.normalize([" Foo ", "foo", "bar_1"]) == ["foo", "bar_1"]


@pytest.mark.parametrize(
    "value",
    ["foo", [1], ["bad-name"], [""], ["a" * 26], [f"x{i}" for i in range(21)]],
)
def test_normalize_rejects_bad_values(value):
    with pytest.raises(ValueError, match="invalid_idle_channels"):
        IdleChannelSettings.normalize(value)


def test_defaults(tmp_path, monkeypatch):
    monkeypatch.setattr("src.config.settings.SETTINGS_PATH", tmp_path / "settings.json")
    settings = Settings()
    assert settings.idle_channels == []
    assert settings.claim_channel_points is True
    assert settings.share_watch_streaks is True


def test_invalid_idle_channels_in_file_do_not_block_load(tmp_path, monkeypatch, caplog):
    path = tmp_path / "settings.json"
    path.write_text('{"idle_channels": ["My-Channel"]}', encoding="utf-8")
    monkeypatch.setattr("src.config.settings.SETTINGS_PATH", path)
    with caplog.at_level(logging.WARNING, logger="TwitchDrops"):
        settings = Settings()
    assert settings.idle_channels == []
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1
    assert "My-Channel" not in caplog.text


def test_hand_edited_list_keeps_valid_entries(tmp_path, monkeypatch, caplog):
    # F4: one bad entry must not drop the whole list
    path = tmp_path / "settings.json"
    entries = ["Good", "bad-name", "good", 5, "Kelvin"] + [f"c{i}" for i in range(25)]
    path.write_text(json.dumps({"idle_channels": entries}), encoding="utf-8")
    monkeypatch.setattr("src.config.settings.SETTINGS_PATH", path)
    with caplog.at_level(logging.WARNING, logger="TwitchDrops"):
        settings = Settings()
    assert settings.idle_channels == ["good"] + [f"c{i}" for i in range(19)]
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1
    assert "good" not in caplog.text.lower() and "c1" not in caplog.text


def test_valid_list_loads_without_warning(tmp_path, monkeypatch, caplog):
    path = tmp_path / "settings.json"
    path.write_text('{"idle_channels": ["Foo", "bar"]}', encoding="utf-8")
    monkeypatch.setattr("src.config.settings.SETTINGS_PATH", path)
    with caplog.at_level(logging.WARNING, logger="TwitchDrops"):
        settings = Settings()
    assert settings.idle_channels == ["foo", "bar"]
    assert not caplog.records


@pytest.mark.parametrize("raw,warned", [('"false"', True), ("0", True), ("null", True), ("false", False)])
def test_non_bool_claim_setting_loads_as_default_with_one_warning(tmp_path, monkeypatch, caplog, raw, warned):
    # RT11: the value already falls back to True (merge_json); the warning was missing
    path = tmp_path / "settings.json"
    path.write_text(f'{{"claim_channel_points": {raw}}}', encoding="utf-8")
    monkeypatch.setattr("src.config.settings.SETTINGS_PATH", path)
    with caplog.at_level(logging.WARNING, logger="TwitchDrops"):
        settings = Settings()
    assert settings.claim_channel_points is warned  # every non-bool falls back to the default, True
    assert len([r for r in caplog.records if r.levelno == logging.WARNING]) == (1 if warned else 0)


@pytest.mark.parametrize("raw,warned", [('"false"', True), ("0", True), ("null", True), ("false", False)])
def test_non_bool_share_setting_loads_as_default_with_one_warning(tmp_path, monkeypatch, caplog, raw, warned):
    path = tmp_path / "settings.json"
    path.write_text(f'{{"share_watch_streaks": {raw}}}', encoding="utf-8")
    monkeypatch.setattr("src.config.settings.SETTINGS_PATH", path)
    with caplog.at_level(logging.WARNING, logger="TwitchDrops"):
        settings = Settings()
    assert settings.share_watch_streaks is warned
    assert len([r for r in caplog.records if r.levelno == logging.WARNING]) == (1 if warned else 0)


def test_share_watch_streaks_requires_strict_bool_and_saves():
    manager, stored, on_change, _ = _manager()
    with pytest.raises(ValueError, match="invalid_share_watch_streaks"):
        _update(manager, {"share_watch_streaks": "false"})
    assert stored.share_watch_streaks is True
    stored.save.assert_not_called()
    response = _update(manager, {"share_watch_streaks": False})
    assert stored.share_watch_streaks is False
    assert response["share_watch_streaks"] is False
    on_change.assert_not_called()


@pytest.mark.parametrize("login", ["Kelvin", "café", " foo", "foo​"])
def test_normalize_rejects_non_ascii_before_lowering(login):
    # F6: the Kelvin sign lowercases to an ASCII "k"
    with pytest.raises(ValueError, match="invalid_idle_channels"):
        IdleChannelSettings.normalize([login])


def _manager():
    stored = SimpleNamespace(idle_channels=[], claim_channel_points=True, share_watch_streaks=True, save=MagicMock())
    on_change = MagicMock()
    console = MagicMock()
    manager = SettingsManager(SimpleNamespace(emit=AsyncMock()), stored, console, on_change)
    manager._on_idle_channels_change = MagicMock()
    return manager, stored, on_change, console


def _update(manager, data):
    async def run():
        return manager.update_settings(data)

    return asyncio.run(run())


def test_settings_update_saves_and_reruns_idle_step_without_games_update(caplog):
    # RT3: list edits must not restart channel cleanup and refetch while mining
    manager, stored, on_change, console = _manager()
    idle_change = manager._on_idle_channels_change
    with caplog.at_level(logging.DEBUG, logger="TwitchDrops"):
        response = _update(manager, {"idle_channels": ["Foo"], "claim_channel_points": False})
    assert stored.idle_channels == ["foo"]
    assert stored.claim_channel_points is False
    assert response["idle_channels"] == ["foo"]
    assert response["claim_channel_points"] is False
    on_change.assert_not_called()
    idle_change.assert_called_once()
    assert "foo" not in " ".join(str(call) for call in console.print.call_args_list)
    assert "foo" not in caplog.text.lower()

    idle_change.reset_mock()
    _update(manager, {"idle_channels": ["FOO"], "claim_channel_points": True})
    assert stored.claim_channel_points is True
    on_change.assert_not_called()
    idle_change.assert_not_called()


def test_gui_manager_wires_idle_list_changes_to_the_client():
    import inspect

    from src.web import gui_manager

    assert "on_idle_channels_change=lambda: self._twitch.idle_channels_changed()" in inspect.getsource(gui_manager)


def test_claim_channel_points_requires_strict_bool():
    manager, stored, on_change, _ = _manager()
    with pytest.raises(ValueError, match="invalid_claim_channel_points"):
        _update(manager, {"claim_channel_points": "false"})
    assert stored.claim_channel_points is True
    stored.save.assert_not_called()


def test_invalid_idle_channels_leave_settings_untouched():
    manager, stored, _, _ = _manager()
    with pytest.raises(ValueError, match="invalid_idle_channels"):
        _update(manager, {"idle_channels": ["bad-name"]})
    stored.save.assert_not_called()


def test_diagnostics_report_counts_only(tmp_path):
    miner = diagnostic_miner(tmp_path)
    miner.settings.idle_channels = ["secretname"]
    snapshot = miner.diagnostics.snapshot(miner)
    assert snapshot["settings"]["idle_channels_total"] == 1
    assert snapshot["settings"]["claim_channel_points"] is True
    assert snapshot["settings"]["share_watch_streaks"] is True
    assert "secretname" not in json.dumps(snapshot)
