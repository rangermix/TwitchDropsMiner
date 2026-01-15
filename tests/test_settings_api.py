import asyncio
import unittest
from unittest.mock import MagicMock, AsyncMock, patch
from src.web.app import SettingsUpdate
from src.web.managers.settings import SettingsManager
from src.config.settings import Settings

class TestSettingsAPI(unittest.IsolatedAsyncioTestCase):
    def test_settings_update_model(self):
        # Verify model accepts new fields
        update_data = {
            "inventory_filters": {"show_upcoming": True},
            "mining_benefits": {"BADGE": True}
        }
        model = SettingsUpdate(**update_data)
        self.assertEqual(model.inventory_filters, update_data["inventory_filters"])
        self.assertEqual(model.mining_benefits, update_data["mining_benefits"])

    def test_settings_update_new_dropdown_filters(self):
        # Verify new dropdown filter fields (account_link_filter, progress_filter)
        update_data = {
            "inventory_filters": {
                "account_link_filter": "linked",
                "progress_filter": "finished",
                "show_active": True
            }
        }
        model = SettingsUpdate(**update_data)
        self.assertEqual(model.inventory_filters["account_link_filter"], "linked")
        self.assertEqual(model.inventory_filters["progress_filter"], "finished")
        self.assertEqual(model.inventory_filters["show_active"], True)

    async def test_settings_manager_networking(self):
        # Mock dependencies
        mock_broadcaster = AsyncMock()
        mock_settings = MagicMock(spec=Settings)
        # Configure mock to satisfy get_settings() calls
        mock_settings.language = "en"
        mock_settings.dark_mode = False
        mock_settings.games_to_watch = []
        mock_settings.proxy = "http://proxy"
        mock_settings.connection_quality = 1
        mock_settings.minimum_refresh_interval_minutes = 30
        
        mock_console = MagicMock()
        mock_callback = MagicMock()
        
        manager = SettingsManager(mock_broadcaster, mock_settings, mock_console, on_change=mock_callback)
        
        # 1. Update Inventory Filters (Should NOT trigger callback if not games/benefits)
        inv_filters = {"show_upcoming": False}
        manager.update_settings({"inventory_filters": inv_filters})
        mock_callback.assert_not_called()
        self.assertEqual(mock_settings.inventory_filters, inv_filters)
        mock_console.print.assert_called_with("Setting changed: inventory_filters updated")
        
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


if __name__ == '__main__':
    unittest.main()
