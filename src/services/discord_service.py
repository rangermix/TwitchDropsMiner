"""Discord notification service for drop claims."""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

import aiohttp


if TYPE_CHECKING:
    from src.models.drop import TimedDrop


logger = logging.getLogger("TwitchDrops")

# Twitch purple (#9146FF) as used in the browser interface.
EMBED_COLOR = 9127187

# Discord markdown characters that need a backslash escape to render literally.
_MARKDOWN_CHARS = "\\*_~|`"


def _escape_markdown(text: str) -> str:
    """Escape Discord markdown special characters in dynamic values.

    Embed field values are interpreted as markdown, so names coming from Twitch
    must be escaped to avoid e.g. bold/italic/strikethrough/spoiler formatting or
    inline code injection. ``@everyone`` and ``@here`` are neutralized because
    webhooks are not allowed to ping by default, and echoes of mention attempts
    should never become real pings.
    """
    escaped = ""
    for char in text:
        if char in _MARKDOWN_CHARS:
            escaped += f"\\{char}"
        else:
            escaped += char
    return (
        escaped.replace("@everyone", "@\u200beveryone")
        .replace("@here", "@\u200bhere")
        .replace("<@", "<@\u200b")
    )


def _truncate(text: str, limit: int = 1024) -> str:
    """Truncate a value to Discord's per-field limit (1024 chars)."""
    if len(text) <= limit:
        return text
    return f"{text[: limit - 1].rstrip()}…"


class DiscordNotifier:
    """Service for sending drop claim notifications via a Discord webhook."""

    def __init__(self, webhook_url: str | None = None):
        """
        Initialize Discord notifier.

        Args:
            webhook_url: Discord webhook URL (e.g.,
                https://discord.com/api/webhooks/ID/TOKEN)
        """
        self.webhook_url = webhook_url
        self.enabled = bool(webhook_url)

    async def notify_drop_claimed(self, drop: TimedDrop) -> bool:
        """
        Send an embed notification when a drop is claimed.

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

            fields = [
                {"name": "Campaign", "value": campaign.name, "inline": True},
                {"name": "Game", "value": campaign.game.name, "inline": True},
                {"name": "Drop", "value": drop.name, "inline": True},
                {"name": "Reward", "value": benefits_text, "inline": False},
            ]

            embed = {
                "title": "🎁 Drop Claimed!",
                "color": EMBED_COLOR,
                "fields": fields,
                "footer": {"text": "TwitchDropsMiner Bot"},
            }

            return await self._send_embed(embed)
        except Exception as e:
            logger.warning(f"Failed to send Discord notification: {e}")
            return False

    async def test_connection(self) -> bool:
        """
        Test Discord webhook connection.

        Returns:
            True if the webhook is reachable and accepts messages, False otherwise
        """
        if not self.enabled:
            return False

        try:
            embed = {
                "title": "✅ Discord connection test successful!",
                "color": EMBED_COLOR,
                "description": "You will receive notifications here when drops are claimed.",
                "footer": {"text": "TwitchDropsMiner Bot"},
            }
            return await self._send_embed(embed)
        except Exception as e:
            logger.warning(f"Discord connection test failed: {e}")
            return False

    async def _send_embed(self, embed: dict) -> bool:
        """Send an embed payload to the Discord webhook URL.

        Args:
            embed: Discord embed object

        Returns:
            True if the webhook accepted the message
        """
        if not self.webhook_url:
            return False

        embed = _escape_embed(embed)
        payload = {"embeds": [embed]}
        try:
            async with aiohttp.ClientSession() as session, session.post(
                self.webhook_url,
                json=payload,
                timeout=aiohttp.ClientTimeout(total=10),
            ) as response:
                if response.status in (200, 204):
                    logger.debug("Discord notification sent successfully")
                    return True
                error_text = await response.text()
                logger.warning(f"Discord API error {response.status}: {error_text}")
                return False
        except asyncio.TimeoutError:
            logger.warning("Discord notification timeout")
            return False
        except Exception as e:
            logger.warning(f"Discord notification error: {e}")
            return False


def _escape_embed(embed: dict) -> dict:
    """Return a copy of an embed with dynamic string values escaped/truncated.

    Only the free-text fields (title, description, field names/values, footer
    text) are touched; structural values such as ``color`` pass through.
    """
    escaped = dict(embed)
    escaped["title"] = _escape_markdown(str(embed["title"]))
    if "description" in embed:
        escaped["description"] = _escape_markdown(str(embed["description"]))
    if "footer" in embed and "text" in embed["footer"]:
        escaped["footer"] = {
            **embed["footer"],
            "text": _escape_markdown(str(embed["footer"]["text"])),
        }
    if "fields" in embed:
        escaped["fields"] = [
            {
                **field,
                "name": _truncate(_escape_markdown(str(field.get("name", "")))),
                "value": _truncate(_escape_markdown(str(field.get("value", "")))),
            }
            for field in embed["fields"]
        ]
    return escaped
