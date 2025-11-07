"""Configuration package for Twitch Drops Miner."""

from __future__ import annotations

from .client_info import ClientInfo, ClientType

# Re-export all public symbols for convenience
from .constants import (
    BASE_TOPICS,
    CALL,
    DEFAULT_LANG,
    FILE_FORMATTER,
    LOGGING_LEVELS,
    MAX_CHANNELS,
    MAX_EXTRA_MINUTES,
    MAX_INT,
    MAX_TOPICS,
    MAX_WEBSOCKETS,
    ONLINE_DELAY,
    PING_INTERVAL,
    PING_TIMEOUT,
    TOPICS_PER_CHANNEL,
    WATCH_INTERVAL,
    WEBSOCKET_TOPICS,
    WS_TOPICS_LIMIT,
    GQLOperation,
    JsonType,
    State,
    TopicProcess,
    URLType,
    WebsocketTopic,
)
from .operations import GQL_OPERATIONS
from .paths import (
    COOKIES_PATH,
    DATA_DIR,
    LANG_PATH,
    SETTINGS_PATH,
    _merge_vars,
)


__all__ = [
    # constants.py
    "CALL",
    "FILE_FORMATTER",
    "LOGGING_LEVELS",
    "State",
    "WebsocketTopic",
    "WEBSOCKET_TOPICS",
    "JsonType",
    "URLType",
    "TopicProcess",
    "GQLOperation",
    "MAX_INT",
    "MAX_EXTRA_MINUTES",
    "BASE_TOPICS",
    "MAX_WEBSOCKETS",
    "WS_TOPICS_LIMIT",
    "TOPICS_PER_CHANNEL",
    "MAX_TOPICS",
    "MAX_CHANNELS",
    "DEFAULT_LANG",
    "PING_INTERVAL",
    "PING_TIMEOUT",
    "ONLINE_DELAY",
    "WATCH_INTERVAL",
    # paths.py
    "DATA_DIR",
    "LANG_PATH",
    "COOKIES_PATH",
    "SETTINGS_PATH",
    "_merge_vars",
    # client_info.py
    "ClientInfo",
    "ClientType",
    # operations.py
    "GQL_OPERATIONS",
]
