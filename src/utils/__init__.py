"""Utility modules for TwitchDropsMiner."""

from __future__ import annotations

# String utilities
from .string_utils import (
    CHARS_ASCII,
    CHARS_HEX_LOWER,
    CHARS_HEX_UPPER,
    create_nonce,
    chunk,
    deduplicate,
)

# JSON utilities
from .json_utils import (
    json_minify,
    json_load,
    json_save,
    merge_json,
    SERIALIZE_ENV,
)

# Async helpers
from .async_helpers import (
    first_to_complete,
    format_traceback,
    task_wrapper,
    invalidate_cache,
    AwaitableValue,
)

# Rate limiting
from .rate_limiter import RateLimiter

# Backoff
from .backoff import ExponentialBackoff


__all__ = [
    # String utilities
    "CHARS_ASCII",
    "CHARS_HEX_LOWER",
    "CHARS_HEX_UPPER",
    "create_nonce",
    "chunk",
    "deduplicate",
    # JSON utilities
    "json_minify",
    "json_load",
    "json_save",
    "merge_json",
    "SERIALIZE_ENV",
    # Async helpers
    "first_to_complete",
    "format_traceback",
    "task_wrapper",
    "invalidate_cache",
    "AwaitableValue",
    # Rate limiting
    "RateLimiter",
    # Backoff
    "ExponentialBackoff",
]
