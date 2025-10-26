"""Core constants, enums, and type definitions for Twitch Drops Miner."""

from __future__ import annotations

import logging
import sys
from copy import deepcopy
from datetime import timedelta
from enum import Enum, auto
from typing import TYPE_CHECKING, Any, Literal, NewType

if TYPE_CHECKING:
    from collections import abc  # noqa
    from typing import TypeAlias


# Logging special levels
CALL: int = logging.INFO - 1
logging.addLevelName(CALL, "CALL")

# Logging configuration
LOGGING_LEVELS = {
    0: logging.ERROR,
    1: logging.WARNING,
    2: logging.INFO,
    3: CALL,
    4: logging.DEBUG,
}
FILE_FORMATTER = logging.Formatter(
    "{asctime}.{msecs:03.0f}:\t{levelname:>7}:\t{filename}:{lineno}:\t{message}",
    style="{",
    datefmt="%Y-%m-%d %H:%M:%S",
)
OUTPUT_FORMATTER = logging.Formatter("{levelname}: {message}", style="{", datefmt="%H:%M:%S")

# Type aliases
JsonType = dict[str, Any]
URLType = NewType("URLType", str)
TopicProcess: TypeAlias = "abc.Callable[[int, JsonType], Any]"

# Core constants
MAX_INT = sys.maxsize
MAX_EXTRA_MINUTES = 15
BASE_TOPICS = 2
MAX_WEBSOCKETS = 8
WS_TOPICS_LIMIT = 50
TOPICS_PER_CHANNEL = 2
MAX_TOPICS = (MAX_WEBSOCKETS * WS_TOPICS_LIMIT) - BASE_TOPICS
MAX_CHANNELS = MAX_TOPICS // TOPICS_PER_CHANNEL

# Misc
DEFAULT_LANG = "English"

# Intervals and Delays
PING_INTERVAL = timedelta(minutes=3)
PING_TIMEOUT = timedelta(seconds=10)
ONLINE_DELAY = timedelta(seconds=120)
WATCH_INTERVAL = timedelta(seconds=59)



class State(Enum):
    """Application state machine states."""

    IDLE = auto()
    INVENTORY_FETCH = auto()
    GAMES_UPDATE = auto()
    CHANNELS_FETCH = auto()
    CHANNELS_CLEANUP = auto()
    CHANNEL_SWITCH = auto()
    EXIT = auto()


class GQLOperation(JsonType):
    """GraphQL operation with persisted query hash."""

    def __init__(self, name: str, sha256: str, *, variables: JsonType | None = None):
        super().__init__(
            operationName=name,
            extensions={
                "persistedQuery": {
                    "version": 1,
                    "sha256Hash": sha256,
                }
            },
        )
        if variables is not None:
            self.__setitem__("variables", variables)

    def with_variables(self, variables: JsonType) -> GQLOperation:
        """Create a copy with merged variables."""
        from .paths import _merge_vars

        modified = deepcopy(self)
        if "variables" in self:
            existing_variables: JsonType = modified["variables"]
            _merge_vars(existing_variables, variables)
        else:
            modified["variables"] = variables
        return modified


class WebsocketTopic:
    """Represents a websocket topic subscription."""

    def __init__(
        self,
        category: Literal["User", "Channel"],
        topic_name: str,
        target_id: int,
        process: TopicProcess,
    ):
        assert isinstance(target_id, int)
        self._id: str = self.as_str(category, topic_name, target_id)
        self._target_id = target_id
        self._process: TopicProcess = process

    @classmethod
    def as_str(cls, category: Literal["User", "Channel"], topic_name: str, target_id: int) -> str:
        return f"{WEBSOCKET_TOPICS[category][topic_name]}.{target_id}"

    def __call__(self, message: JsonType):
        return self._process(self._target_id, message)

    def __str__(self) -> str:
        return self._id

    def __repr__(self) -> str:
        return f"Topic({self._id})"

    def __eq__(self, other) -> bool:
        if isinstance(other, WebsocketTopic):
            return self._id == other._id
        elif isinstance(other, str):
            return self._id == other
        return NotImplemented

    def __hash__(self) -> int:
        return hash((self.__class__.__name__, self._id))


WEBSOCKET_TOPICS: dict[str, dict[str, str]] = {
    "User": {  # Using user_id
        "Presence": "presence",  # unused
        "Drops": "user-drop-events",
        "Notifications": "onsite-notifications",
        "CommunityPoints": "community-points-user-v1",
    },
    "Channel": {  # Using channel_id
        "Drops": "channel-drop-events",  # unused
        "StreamState": "video-playback-by-id",
        "StreamUpdate": "broadcast-settings-update",
        "CommunityPoints": "community-points-channel-v1",  # unused
    },
}
