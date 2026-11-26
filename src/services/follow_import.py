"""Import the channels the account follows into the idle watching list."""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

import aiohttp

from src.api.gql_client import private_gql_discovery
from src.config import GQL_OPERATIONS
from src.config.settings import IdleChannelSettings
from src.exceptions import ExitRequest, MinerException


if TYPE_CHECKING:
    from src.core.client import Twitch


logger = logging.getLogger("TwitchDrops")


class FollowImportError(Exception):
    """The follow list could not be read."""


class FollowImporter:
    """Read the followed logins and merge them into the idle list.

    The follow list is account data: it is read under private_gql_discovery() and never logged,
    only the three counts of the merge are.
    """

    MAX_PAGES = 20
    PAGE_TIMEOUT = 30

    def __init__(self, twitch: Twitch) -> None:
        self._twitch = twitch

    async def fetch(self) -> list[str]:
        """Valid lowercase logins, oldest follow first; at most MAX_PAGES pages, ending at a page without a cursor."""
        logins: list[str] = []
        cursor = ""
        for _page in range(self.MAX_PAGES):
            try:
                with private_gql_discovery():
                    response = await asyncio.wait_for(
                        self._twitch.gql_request(GQL_OPERATIONS["ChannelFollows"].with_variables({"cursor": cursor})),
                        self.PAGE_TIMEOUT,
                    )
                follows = response["data"]["user"]["follows"]  # type: ignore[index,call-overload]
                if response.get("errors") or not isinstance(follows["edges"], list):  # type: ignore[union-attr]
                    raise FollowImportError
                edges = follows["edges"]
                # ASCII is checked before lower(): the Kelvin sign would lowercase to an ASCII "k"
                logins += [n["login"].lower() for e in edges if isinstance((n := e["node"]), dict)
                           and isinstance(n.get("login"), str) and n["login"].isascii()]
                cursor = edges[-1].get("cursor") if edges else None
                more = follows["pageInfo"]["hasNextPage"] is True
            except ExitRequest:
                raise
            except (MinerException, aiohttp.ClientError, TimeoutError, KeyError, TypeError, IndexError,
                    AttributeError) as exc:
                # no response text: it holds account data
                logger.warning("Followed channels import failed (%s)", type(exc).__name__)
                raise FollowImportError from None
            if not more or not isinstance(cursor, str) or not cursor:
                break
        return [login for login in logins if IdleChannelSettings.LOGIN.fullmatch(login)]

    @staticmethod
    def merge(current: list[str], followed: list[str]) -> tuple[list[str], dict[str, int]]:
        """The list with the new follows appended up to the cap, and the counts for the response."""
        known = set(current)
        new = [login for login in dict.fromkeys(followed) if login not in known]
        room = max(IdleChannelSettings.MAX_CHANNELS - len(current), 0)
        counts = {
            "added": min(len(new), room),
            "already_listed": len(set(followed) & known),
            "over_limit": max(len(new) - room, 0),
        }
        return [*current, *new[:room]], counts
