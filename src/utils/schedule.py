"""Active-hours window helpers for the mining schedule setting."""

from __future__ import annotations

from datetime import datetime


MINUTES_PER_DAY = 24 * 60


def parse_minutes(value: object, default: int = 0) -> int:
    """Parse ``"HH:MM"`` into minutes since midnight, clamping into the day.

    Args:
        value: String in ``HH:MM`` 24-hour format expected from the settings.
        default: Value to fall back on when ``value`` cannot be parsed.

    Returns:
        Minutes since midnight (``0..1439``).
    """
    try:
        hours, minutes = str(value).split(":")
        total = int(hours) * 60 + int(minutes)
    except (ValueError, TypeError):
        return default
    return max(0, min(total, MINUTES_PER_DAY - 1))


def window_status(start: str, end: str, now: datetime) -> tuple[bool, float]:
    """Evaluate an active-hours window against the current local time.

    Args:
        start: Window start as ``"HH:MM"`` (inclusive).
        end: Window end as ``"HH:MM"`` (exclusive).
        now: The current local time.

    Returns:
        A tuple of ``(in_window, seconds_until_next_boundary)`` where the
        boundary is the window closing edge while ``in_window`` is ``True`` and
        the opening edge otherwise. A zero-length range is treated as always
        active and re-polls once per hour.
    """
    start_min = parse_minutes(start)
    end_min = parse_minutes(end)
    now_min = now.hour * 60 + now.minute + now.second / 60.0

    if start_min == end_min:
        return True, 3600.0

    if start_min < end_min:
        # same-day window: [start, end)
        if start_min <= now_min < end_min:
            return True, (end_min - now_min) * 60.0
        if now_min < start_min:
            return False, (start_min - now_min) * 60.0
        return False, (start_min + MINUTES_PER_DAY - now_min) * 60.0

    # overnight window that wraps midnight (e.g. 22:00 -> 06:00)
    if now_min >= start_min or now_min < end_min:
        if now_min < end_min:
            return True, (end_min - now_min) * 60.0
        return True, (end_min + MINUTES_PER_DAY - now_min) * 60.0
    return False, (start_min - now_min) * 60.0
