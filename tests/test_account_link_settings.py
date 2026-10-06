"""Persistence, migration, API validation and refresh for account-link controls."""

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pydantic import ValidationError

from src.config.settings import InventoryFilterSettings, Settings
from src.web.app import SettingsUpdate
from src.web.managers.settings import SettingsManager


@pytest.mark.parametrize(
    ("filters", "mode"),
    [
        ({}, "all"),
        ({"show_only_not_linked": True}, "not_linked"),
        ({"show_only_not_linked": False}, "all"),
        ({"show_not_linked": True}, "all"),
        ({"link_status": "linked", "show_only_not_linked": True}, "linked"),
        ({"link_status": "invalid"}, "all"),
    ],
)
def test_persisted_filters_migrate_before_defaults_and_round_trip(tmp_path, filters, mode):
    path = tmp_path / "settings.json"
    path.write_text(json.dumps({"inventory_filters": filters}), encoding="utf-8")
    with patch("src.config.settings.SETTINGS_PATH", path):
        settings = Settings()
        assert settings.inventory_filters["link_status"] == mode
        assert not settings.allow_unlinked_campaigns
        settings.allow_unlinked_campaigns = True
        settings.save()
        restored = Settings()
    assert restored.allow_unlinked_campaigns is True
    assert restored.inventory_filters["link_status"] == mode
    saved = json.loads(path.read_text(encoding="utf-8"))
    assert "show_only_not_linked" not in saved["inventory_filters"]
    assert "show_not_linked" not in saved["inventory_filters"]


@pytest.mark.parametrize("value", ["true", "false", 1, [], {}])
def test_malformed_saved_override_stays_off(tmp_path, value):
    path = tmp_path / "settings.json"
    path.write_text(json.dumps({"allow_unlinked_campaigns": value}), encoding="utf-8")
    with patch("src.config.settings.SETTINGS_PATH", path):
        assert Settings().allow_unlinked_campaigns is False


@pytest.mark.parametrize("value", ["true", "false", 1, 0, [], {}])
def test_api_rejects_nonboolean_override(value):
    with pytest.raises(ValidationError):
        SettingsUpdate(allow_unlinked_campaigns=value)


@pytest.mark.parametrize("value", ["invalid", True, None, {}])
def test_api_rejects_invalid_link_filter(value):
    with pytest.raises(ValidationError):
        SettingsUpdate(inventory_filters={"link_status": value})


@pytest.mark.asyncio
async def test_override_refreshes_once_per_change_and_display_filters_do_not(tmp_path):
    with patch("src.config.settings.SETTINGS_PATH", tmp_path / "settings.json"):
        settings = Settings()
        callback = MagicMock()
        broadcaster = AsyncMock()
        manager = SettingsManager(broadcaster, settings, MagicMock(), on_change=callback)
        for enabled in (True, True, False):
            data = SettingsUpdate(allow_unlinked_campaigns=enabled).model_dump(exclude_unset=True)
            result = manager.update_settings(data)
            await asyncio.sleep(0)
            assert result["allow_unlinked_campaigns"] is enabled
            assert Settings().allow_unlinked_campaigns is enabled
        assert callback.call_count == 2
        callback.reset_mock()
        manager.update_settings({"inventory_filters": {"link_status": "linked"}})
        await asyncio.sleep(0)
        callback.assert_not_called()
        assert Settings().inventory_filters["link_status"] == "linked"
        assert broadcaster.emit.call_count == 4


def test_partial_filter_updates_preserve_link_mode_and_legacy_checkbox_updates():
    current = InventoryFilterSettings.normalize({"link_status": "linked", "show_active": True})
    updated = InventoryFilterSettings.normalize({"show_upcoming": True}, current)
    assert updated["link_status"] == "linked"
    assert updated["show_active"] and updated["show_upcoming"]
    for legacy, expected in ((True, "not_linked"), (False, "all")):
        updated = InventoryFilterSettings.normalize({"show_only_not_linked": legacy}, current)
        assert updated["link_status"] == expected
        assert "show_only_not_linked" not in updated


@pytest.mark.asyncio
@pytest.mark.parametrize("previous", [False, True])
async def test_failed_override_save_preserves_live_value_and_retry_refreshes(tmp_path, previous):
    with patch("src.config.settings.SETTINGS_PATH", tmp_path / "settings.json"):
        settings = Settings()
        settings.allow_unlinked_campaigns = previous
        settings.save()
        callback = MagicMock()
        broadcaster = AsyncMock()
        console = MagicMock()
        manager = SettingsManager(broadcaster, settings, console, on_change=callback)
        with (
            patch.object(settings, "save", side_effect=OSError("disk full")),
            pytest.raises(OSError, match="disk full"),
        ):
            manager.update_settings({"allow_unlinked_campaigns": not previous})
        await asyncio.sleep(0)
        assert settings.allow_unlinked_campaigns is previous
        assert Settings().allow_unlinked_campaigns is previous
        callback.assert_not_called()
        broadcaster.emit.assert_not_called()
        console.print.assert_not_called()
        result = manager.update_settings({"allow_unlinked_campaigns": not previous})
        await asyncio.sleep(0)
        assert result["allow_unlinked_campaigns"] is not previous
        assert Settings().allow_unlinked_campaigns is not previous
        callback.assert_called_once()
        broadcaster.emit.assert_called_once()
