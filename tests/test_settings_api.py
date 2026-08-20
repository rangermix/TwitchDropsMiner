import asyncio
import importlib
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from src.config.settings import Settings, default_settings
from src.web.app import SettingsUpdate
from src.web.managers.settings import SettingsManager


web_app = importlib.import_module("src.web.app")


class TestSettingsAPI(unittest.IsolatedAsyncioTestCase):
    def test_settings_update_model(self):
        # Verify model accepts new fields
        update_data = {
            "inventory_filters": {"show_upcoming": True},
            "mining_benefits": {"BADGE": True},
        }
        model = SettingsUpdate(**update_data)
        self.assertEqual(model.inventory_filters, update_data["inventory_filters"])
        self.assertEqual(model.mining_benefits, update_data["mining_benefits"])

    async def test_settings_endpoint_returns_request_scoped_compatibility_response(self):
        expected = {
            "inventory_filters": {
                "show_only_not_linked": False,
                "show_not_linked": True,
            }
        }
        mock_gui = MagicMock()
        mock_gui.settings.update_settings.return_value = expected

        with patch.object(web_app, "gui_manager", mock_gui):
            response = await web_app.update_settings(
                SettingsUpdate(inventory_filters={"show_not_linked": True})
            )

        assert response == {"success": True, "settings": expected}
        mock_gui.settings.update_settings.assert_called_once_with(
            {"inventory_filters": {"show_not_linked": True}}
        )

    async def test_legacy_filter_request_is_echoed_without_changing_current_semantics(self):
        mock_broadcaster = AsyncMock()
        mock_settings = MagicMock(spec=Settings)
        mock_settings.inventory_filters = {
            "game_name_search": ["Game A"],
            "show_active": False,
            "show_benefit_badge": True,
            "show_benefit_emote": True,
            "show_benefit_item": True,
            "show_benefit_other": True,
            "show_expired": False,
            "show_finished": False,
            "show_not_linked": True,
            "show_upcoming": True,
        }
        manager = SettingsManager(mock_broadcaster, mock_settings, MagicMock())

        response_settings = manager.update_settings(
            {"inventory_filters": {"show_active": True, "show_not_linked": True}}
        )
        await asyncio.sleep(0)

        filters = mock_settings.inventory_filters
        self.assertTrue(filters["show_active"])
        self.assertEqual(filters["game_name_search"], ["Game A"])
        self.assertFalse(filters["show_only_not_linked"])
        self.assertNotIn("show_not_linked", filters)
        self.assertTrue(response_settings["inventory_filters"]["show_not_linked"])
        self.assertFalse(response_settings["inventory_filters"]["show_only_not_linked"])
        self.assertNotIn("show_not_linked", manager.get_settings()["inventory_filters"])
        mock_broadcaster.emit.assert_awaited_once_with(
            "settings_updated", response_settings
        )
        mock_settings.save.assert_called_once()

    async def test_persisted_legacy_filter_is_removed_without_becoming_only_not_linked(self):
        mock_broadcaster = AsyncMock()
        mock_settings = MagicMock(spec=Settings)
        mock_settings.inventory_filters = {
            "show_not_linked": True,
        }
        manager = SettingsManager(mock_broadcaster, mock_settings, MagicMock())

        response_settings = manager.update_settings(
            {"inventory_filters": {"show_active": True}}
        )
        await asyncio.sleep(0)

        filters = mock_settings.inventory_filters
        self.assertTrue(filters["show_active"])
        self.assertFalse(filters["show_only_not_linked"])
        self.assertNotIn("show_not_linked", filters)
        self.assertNotIn("show_not_linked", response_settings["inventory_filters"])

    async def test_settings_manager_networking(self):
        # Mock dependencies
        mock_broadcaster = AsyncMock()
        mock_settings = MagicMock(spec=Settings)
        # Initialize mock attributes with default values for comparison
        mock_settings.inventory_filters = {}
        mock_settings.mining_benefits = {}
        mock_settings.games_to_watch = []

        mock_console = MagicMock()
        mock_callback = MagicMock()

        manager = SettingsManager(
            mock_broadcaster, mock_settings, mock_console, on_change=mock_callback
        )

        # 1. Update Inventory Filters (does NOT trigger callback per implementation)
        inv_filters = {"show_upcoming": False}
        manager.update_settings({"inventory_filters": inv_filters})
        mock_callback.assert_not_called()  # inventory_filters has should_trigger_update=False
        self.assertFalse(mock_settings.inventory_filters["show_upcoming"])
        self.assertEqual(
            set(mock_settings.inventory_filters),
            set(default_settings["inventory_filters"]),
        )
        self.assertIn("Setting changed: inventory_filters", mock_console.print.call_args.args[0])

        # 2. Update Mining Benefits (SHOULD trigger callback)
        benefits = {"BADGE": False}
        manager.update_settings({"mining_benefits": benefits})
        mock_callback.assert_called_once()
        self.assertEqual(mock_settings.mining_benefits, benefits)
        mock_console.print.assert_called_with("Setting changed: mining_benefits = {'BADGE': False}")
        mock_callback.reset_mock()

        # 3. Update Games to Watch (SHOULD trigger callback)
        games = ["Game 1"]
        manager.update_settings({"games_to_watch": games})
        mock_callback.assert_called_once()


if __name__ == "__main__":
    unittest.main()
