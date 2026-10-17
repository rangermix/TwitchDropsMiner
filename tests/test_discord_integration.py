"""Unit coverage for the Discord notification path.

Covers the review findings:
- the Discord webhook URL is never echoed through the web API/socket
  (only a configured flag and a masked placeholder are returned),
- updates accept a replacement webhook URL without echoing the stored value,
- the "Drop Claimed!" Discord notification is gated on a successful claim,
- dynamic campaign/game/drop/reward names have Discord markdown escaped and
  mention attempts neutralized in the embed payload.
"""

import asyncio
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from src.config.settings import Settings
from src.services.discord_service import DiscordNotifier, _escape_embed
from src.models.drop import BaseDrop
from src.services.telegram_service import TelegramNotifier
from src.services.message_handlers import MessageHandlerService
from tests.test_watch_drop_filtering import _campaign, _drop
from src.web.managers.console import ConsoleOutputManager
from src.web.managers.settings import DISCORD_WEBHOOK_MASK, SettingsManager


class MockResponseContext:
    def __init__(self, response_or_exc):
        self.response_or_exc = response_or_exc

    async def __aenter__(self):
        if isinstance(self.response_or_exc, Exception):
            raise self.response_or_exc
        return self.response_or_exc

    async def __aexit__(self, exc_type, exc, tb):
        pass


class TestSettingsWebhookMasking(unittest.IsolatedAsyncioTestCase):
    def _manager(self, webhook_url: str = ""):
        self.mock_broadcaster = MagicMock()
        self.mock_broadcaster.emit = AsyncMock()
        mock_settings = MagicMock(spec=Settings)
        mock_settings.discord_webhook_url = webhook_url
        mock_settings.inventory_filters = {}
        return SettingsManager(
            self.mock_broadcaster,
            mock_settings,
            MagicMock(spec=ConsoleOutputManager),
        )

    async def test_get_settings_never_returns_the_stored_webhook_url(self):
        secret = "https://discord.com/api/webhooks/111/aaa_bbb"
        manager = self._manager(webhook_url=secret)
        response = manager.get_settings()
        self.assertEqual(response["discord_webhook_url"], DISCORD_WEBHOOK_MASK)
        self.assertTrue(response["discord_configured"])
        self.assertNotIn("aaa_bbb", str(response))

    async def test_get_settings_returns_configured_flag_when_empty(self):
        manager = self._manager(webhook_url="")
        response = manager.get_settings()
        self.assertEqual(response["discord_webhook_url"], "")
        self.assertFalse(response["discord_configured"])

    async def test_update_accepts_replacement_webhook_url_without_echoing(self):
        manager = self._manager(webhook_url="https://discord.com/api/webhooks/111/old")
        manager.update_settings({"discord_webhook_url": "https://discord.com/api/webhooks/222/new"})
        await asyncio.sleep(0)
        self.assertEqual(
            manager._settings.discord_webhook_url,
            "https://discord.com/api/webhooks/222/new",
        )
        emitted = self.mock_broadcaster.emit.await_args.args[1]
        self.assertEqual(emitted["discord_webhook_url"], DISCORD_WEBHOOK_MASK)
        self.assertTrue(emitted["discord_configured"])
        console_output = manager._console.print.call_args_list
        display = str(sorted(console_output))
        self.assertNotIn("https://discord.com/api/webhooks/222/new", display)

    async def test_update_ignores_masked_placeholder_echo(self):
        manager = self._manager(webhook_url="https://discord.com/api/webhooks/333/real")
        manager.update_settings({"discord_webhook_url": DISCORD_WEBHOOK_MASK})
        await asyncio.sleep(0)
        self.assertEqual(
            manager._settings.discord_webhook_url,
            "https://discord.com/api/webhooks/333/real",
        )

    async def test_update_ignores_empty_webhook_url(self):
        manager = self._manager(webhook_url="https://discord.com/api/webhooks/333/real")
        manager.update_settings({"discord_webhook_url": ""})
        await asyncio.sleep(0)
        self.assertEqual(
            manager._settings.discord_webhook_url,
            "https://discord.com/api/webhooks/333/real",
        )

    async def test_proxy_only_update_does_not_touch_discord(self):
        manager = self._manager(webhook_url="https://discord.com/api/webhooks/333/real")
        manager.update_settings({"proxy": "http://1.2.3.4:8080"})
        await asyncio.sleep(0)
        self.assertEqual(
            manager._settings.discord_webhook_url,
            "https://discord.com/api/webhooks/333/real",
        )


class TestNotificationGatedOnClaim(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.drop_data = _drop("drop-1", "Watch", 30)
        self.campaign = _campaign("discord", [self.drop_data])
        self.drop = self.campaign.timed_drops["drop-1"]
        self.drop.update_claim("inst-1")
        self.twitch = self.campaign._twitch
        self.twitch.settings.discord_webhook_url = "https://discord.com/api/webhooks/111/aaa_bbb"
        self.twitch.gql_request = AsyncMock(
            return_value={"data": {"claimDropRewards": {"status": "ELIGIBLE_FOR_ALL"}}}
        )
        self.twitch.gui.broadcast_wanted_items_now = AsyncMock()
        self.twitch.watching_channel.get_with_default.return_value = None
        self.twitch._drops = {self.drop.id: self.drop}
        self.service = MessageHandlerService(self.twitch)
        self.sent = self.enterContext(
            patch.object(DiscordNotifier, "_send_embed", new=AsyncMock(return_value=True))
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
        self.assertIn("Campaign discord", str(self.sent.await_args.args[0]))
        self.assertIn("Reward Watch", str(self.sent.await_args.args[0]))
        self.twitch.gui.broadcast_wanted_items_now.assert_awaited_once_with()
        self.twitch.drop_history.record.assert_called_once_with(self.drop, self.campaign)

    async def test_telegram_failure_does_not_block_discord_or_history(self):
        self.twitch.settings.telegram_bot_token = "dummy-token"
        self.twitch.settings.telegram_chat_id = "42"
        with patch.object(
            TelegramNotifier, "notify_drop_claimed", new=AsyncMock(side_effect=RuntimeError("failed"))
        ) as telegram:
            self.assertTrue(await self.drop.claim())
        telegram.assert_awaited_once_with(self.drop)
        self.sent.assert_awaited_once()
        self.twitch.drop_history.record.assert_called_once_with(self.drop, self.campaign)

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
        self.twitch.drop_history.record.assert_called_once_with(self.drop, self.campaign)

    async def test_inventory_claim_followed_by_websocket_notifies_once(self):
        self.assertTrue(await self.drop.claim())
        await self.service.process_drops(1, self._claim_message())
        self.sent.assert_awaited_once()

    async def test_failed_claim_does_not_send_notification(self):
        self.twitch.gql_request.return_value = {"data": {"claimDropRewards": None}}
        await self.service.process_drops(1, self._claim_message())
        self.assertFalse(self.drop.is_claimed)
        self.sent.assert_not_awaited()
        self.twitch.drop_history.record.assert_not_called()

    async def test_already_claimed_drop_is_not_added_to_history_again(self):
        self.drop.is_claimed = True
        self.assertTrue(await self.drop.claim())
        self.twitch.drop_history.record.assert_not_called()

    async def test_unconfigured_claim_does_not_send_notification(self):
        self.twitch.settings.discord_webhook_url = ""
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
            DiscordNotifier, "notify_drop_claimed", new=AsyncMock(side_effect=RuntimeError("failed"))
        ) as notify:
            self.assertTrue(await self.drop.claim())
        self.assertTrue(self.drop.is_claimed)
        self.assertEqual(self.drop.current_minutes, self.drop.required_minutes)
        notify.assert_awaited_once_with(self.drop)


class TestDiscordMessageEscaping(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.notifier = DiscordNotifier("https://discord.com/api/webhooks/111/aaa_bbb")

        self.campaign = MagicMock()
        self.campaign.name = "Sprint *&^ Sharpen _"
        self.campaign.game = MagicMock()
        self.campaign.game.name = "Game <One> & |Co~"

        self.drop = MagicMock()
        self.drop.name = "Drop @everyone @here"
        self.drop.campaign = self.campaign

        benefit = MagicMock()
        benefit.name = "Mask `code` & Badge"
        self.drop.benefits = [benefit]

    async def _post_embed(self):
        captured = {}

        with unittest.mock.patch("aiohttp.ClientSession") as mock_session_cls:
            mock_session = MagicMock()
            mock_session_cls.return_value.__aenter__.return_value = mock_session

            mock_response = AsyncMock()
            mock_response.status = 204
            mock_session.post.side_effect = lambda *args, **kwargs: MockResponseContext(
                mock_response
            )

            result = await self.notifier.notify_drop_claimed(self.drop)

        captured["result"] = result
        captured["payload"] = mock_session.post.call_args.kwargs["json"]
        return captured

    async def test_claim_notification_escapes_markdown_and_mentions(self):
        captured = await self._post_embed()
        self.assertTrue(captured["result"])
        embed = captured["payload"]["embeds"][0]
        field_values = {f["name"]: f["value"] for f in embed["fields"]}
        self.assertEqual(field_values["Campaign"], "Sprint \\*&^ Sharpen \\_")
        self.assertEqual(field_values["Game"], "Game <One> & \\|Co\\~")
        self.assertEqual(field_values["Drop"], "Drop @\u200beveryone @\u200bhere")
        self.assertEqual(field_values["Reward"], "Mask \\`code\\` & Badge")
        self.assertNotIn("@everyone", field_values["Drop"])
        self.assertNotIn("@here", field_values["Drop"])

    async def test_claim_notification_sends_with_twitch_purple_color(self):
        captured = await self._post_embed()
        embed = captured["payload"]["embeds"][0]
        self.assertEqual(embed["color"], 0x9146FF)
        self.assertEqual(embed["title"], "🎁 Drop Claimed!")
        self.assertEqual(embed["footer"]["text"], "TwitchDropsMiner Bot")


class TestDiscordSending(unittest.IsolatedAsyncioTestCase):
    def test_send_embed_posts_payload_and_accepts_204(self):
        notifier = DiscordNotifier("https://discord.com/api/webhooks/111/aaa_bbb")

        with unittest.mock.patch("aiohttp.ClientSession") as mock_session_cls:
            mock_session = MagicMock()
            mock_session_cls.return_value.__aenter__.return_value = mock_session

            mock_response = AsyncMock()
            mock_response.status = 204
            mock_session.post.side_effect = lambda *args, **kwargs: MockResponseContext(
                mock_response
            )

            result = asyncio.run(
                notifier._send_embed({"title": "Hello", "color": 9127187})
            )

        self.assertTrue(result)
        posted = mock_session.post.call_args.args[0]
        self.assertEqual(posted, "https://discord.com/api/webhooks/111/aaa_bbb")
        payload = mock_session.post.call_args.kwargs["json"]
        self.assertEqual(payload["embeds"][0]["title"], "Hello")

    def test_send_embed_reports_non_success_status(self):
        notifier = DiscordNotifier("https://discord.com/api/webhooks/111/aaa_bbb")

        with unittest.mock.patch("aiohttp.ClientSession") as mock_session_cls:
            mock_session = MagicMock()
            mock_session_cls.return_value.__aenter__.return_value = mock_session

            mock_response = AsyncMock()
            mock_response.status = 403
            mock_response.text = AsyncMock(return_value="Forbidden")
            mock_session.post.side_effect = lambda *args, **kwargs: MockResponseContext(
                mock_response
            )

            result = asyncio.run(
                notifier._send_embed({"title": "Hello", "color": 9127187})
            )

        self.assertFalse(result)

    def test_disabled_notifier_sends_nothing(self):
        notifier = DiscordNotifier(webhook_url=None)
        self.assertFalse(notifier.enabled)
        result = asyncio.run(notifier._send_embed({"title": "Hello"}))
        self.assertFalse(result)

    def test_long_values_are_truncated_to_embed_field_limit(self):
        long_text = "x" * 5000
        embed = _escape_embed(
            {"title": "T", "fields": [{"name": "N", "value": long_text}]}
        )
        self.assertLessEqual(len(embed["fields"][0]["value"]), 1024)


if __name__ == "__main__":
    unittest.main()
