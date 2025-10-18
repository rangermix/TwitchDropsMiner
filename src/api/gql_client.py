"""
GraphQL client for Twitch GQL API.

Handles GraphQL requests with rate limiting, error handling, and retry logic.
"""

from __future__ import annotations

import asyncio
import logging
from itertools import chain
from typing import TYPE_CHECKING, overload

from src.exceptions import GQLException, MinerException
from src.utils import ExponentialBackoff, RateLimiter


if TYPE_CHECKING:
    from src.api.http_client import HTTPClient
    from src.auth import _AuthState
    from src.config import ClientInfo, GQLOperation, JsonType


logger = logging.getLogger("TwitchDrops")
gql_logger = logging.getLogger("TwitchDrops.gql")


class GQLClient:
    """
    GraphQL client for Twitch GQL API.

    This client provides:
    - Rate-limited GraphQL requests to prevent API bans
    - Automatic retry with exponential backoff on errors
    - Error handling for various GQL error types
    - Support for batched requests
    - Data merging utilities for campaign data
    """

    def __init__(
        self,
        http_client: HTTPClient,
        auth_state: _AuthState,
        client_type: ClientInfo,
    ):
        """
        Initialize the GraphQL client.

        Parameters
        ----------
        http_client : HTTPClient
            The HTTP client for making requests
        auth_state : _AuthState
            Authentication state manager
        client_type : ClientInfo
            Client type information (User-Agent, Client-ID, etc.)
        """
        self.http_client = http_client
        self._auth_state = auth_state
        self._client_type = client_type
        # NOTE: GQL is volatile and breaks everything if rate limited.
        # Do not modify these safe defaults.
        self._qgl_limiter = RateLimiter(capacity=5, window=1)

    @overload
    async def request(self, ops: GQLOperation) -> JsonType:
        ...

    @overload
    async def request(self, ops: list[GQLOperation]) -> list[JsonType]:
        ...

    async def request(
        self, ops: GQLOperation | list[GQLOperation]
    ) -> JsonType | list[JsonType]:
        """
        Execute one or more GraphQL operations.

        Parameters
        ----------
        ops : GQLOperation | list[GQLOperation]
            Single operation or list of operations to execute

        Returns
        -------
        JsonType | list[JsonType]
            Response data for the operation(s)

        Raises
        ------
        GQLException
            If the GQL API returns an error that can't be handled
        RuntimeError
            If the retry loop is broken unexpectedly
        """
        gql_logger.debug(f"GQL Request: {ops}")
        backoff = ExponentialBackoff(maximum=60)
        # Flag to retry the request once for specific errors
        single_retry: bool = True

        for delay in backoff:
            async with self._qgl_limiter:
                auth_state = await self._auth_state.validate()
                async with self.http_client.request(
                    "POST",
                    "https://gql.twitch.tv/gql",
                    json=ops,
                    headers=auth_state.headers(
                        user_agent=self._client_type.USER_AGENT, gql=True
                    ),
                ) as response:
                    response_json: JsonType | list[JsonType] = await response.json()

            gql_logger.debug(f"GQL Response: {response_json}")
            orig_response = response_json

            # Normalize to list for unified error handling
            response_list = response_json if isinstance(response_json, list) else [response_json]

            force_retry: bool = False
            for response_json in response_list:
                # GQL error handling
                if "errors" in response_json:
                    for error_dict in response_json["errors"]:
                        if "message" in error_dict:
                            if (
                                single_retry
                                and error_dict["message"]
                                in (
                                    "service error",
                                    "PersistedQueryNotFound",
                                )
                            ):
                                logger.error(
                                    f"Retrying a {error_dict['message']} for "
                                    f"{response_json['extensions']['operationName']}"
                                )
                                single_retry = False
                                if delay < 5:
                                    # Overwrite delay if too short
                                    delay = 5
                                force_retry = True
                                break
                            elif error_dict["message"] == "server error":
                                # Nullify the key the error path points to
                                data_dict: JsonType = response_json["data"]
                                path: list[str] = error_dict.get("path", [])
                                for key in path[:-1]:
                                    data_dict = data_dict[key]
                                data_dict[path[-1]] = None
                                break
                            elif error_dict["message"] in (
                                "service timeout",
                                "service unavailable",
                                "context deadline exceeded",
                            ):
                                force_retry = True
                                break
                    else:
                        raise GQLException(response_json["errors"])
                # Other error handling
                elif "error" in response_json:
                    raise GQLException(
                        f"{response_json['error']}: {response_json['message']}"
                    )

                if force_retry:
                    break
            else:
                return orig_response

            await asyncio.sleep(delay)

        raise RuntimeError("Retry loop was broken")

    @staticmethod
    def merge_data(primary_data: JsonType, secondary_data: JsonType) -> JsonType:
        """
        Recursively merge two JSON objects, preferring primary data.

        This is used to merge campaign data from inventory and general campaigns endpoints.

        Parameters
        ----------
        primary_data : JsonType
            Primary data source (takes precedence)
        secondary_data : JsonType
            Secondary data source (used when key missing in primary)

        Returns
        -------
        JsonType
            Merged data dictionary

        Raises
        ------
        MinerException
            If data types are inconsistent between sources
        """
        merged = {}
        for key in set(chain(primary_data.keys(), secondary_data.keys())):
            in_primary = key in primary_data
            if in_primary and key in secondary_data:
                vp = primary_data[key]
                vs = secondary_data[key]
                if not isinstance(vp, type(vs)) or not isinstance(vs, type(vp)):
                    raise MinerException("Inconsistent merge data")
                if isinstance(vp, dict):  # Both are dicts
                    merged[key] = GQLClient.merge_data(vp, vs)
                else:
                    # Use primary value
                    merged[key] = vp
            elif in_primary:
                merged[key] = primary_data[key]
            else:  # In secondary only
                merged[key] = secondary_data[key]
        return merged
