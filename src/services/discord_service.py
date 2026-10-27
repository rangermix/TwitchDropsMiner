"""Discord notification service for drop claims."""

from __future__ import annotations

import asyncio
import logging
import re
from typing import TYPE_CHECKING
from urllib.parse import urlsplit

import aiohttp


if TYPE_CHECKING:
    from src.diagnostics import Diagnostics
    from src.models.drop import BaseDrop


logger = logging.getLogger("TwitchDrops")

# Twitch purple (#9146FF) as used in the browser interface.
EMBED_COLOR = 0x9146FF

# Discord markdown characters that need a backslash escape to render literally.
_MARKDOWN_CHARS = "\\*_~|`[]()"


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

    def __init__(self, webhook_url: str | None = None, *, diagnostics: Diagnostics | None = None):
        """
        Initialize Discord notifier.

        Args:
            webhook_url: Discord webhook URL (e.g.,
                https://discord.com/api/webhooks/ID/TOKEN)
        """
        self.webhook_url = webhook_url if self.is_valid_webhook(webhook_url) else None
        self.enabled = bool(self.webhook_url)
        self.diagnostics = diagnostics

    @staticmethod
    def is_valid_webhook(value: object) -> bool:
        """Accept only Discord's HTTPS webhook endpoint, without URL modifiers."""
        return isinstance(value, str) and re.fullmatch(
            r"https://discord\.com/api/webhooks/[0-9]{1,30}/[A-Za-z0-9_-]{1,200}", value
        ) is not None

    @staticmethod
    def reward_image(drop: BaseDrop) -> str | None:
        """Select public Twitch artwork; never forward credentials or signed URLs."""
        for benefit in drop.benefits:
            value = getattr(benefit, "image_url", None)
            if not isinstance(value, str) or len(value) > 2048 or any(
                ord(char) < 33 or 127 <= ord(char) <= 159 for char in value
            ):
                continue
            try:
                url = urlsplit(value)
                if (url.scheme == "https" and url.hostname == "static-cdn.jtvnw.net"
                        and url.port in (None, 443) and not url.username and not url.password
                        and not url.query and not url.fragment and url.path.startswith("/")):
                    return value
            except ValueError:
                continue
        return None

    async def notify_drop_claimed(self, drop: BaseDrop) -> bool:
        """
        Send an embed notification when a drop is claimed.

        Args:
            drop: The drop that was claimed

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
            image_url = self.reward_image(drop)
            if image_url is not None:
                embed["image"] = {"url": image_url}

            return await self._send_embed(embed)
        except Exception:
            logger.warning("Failed to send Discord notification")
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
        except Exception:
            logger.warning("Discord connection test failed")
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
        payload = {"embeds": [embed], "allowed_mentions": {"parse": []}}
        try:
            async with aiohttp.ClientSession() as session, session.post(
                self.webhook_url,
                json=payload,
                allow_redirects=False,
                timeout=aiohttp.ClientTimeout(total=10),
            ) as response:
                if self.diagnostics is not None:
                    self.diagnostics.record("discord", status=response.status)
                if response.status in (200, 204):
                    logger.debug("Discord notification sent successfully")
                    return True
                logger.warning("Discord notification rejected")
                return False
        except asyncio.TimeoutError:
            if self.diagnostics is not None:
                self.diagnostics.record("discord", outcome="timeout")
            logger.warning("Discord notification timeout")
            return False
        except Exception:
            if self.diagnostics is not None:
                self.diagnostics.record("discord", outcome="connection")
            logger.warning("Discord notification connection failed")
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
