"""Unit coverage for the Telegram notification path.

Covers the review findings:
- the Telegram bot token is never echoed through the web API/socket
  (only a configured flag and a masked placeholder are returned),
- updates accept a replacement token without echoing the stored value,
- the "Drop Claimed!" Telegram notification is gated on a successful claim,
- dynamic campaign/game/drop/reward names are HTML-escaped in messages.
"""

import asyncio
import unittest
from unittest.mock import AsyncMock, MagicMock

from src.config.settings import Settings
from src.services.message_handlers import MessageHandlerService
from src.services.telegram_service import TelegramNotifier
from src.web.managers.console import ConsoleOutputManager
from src.web.managers.settings import TELEGRAM_TOKEN_MASK, SettingsManager


class TestSettingsTokenMasking(unittest.IsolatedAsyncioTestCase):
    def _manager(self, token: str = "", chat_id: str = ""):
        self.mock_broadcaster = MagicMock()
        self.mock_broadcaster.emit = AsyncMock()
        mock_settings = MagicMock(spec=Settings)
        mock_settings.telegram_bot_token = token
        mock_settings.telegram_chat_id = chat_id
        mock_settings.inventory_filters = {}
        return SettingsManager(
            self.mock_broadcaster,
            mock_settings,
            MagicMock(spec=ConsoleOutputManager),
        )

    async def test_get_settings_never_returns_the_stored_token(self):
        manager = self._manager(token="123456:ABC_secret")
        response = manager.get_settings()
        self.assertEqual(response["telegram_bot_token"], TELEGRAM_TOKEN_MASK)
        self.assertTrue(response["telegram_configured"])
        self.assertNotIn("123456:ABC_secret", str(response))

    async def test_get_settings_returns_configured_flag_when_empty(self):
        manager = self._manager(token="", chat_id="")
        response = manager.get_settings()
        self.assertEqual(response["telegram_bot_token"], "")
        self.assertFalse(response["telegram_configured"])

    async def test_update_accepts_replacement_token_without_echoing(self):
        manager = self._manager(token="old-secret", chat_id="42")
        manager.update_settings({"telegram_bot_token": "new-secret"})
        await asyncio.sleep(0)
        self.assertEqual(manager._settings.telegram_bot_token, "new-secret")
        emitted = self.mock_broadcaster.emit.await_args.args[1]
        self.assertEqual(emitted["telegram_bot_token"], TELEGRAM_TOKEN_MASK)
        self.assertTrue(emitted["telegram_configured"])
        console_output = manager._console.print.call_args_list
        display = str(sorted(console_output))
        self.assertNotIn("new-secret", display)

    async def test_update_ignores_masked_placeholder_echo(self):
        manager = self._manager(token="real-secret", chat_id="42")
        manager.update_settings({"telegram_bot_token": TELEGRAM_TOKEN_MASK})
        await asyncio.sleep(0)
        self.assertEqual(manager._settings.telegram_bot_token, "real-secret")
        self.assertEqual(manager._settings.telegram_chat_id, "42")

    async def test_update_ignores_empty_token(self):
        manager = self._manager(token="real-secret", chat_id="42")
        manager.update_settings({"telegram_bot_token": "", "telegram_chat_id": "7"})
        await asyncio.sleep(0)
        self.assertEqual(manager._settings.telegram_bot_token, "real-secret")
        self.assertEqual(manager._settings.telegram_chat_id, "7")

    async def test_proxy_only_update_does_not_touch_telegram(self):
        manager = self._manager(token="real-secret", chat_id="42")
        manager.update_settings({"proxy": "http://1.2.3.4:8080"})
        await asyncio.sleep(0)
        self.assertEqual(manager._settings.telegram_bot_token, "real-secret")
        self.assertEqual(manager._settings.telegram_chat_id, "42")


class TestNotificationGatedOnClaim(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.mock_twitch = MagicMock()
        self.mock_twitch.settings = MagicMock()
        self.mock_twitch.settings.telegram_bot_token = "123456:ABC"
        self.mock_twitch.settings.telegram_chat_id = "42"
        self.mock_twitch.gql_request = AsyncMock(
            return_value={"data": {"currentUser": {"dropCurrentSession": None}}}
        )

        self.service = MessageHandlerService(self.mock_twitch)

        self.drop = MagicMock()
        self.drop.id = "drop-1"
        self.drop.campaign = MagicMock()
        self.drop.claim = AsyncMock(return_value=True)

        self.channel = MagicMock()
        self.channel.id = 100
        self.mock_twitch.channels.get.return_value = self.channel
        self.mock_twitch.watching_channel.get_with_default.return_value = self.channel
        self.mock_twitch._drops.get.return_value = self.drop

        self.drop.update = MagicMock()

    def _claim_message(self, failed: bool = False):
        self.drop.claim.return_value = not failed
        return {
            "type": "drop-claim",
            "data": {"drop_id": "drop-1", "drop_instance_id": "inst-1"},
        }

    async def test_telegram_notification_sent_on_successful_claim(self):
        with (
            unittest.mock.patch(
                "src.services.message_handlers.asyncio.sleep", new=AsyncMock()
            ),
            unittest.mock.patch.object(
                self.service, "_send_telegram_notification", new=AsyncMock()
            ) as notify,
        ):
            await self.service.process_drops(1, self._claim_message(failed=False))
            notify.assert_awaited_once_with(self.drop)

    async def test_telegram_notification_skipped_on_failed_claim(self):
        with (
            unittest.mock.patch(
                "src.services.message_handlers.asyncio.sleep", new=AsyncMock()
            ),
            unittest.mock.patch.object(
                self.service, "_send_telegram_notification", new=AsyncMock()
            ) as notify,
        ):
            await self.service.process_drops(1, self._claim_message(failed=True))
            notify.assert_not_awaited()


class TestTelegramMessageEscaping(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.notifier = TelegramNotifier("123456:ABC", "42")

        self.campaign = MagicMock()
        self.campaign.name = "Sprint & <Sharpen>"
        self.campaign.game = MagicMock()
        self.campaign.game.name = "Game <One> & Co"

        self.drop = MagicMock()
        self.drop.name = "Drop & <Reward>"
        self.drop.campaign = self.campaign

        benefit = MagicMock()
        benefit.name = "Mask <Special> & Badge"
        self.drop.benefits = [benefit]

    async def _capture_message(self):
        sent = {}

        async def fake_send(text):
            sent["text"] = text
            return True

        self.notifier._send_message = fake_send
        return sent

    async def test_claim_notification_escapes_dynamic_fields(self):
        sent = await self._capture_message()
        result = await self.notifier.notify_drop_claimed(self.drop)
        self.assertTrue(result)
        self.assertIn("Sprint &amp; &lt;Sharpen&gt;", sent["text"])
        self.assertIn("Game &lt;One&gt; &amp; Co", sent["text"])
        self.assertIn("Drop &amp; &lt;Reward&gt;", sent["text"])
        self.assertIn("Mask &lt;Special&gt; &amp; Badge", sent["text"])
        self.assertNotIn("<Sharpen>", sent["text"])

    async def test_progress_notification_escapes_dynamic_fields(self):
        sent = await self._capture_message()
        result = await self.notifier.notify_drop_progress(self.drop)
        self.assertTrue(result)
        self.assertIn("Sprint &amp; &lt;Sharpen&gt;", sent["text"])
        self.assertIn("Game &lt;One&gt; &amp; Co", sent["text"])
        self.assertIn("Drop &amp; &lt;Reward&gt;", sent["text"])
        self.assertIn("Mask &lt;Special&gt; &amp; Badge", sent["text"])
        self.assertNotIn("<Sharpen>", sent["text"])

    async def test_escape_keeps_intentional_bold_markup(self):
        sent = await self._capture_message()
        await self.notifier.notify_drop_claimed(self.drop)
        self.assertIn("<b>Drop Claimed!</b>", sent["text"])
        self.assertIn("<b>Campaign:</b>", sent["text"])


if __name__ == "__main__":
    unittest.main()
