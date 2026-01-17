import unittest
from unittest.mock import AsyncMock, MagicMock

from src.config.settings import Settings
from src.web.app import SettingsUpdate
from src.web.managers.settings import SettingsManager


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
        self.assertEqual(mock_settings.inventory_filters, inv_filters)
        mock_console.print.assert_called_with(
            "Setting changed: inventory_filters = {'show_upcoming': False}"
        )

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
