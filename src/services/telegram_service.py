"""Telegram notification service for drop claims."""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

import aiohttp


if TYPE_CHECKING:
    from src.models.drop import TimedDrop


logger = logging.getLogger("TwitchDrops")

# Telegram Bot API base URL
TELEGRAM_API = "https://api.telegram.org/bot"


class TelegramNotifier:
    """Service for sending drop claim notifications via Telegram."""

    def __init__(self, bot_token: str | None = None, chat_id: str | None = None):
        """
        Initialize Telegram notifier.

        Args:
            bot_token: Telegram bot token (e.g., 123456789:ABCdefGHIjklMNOpqrsTUVwxyz)
            chat_id: Telegram chat ID to send messages to (numeric ID or @username)
        """
        self.bot_token = bot_token
        self.chat_id = chat_id
        self.enabled = bool(bot_token and chat_id)

    async def notify_drop_claimed(self, drop: TimedDrop) -> bool:
        """
        Send notification when a drop is claimed.

        Args:
            drop: The TimedDrop that was claimed

        Returns:
            True if notification was sent successfully, False otherwise
        """
        if not self.enabled:
            return False

        try:
            campaign = drop.campaign
            benefits_text = ", ".join(b.name for b in drop.benefits) or "Unknown"

            message = (
                f"🎮 <b>Drop Claimed!</b>\n"
                f"<b>Campaign:</b> {campaign.name}\n"
                f"<b>Game:</b> {campaign.game.name}\n"
                f"<b>Drop:</b> {drop.name}\n"
                f"<b>Reward:</b> {benefits_text}"
            )

            return await self._send_message(message)
        except Exception as e:
            logger.warning(f"Failed to send Telegram notification: {e}")
            return False

    async def notify_drop_progress(self, drop: TimedDrop) -> bool:
        """
        Send notification when a drop reaches completion.

        Args:
            drop: The TimedDrop that reached 100%

        Returns:
            True if notification was sent successfully, False otherwise
        """
        if not self.enabled:
            return False

        try:
            campaign = drop.campaign
            benefits_text = ", ".join(b.name for b in drop.benefits) or "Unknown"

            message = (
                f"⏱️ <b>Drop Ready to Claim!</b>\n"
                f"<b>Campaign:</b> {campaign.name}\n"
                f"<b>Game:</b> {campaign.game.name}\n"
                f"<b>Drop:</b> {drop.name}\n"
                f"<b>Reward:</b> {benefits_text}\n"
                f"✅ <i>All required time reached - ready to claim!</i>"
            )

            return await self._send_message(message)
        except Exception as e:
            logger.warning(f"Failed to send Telegram progress notification: {e}")
            return False

    async def test_connection(self) -> bool:
        """
        Test Telegram bot connection and chat ID.

        Returns:
            True if connection and chat ID are valid, False otherwise
        """
        if not self.bot_token or not self.chat_id:
            return False

        try:
            message = "✅ Telegram notification test successful!"
            return await self._send_message(message)
        except Exception as e:
            logger.warning(f"Telegram connection test failed: {e}")
            return False

    async def _send_message(self, text: str) -> bool:
        """
        Internal method to send a message via Telegram API.

        Args:
            text: HTML-formatted message text

        Returns:
            True if message was sent successfully
        """
        if not self.bot_token or not self.chat_id:
            return False

        try:
            url = f"{TELEGRAM_API}{self.bot_token}/sendMessage"
            data = {"chat_id": self.chat_id, "text": text, "parse_mode": "HTML"}

            async with aiohttp.ClientSession() as session, session.post(url, json=data, timeout=aiohttp.ClientTimeout(total=10)) as response:
                if response.status == 200:
                    logger.debug("Telegram notification sent successfully")
                    return True
                else:
                    error_text = await response.text()
                    logger.warning(f"Telegram API error {response.status}: {error_text}")
                    return False
        except asyncio.TimeoutError:
            logger.warning("Telegram notification timeout")
            return False
        except Exception as e:
            logger.warning(f"Telegram notification error: {e}")
            return False
