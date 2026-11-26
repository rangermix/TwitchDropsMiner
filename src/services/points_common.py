"""Helpers shared by the channel points actions of idle watching: bounded id stores and mutation replies."""

from __future__ import annotations

from typing import Any


# event ids kept for "once per event" and for the results of placed bets, oldest dropped first
EVENTS_KEPT = 200


def remember(store: dict[str, Any], key: str, value: Any = None) -> None:
    """Add key to an insertion-ordered store and keep only the newest EVENTS_KEPT."""
    store[key] = value
    while len(store) > EVENTS_KEPT:
        del store[next(iter(store))]


def error_code(response: object, field: str) -> str | None:
    """None when the reply's data.<field> is an object whose error is null or missing, else an error code.

    Any other error value (also an empty object) is a failure: its non-empty string code, else "unknown".
    """
    try:
        result = response["data"][field]  # type: ignore[index]
    except (KeyError, TypeError):
        return "unknown"
    error = result.get("error") if isinstance(result, dict) else True
    if error is None:
        return None
    code = error.get("code") if isinstance(error, dict) else None
    return code if isinstance(code, str) and code else "unknown"
