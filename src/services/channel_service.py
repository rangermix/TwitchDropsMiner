"""
Channel service for managing channel discovery, online status checks, and priority sorting.

This service handles all channel-related operations including fetching live streams
from directories, bulk online status verification, and channel priority determination.
"""

from __future__ import annotations

import asyncio
import logging
from collections import abc
from time import time
from typing import TYPE_CHECKING

import aiohttp

from src.config import GQL_OPERATIONS, MAX_INT
from src.exceptions import ExitRequest, GQLException, MinerException
from src.models.channel import Channel
from src.utils import chunk


if TYPE_CHECKING:
    from src.config import GQLRequest, JsonType
    from src.core.client import Twitch
    from src.models.game import Game


logger = logging.getLogger("TwitchDrops")


class ChannelService:
    """
    Service responsible for channel management operations.

    Handles:
    - Channel priority calculation
    - Live stream discovery from game directories
    - Bulk online status checks for ACL channels
    - Channel sorting by viewers and priority
    """

    def __init__(self, twitch: Twitch) -> None:
        """
        Initialize the channel service.

        Args:
            twitch: The Twitch client instance
        """
        self._twitch = twitch
        self._banned_channels: dict[int, Channel] = {}
        self._ban_refresh: asyncio.TimerHandle | None = None

    @property
    def banned_channels(self) -> list[Channel]:
        return [channel for channel in self._banned_channels.values() if channel.banned]

    def clear_bans(self) -> None:
        """Release account-owned ban state and its expiry callback."""
        if self._ban_refresh is not None:
            self._ban_refresh.cancel()
            self._ban_refresh = None
        self._banned_channels.clear()

    def _schedule_ban_refresh(self) -> None:
        if self._ban_refresh is not None:
            self._ban_refresh.cancel()
            self._ban_refresh = None
        expiries = [channel.ban_expires_at for channel in self.banned_channels
                    if channel.ban_expires_at is not None]
        if expiries:
            self._ban_refresh = asyncio.get_running_loop().call_later(
                max(0, min(expiries) - time()), self._ban_expired,
            )

    def _ban_expired(self) -> None:
        self._ban_refresh = None
        # Queue discovery through the existing safe state-machine transition.
        self._twitch.request_games_update()
        self._schedule_ban_refresh()

    def get_priority(self, channel: Channel) -> int:
        """
        Return a priority number for a given channel based on games_to_watch order.

        Priority is determined by the position of the channel's game in the
        wanted_games list. Lower numbers indicate higher priority.

        Args:
            channel: The channel to evaluate

        Returns:
            Priority number where:
            - 0 has the highest priority (first in games_to_watch list)
            - Higher numbers indicate lower priority
            - MAX_INT signifies the lowest possible priority (unwanted game or offline)
        """
        if (
            (game := channel.game) is None  # None when OFFLINE or no game set
            or game not in self._twitch.wanted_games  # we don't care about the played game
        ):
            return MAX_INT
        return self._twitch.wanted_games.index(game)

    @staticmethod
    def get_viewers_key(channel: Channel) -> int:
        """
        Sort key for channels by viewer count (descending).

        Args:
            channel: The channel to evaluate

        Returns:
            Viewer count, or -1 if not available (offline channels)
        """
        if (viewers := channel.viewers) is not None:
            return viewers
        return -1

    async def get_live_streams(
        self, game: Game, *, limit: int = 20, drops_enabled: bool = True
    ) -> list[Channel]:
        """
        Fetch live streams for a specific game from Twitch directory.

        Args:
            game: The game to fetch streams for
            limit: Maximum number of streams to return (default: 20)
            drops_enabled: Only return channels with drops enabled (default: True)

        Returns:
            List of Channel objects representing live streams

        Raises:
            MinerException: If the GQL request fails
        """
        filters: list[str] = []
        if drops_enabled:
            filters.append("DROPS_ENABLED")

        try:
            response = await self._twitch.gql_request(
                GQL_OPERATIONS["GameDirectory"].with_variables(
                    {
                        "limit": limit,
                        "slug": game.slug,
                        "options": {
                            "includeRestricted": ["SUB_ONLY_LIVE"],
                            "systemFilters": filters,
                        },
                    }
                )
            )
        except GQLException as exc:
            raise MinerException(f"Game: {game.slug}") from exc

        if "game" in response["data"]:
            return [
                Channel.from_directory(
                    self._twitch, stream_channel_data["node"], drops_enabled=drops_enabled
                )
                for stream_channel_data in response["data"]["game"]["streams"]["edges"]
                if stream_channel_data["node"]["broadcaster"] is not None
            ]
        return []

    async def filter_banned_channels(
        self, channels: abc.Iterable[Channel], *, retained_channels: abc.Iterable[Channel] = (),
    ) -> list[Channel]:
        """Filter discovery candidates and synchronize retained same-account objects."""
        channel_list = list(channels)
        by_id: dict[str, list[Channel]] = {}
        for channel in (*channel_list, *retained_channels, *self.banned_channels):
            by_id.setdefault(str(channel.id), []).append(channel)
        if not by_id:
            return []
        # Reload can retain watched/manual objects after clearing the tracked list.
        # A failed refresh must not lose a previously confirmed same-account ban.
        for instances in by_id.values():
            if (known := next((channel for channel in instances if channel.banned), None)):
                for channel in instances:
                    channel.inherit_ban(known)
        unique_channels = [instances[0] for instances in by_id.values()]
        tasks = [
            asyncio.create_task(asyncio.wait_for(
                self._twitch.gql_request([channel.ban_gql for channel in batch]), timeout=10,
            ))
            for batch in chunk(unique_channels, 20)
        ]
        try:
            for task in asyncio.as_completed(tasks):
                try:
                    response = await task
                except ExitRequest:
                    raise
                except (MinerException, aiohttp.ClientError, TimeoutError,
                        KeyError, TypeError, IndexError, ValueError):
                    # GQL/JSON normalization can fail before malformed optional
                    # responses reach us. Never expose their bodies or exceptions.
                    continue
                rows = response if isinstance(response, list) else [response]
                for row in rows:
                    if not isinstance(row, dict):
                        continue
                    data = row.get("data")
                    user = data.get("user") if isinstance(data, dict) else None
                    if isinstance(user, dict) and isinstance(user.get("id"), str):
                        for channel in by_id.get(user["id"], ()):
                            channel.update_ban_status(row)
        finally:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
        self._banned_channels = {
            instances[0].id: instances[0] for instances in by_id.values() if instances[0].banned
        }
        self._schedule_ban_refresh()
        return [channel for channel in channel_list if not channel.banned]

    async def bulk_check_online(self, channels: abc.Iterable[Channel]) -> None:
        """
        Utilize batch GQL requests to check ONLINE status for multiple channels at once.

        This method efficiently checks the online status and drops_enabled flag
        for a large number of channels by batching GraphQL requests.

        Args:
            channels: Iterable of Channel objects to check
        """
        channel_list = list(channels)
        acl_streams_map: dict[int, JsonType] = {}
        stream_gql_ops: list[GQLRequest] = [channel.stream_gql for channel in channel_list]

        if not stream_gql_ops:
            # shortcut for nothing to process
            # NOTE: Have to do this here, because "channels" can be any iterable
            return

        # gql_request may return either a single JsonType or a list[JsonType],
        # so accept the union in the Task type.
        stream_gql_tasks: list[asyncio.Task[JsonType | list[JsonType]]] = [
            asyncio.create_task(self._twitch.gql_request(stream_gql_chunk))
            for stream_gql_chunk in chunk(stream_gql_ops, 20)
        ]

        try:
            for coro in asyncio.as_completed(stream_gql_tasks):
                response = await coro
                # Normalize response to a list for uniform processing
                if isinstance(response, list):
                    response_list: list[JsonType] = response
                else:
                    response_list = [response]
                for response_json in response_list:
                    channel_data: JsonType = response_json["data"]["user"]
                    if channel_data is not None:
                        acl_streams_map[int(channel_data["id"])] = channel_data
        finally:
            for task in stream_gql_tasks:
                task.cancel()
            await asyncio.gather(*stream_gql_tasks, return_exceptions=True)

        # Update all channels with their stream data
        for channel in channel_list:
            channel_id = channel.id
            if channel_id not in acl_streams_map:
                continue
            channel_data = acl_streams_map[channel_id]
            if channel_data["stream"] is None:
                continue
            # Update channel with stream data (no available drops check)
            channel.external_update(channel_data, [])
