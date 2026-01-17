import unittest
from unittest.mock import AsyncMock, MagicMock

from yarl import URL

from src.config.settings import Settings
from src.web.managers.console import ConsoleOutputManager
from src.web.managers.settings import SettingsManager


class TestProxySettings(unittest.TestCase):
    def setUp(self):
        self.mock_broadcaster = MagicMock()
        # Use AsyncMock for emit - it returns a coroutine without needing an event loop
        self.mock_broadcaster.emit = AsyncMock()

        # Create a pure mock Settings without wrapping a real instance
        # This avoids file I/O during tests
        self.mock_settings = MagicMock(spec=Settings)
        self.mock_settings.proxy = URL()  # Default empty proxy
        self.mock_console = MagicMock(spec=ConsoleOutputManager)

        # Mock asyncio.create_task
        self.create_task_patcher = unittest.mock.patch("asyncio.create_task")
        self.mock_create_task = self.create_task_patcher.start()

    def tearDown(self):
        self.create_task_patcher.stop()

    def test_update_proxy_setting(self):
        manager = SettingsManager(self.mock_broadcaster, self.mock_settings, self.mock_console)

        # Test setting a proxy
        proxy_url = "http://user:pass@localhost:8080"
        manager.update_settings({"proxy": proxy_url})

        self.assertEqual(self.mock_settings.proxy, URL(proxy_url))
        self.mock_console.print.assert_called_with(
            "Setting changed: proxy = http://user:pass@localhost:8080"
        )

        # Test clearing a proxy
        manager.update_settings({"proxy": ""})
        self.assertEqual(self.mock_settings.proxy, URL())
        self.mock_console.print.assert_called_with("Proxy cleared")

    def test_proxy_persistence_trigger(self):
        manager = SettingsManager(self.mock_broadcaster, self.mock_settings, self.mock_console)
        manager.update_settings({"proxy": "http://1.2.3.4:8080"})

        self.mock_settings.save.assert_called()


if __name__ == "__main__":
    unittest.main()
