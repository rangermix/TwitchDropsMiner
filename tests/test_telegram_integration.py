"""Unit coverage for the Telegram notification path.

Covers the review findings:
- the Telegram bot token is never echoed through the web API/socket
  (only a configured flag and a masked placeholder are returned),
- updates accept a replacement token without echoing the stored value,
- direct and websocket claims notify once on the successful claim transition,
- Telegram failures do not change successful Twitch claim results,
- dynamic campaign/game/drop/reward names are HTML-escaped in messages.
"""

import asyncio
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from src.config.settings import Settings
from src.models.drop import BaseDrop
from src.services.message_handlers import MessageHandlerService
from src.services.telegram_service import TelegramNotifier
from src.web.managers.console import ConsoleOutputManager
from src.web.managers.settings import TELEGRAM_TOKEN_MASK, SettingsManager
from tests.test_watch_drop_filtering import _campaign, _drop


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
        self.drop_data = _drop("drop-1", "Watch", 30)
        self.campaign = _campaign("telegram", [self.drop_data])
        self.drop = self.campaign.timed_drops["drop-1"]
        self.drop.update_claim("inst-1")
        self.twitch = self.campaign._twitch
        self.twitch.settings.telegram_bot_token = "123456:ABC"
        self.twitch.settings.telegram_chat_id = "42"
        self.twitch.gql_request = AsyncMock(
            return_value={"data": {"claimDropRewards": {"status": "ELIGIBLE_FOR_ALL"}}}
        )
        self.twitch.gui.broadcast_wanted_items_now = AsyncMock()
        self.twitch.watching_channel.get_with_default.return_value = None
        self.twitch._drops = {self.drop.id: self.drop}
        self.service = MessageHandlerService(self.twitch)
        self.sent = self.enterContext(
            patch.object(TelegramNotifier, "_send_message", new=AsyncMock(return_value=True))
        )
        self.enterContext(patch("src.services.message_handlers.asyncio.sleep", new=AsyncMock()))

    def _claim_message(self):
        return {
            "type": "drop-claim",
            "data": {"drop_id": "drop-1", "drop_instance_id": "inst-1"},
        }

    async def test_direct_inventory_claim_sends_notification(self):
        self.assertTrue(await self.drop.claim())
        self.assertTrue(self.drop.is_claimed)
        self.sent.assert_awaited_once()
        self.assertIn("Campaign telegram", self.sent.await_args.args[0])
        self.assertIn("Reward Watch", self.sent.await_args.args[0])
        self.twitch.gui.broadcast_wanted_items_now.assert_awaited_once_with()

    async def test_base_drop_claim_also_sends_notification(self):
        drop = BaseDrop(self.campaign, self.drop_data, {})
        drop.update_claim("inst-1")
        self.assertTrue(await drop.claim())
        self.sent.assert_awaited_once()

    async def test_websocket_claim_sends_one_notification(self):
        await self.service.process_drops(1, self._claim_message())
        self.assertTrue(self.drop.is_claimed)
        self.sent.assert_awaited_once()

    async def test_repeated_websocket_claim_does_not_notify_twice(self):
        await self.service.process_drops(1, self._claim_message())
        await self.service.process_drops(1, self._claim_message())
        self.sent.assert_awaited_once()
        self.twitch.gql_request.assert_awaited_once()

    async def test_inventory_claim_followed_by_websocket_notifies_once(self):
        self.assertTrue(await self.drop.claim())
        await self.service.process_drops(1, self._claim_message())
        self.sent.assert_awaited_once()

    async def test_failed_claim_does_not_send_notification(self):
        self.twitch.gql_request.return_value = {"data": {"claimDropRewards": None}}
        await self.service.process_drops(1, self._claim_message())
        self.assertFalse(self.drop.is_claimed)
        self.sent.assert_not_awaited()

    async def test_unconfigured_claim_does_not_send_notification(self):
        self.twitch.settings.telegram_bot_token = ""
        self.assertTrue(await self.drop.claim())
        self.sent.assert_not_awaited()

    async def test_notification_rejection_preserves_successful_claim(self):
        self.sent.return_value = False
        self.assertTrue(await self.drop.claim())
        self.assertTrue(self.drop.is_claimed)
        self.assertEqual(self.drop.current_minutes, self.drop.required_minutes)
        self.sent.assert_awaited_once()

    async def test_notification_exception_preserves_successful_claim(self):
        with patch.object(
            TelegramNotifier, "notify_drop_claimed", new=AsyncMock(side_effect=RuntimeError("failed"))
        ) as notify:
            self.assertTrue(await self.drop.claim())
        self.assertTrue(self.drop.is_claimed)
        self.assertEqual(self.drop.current_minutes, self.drop.required_minutes)
        notify.assert_awaited_once_with(self.drop)


class TestTelegramTransport(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.client_session = self.enterContext(
            patch("src.services.telegram_service.aiohttp.ClientSession")
        )
        self.session = self.client_session.return_value
        self.session.__aenter__.return_value = self.session
        self.response = self.session.post.return_value.__aenter__.return_value
        self.response.status = 200
        self.response.text = AsyncMock(return_value='{"ok": false, "description": "Forbidden"}')
        self.notifier = TelegramNotifier("123456:ABC", "42")

    async def test_connection_sends_expected_request(self):
        self.assertTrue(await self.notifier.test_connection())
        self.session.post.assert_called_once()
        args, kwargs = self.session.post.call_args
        self.assertEqual(args, ("https://api.telegram.org/bot123456:ABC/sendMessage",))
        self.assertEqual(kwargs["json"]["chat_id"], "42")
        self.assertEqual(kwargs["json"]["parse_mode"], "HTML")
        self.assertIn("test successful", kwargs["json"]["text"])
        self.assertEqual(kwargs["timeout"].total, 10)
        self.session.__aexit__.assert_awaited_once()

    async def test_api_rejection_returns_false(self):
        self.response.status = 403
        self.assertFalse(await self.notifier.test_connection())
        self.response.text.assert_awaited_once()
        self.session.__aexit__.assert_awaited_once()

    async def test_timeout_returns_false_and_closes_session(self):
        self.session.post.return_value.__aenter__.side_effect = asyncio.TimeoutError()
        self.assertFalse(await self.notifier.test_connection())
        self.session.__aexit__.assert_awaited_once()

    async def test_transport_failure_returns_false(self):
        self.session.post.return_value.__aenter__.side_effect = OSError("connection reset")
        self.assertFalse(await self.notifier.test_connection())
        self.session.__aexit__.assert_awaited_once()

    async def test_missing_credentials_never_open_a_session(self):
        for token, chat_id in (("", "42"), ("123456:ABC", "")):
            with self.subTest(token=token, chat_id=chat_id):
                notifier = TelegramNotifier(token, chat_id)
                self.assertFalse(await notifier.test_connection())
                self.assertFalse(await notifier.notify_drop_claimed(MagicMock()))
        self.client_session.assert_not_called()


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
