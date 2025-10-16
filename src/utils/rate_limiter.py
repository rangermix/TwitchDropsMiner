"""Rate limiting utilities for API request management."""

from __future__ import annotations

import asyncio


class RateLimiter:
    """
    Async context manager for rate limiting operations.

    Enforces a maximum number of operations within a sliding time window.
    Tracks both total operations in the window and concurrent operations.

    Usage:
        limiter = RateLimiter(capacity=10, window=60)
        async with limiter:
            # perform rate-limited operation
            pass
    """

    def __init__(self, *, capacity: int, window: int):
        """
        Initialize rate limiter.

        Args:
            capacity: Maximum number of operations allowed
            window: Time window in seconds
        """
        self.total: int = 0
        self.concurrent: int = 0
        self.window: int = window
        self.capacity: int = capacity
        self._reset_task: asyncio.Task[None] | None = None
        self._cond: asyncio.Condition = asyncio.Condition()

    def __repr__(self) -> str:
        return f"{self.__class__.__name__}({self.concurrent}/{self.total}/{self.capacity})"

    def __del__(self) -> None:
        if self._reset_task is not None:
            self._reset_task.cancel()

    def _can_proceed(self) -> bool:
        """Check if an operation can proceed based on current limits."""
        return max(self.total, self.concurrent) < self.capacity

    async def __aenter__(self):
        """Enter context - wait if rate limit is reached."""
        async with self._cond:
            await self._cond.wait_for(self._can_proceed)
            self.total += 1
            self.concurrent += 1
            if self._reset_task is None:
                self._reset_task = asyncio.create_task(self._rtask())

    async def __aexit__(self, exc_type, exc, tb):
        """Exit context - decrement concurrent counter and notify waiters."""
        self.concurrent -= 1
        async with self._cond:
            self._cond.notify(self.capacity - self.concurrent)

    async def _reset(self) -> None:
        """Reset the total counter after the window expires."""
        if self._reset_task is not None:
            self._reset_task = None
        async with self._cond:
            self.total = 0
            if self.concurrent < self.capacity:
                self._cond.notify(self.capacity - self.concurrent)

    async def _rtask(self) -> None:
        """Background task that resets counters after the time window."""
        await asyncio.sleep(self.window)
        await self._reset()
