"""Read campaign metadata from a public mirror when Twitch's GraphQL is gated.

Twitch gates its own drop-campaign GraphQL behind a Kasada anti-bot check that
some hosting IP ranges can never pass, so this service reads the same campaign
data from a public mirror instead. Only campaign metadata comes from here;
watch progress still comes from Twitch's ungated ``Inventory`` query.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import aiohttp


if TYPE_CHECKING:
    from src.config import JsonType


logger = logging.getLogger("TwitchDrops")


class PublicCatalog:
    """Fetch Twitch drop-campaign metadata from a public catalog mirror."""

    def __init__(self, url: str) -> None:
        """Initialize the catalog with a lazily-created HTTP session."""
        self._url = url.rstrip()
        self._session: aiohttp.ClientSession | None = None

    async def campaigns(self) -> list[JsonType]:
        """Fetch and flatten campaign metadata, returning an empty list on failure."""
        try:
            if self._session is None or self._session.closed:
                self._session = aiohttp.ClientSession(
                    timeout=aiohttp.ClientTimeout(total=30),
                    headers={
                        "Accept": "application/json",
                        "User-Agent": "TwitchDropsMiner",
                    },
                )

            async with self._session.get(self._url) as response:
                if response.status != 200:
                    raise RuntimeError(f"catalog returned HTTP {response.status}")
                payload = await response.json()

            if not isinstance(payload, list):
                raise TypeError("catalog response is not a list")

            campaigns: list[JsonType] = []
            for group in payload:
                if not isinstance(group, dict):
                    continue
                rewards = group.get("rewards")
                if not isinstance(rewards, list):
                    continue
                for reward in rewards:
                    if not isinstance(reward, dict):
                        continue
                    reward.setdefault("self", {"isAccountConnected": True})
                    # The mirror omits `channels` on campaigns that have no
                    # participating-channel list; the campaign model indexes it
                    # directly, so normalise the field here.
                    allow = reward.get("allow")
                    if not isinstance(allow, dict):
                        allow = {}
                    allow.setdefault("channels", [])
                    allow.setdefault("isEnabled", True)
                    reward["allow"] = allow
                    campaigns.append(reward)
            return campaigns
        except Exception as exc:
            logger.warning("Failed to fetch public campaign catalog: %s", exc)
            return []

    async def close(self) -> None:
        """Close the reusable HTTP session when one has been created."""
        if self._session is not None and not self._session.closed:
            await self._session.close()
        self._session = None
