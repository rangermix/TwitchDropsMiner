"""Async programming utilities and helpers."""

from __future__ import annotations

import asyncio
import logging
import traceback
from collections import abc
from contextlib import suppress
from functools import wraps
from typing import Any, Generic, Literal, ParamSpec, TypeVar

from src.exceptions import ExitRequest


_T = TypeVar("_T")  # type
_D = TypeVar("_D")  # default
_P = ParamSpec("_P")  # params

logger = logging.getLogger("TwitchDrops")


async def first_to_complete(coros: abc.Iterable[abc.Coroutine[Any, Any, _T]]) -> _T:
    """Wait for the first coroutine to complete, canceling the rest."""
    # In Python 3.11, we need to explicitly wrap awaitables
    tasks: list[asyncio.Task[_T]] = [asyncio.ensure_future(coro) for coro in coros]
    done: set[asyncio.Task[Any]]
    pending: set[asyncio.Task[Any]]
    done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
    for task in pending:
        task.cancel()
    return await next(iter(done))


def format_traceback(exc: BaseException, **kwargs: Any) -> str:
    """
    Like `traceback.print_exc` but returns a string. Uses the passed-in exception.
    Any additional `**kwargs` are passed to the underlaying `traceback.format_exception`.
    """
    return "".join(traceback.format_exception(type(exc), exc, exc.__traceback__, **kwargs))


def task_wrapper(
    afunc: abc.Callable[_P, abc.Coroutine[Any, Any, _T]] | None = None, *, critical: bool = False
):
    """
    Decorator for async tasks that handles exceptions gracefully.

    Args:
        afunc: The async function to wrap
        critical: If True, a critical task failure will trigger application termination

    Handles ExitRequest silently, logs other exceptions.
    Critical tasks will attempt to find and close the Twitch instance on failure.
    """

    def decorator(
        afunc: abc.Callable[_P, abc.Coroutine[Any, Any, _T]],
    ) -> abc.Callable[_P, abc.Coroutine[Any, Any, _T]]:
        @wraps(afunc)
        async def wrapper(*args: _P.args, **kwargs: _P.kwargs):
            try:
                await afunc(*args, **kwargs)
            except ExitRequest:
                pass
            except Exception:
                logger.exception(f"Exception in {afunc.__name__} task")
                if critical:
                    # critical task's death should trigger a termination.
                    # there isn't an easy and sure way to obtain the Twitch instance here,
                    # but we can improvise finding it
                    from src.core.client import Twitch  # cyclic import

                    probe = args and args[0] or None  # extract from 'self' arg
                    if isinstance(probe, Twitch):
                        probe.close()
                    elif probe is not None:
                        probe = getattr(probe, "_twitch", None)  # extract from '_twitch' attr
                        if isinstance(probe, Twitch):
                            probe.close()
                raise  # raise up to the wrapping task

        return wrapper

    if afunc is None:
        return decorator
    return decorator(afunc)


def invalidate_cache(instance: object, *attrnames: str) -> None:
    """
    Invalidate cached_property attributes on an instance.
    Used to clear functools.cached_property values.
    """
    for name in attrnames:
        with suppress(AttributeError):
            delattr(instance, name)


class AwaitableValue(Generic[_T]):
    """
    A value that can be set once and awaited by multiple consumers.

    Provides async/await interface for waiting on a value to become available.
    Useful for coordination between async tasks.
    """

    def __init__(self):
        self._value: _T
        self._event = asyncio.Event()

    def has_value(self) -> bool:
        """Check if the value has been set."""
        return self._event.is_set()

    def wait(self) -> abc.Coroutine[Any, Any, Literal[True]]:
        """Return a coroutine that waits for the value to be set."""
        return self._event.wait()

    def get_with_default(self, default: _D) -> _T | _D:
        """Get the value if set, otherwise return the default."""
        if self._event.is_set():
            return self._value
        return default

    async def get(self) -> _T:
        """Wait for and return the value."""
        await self._event.wait()
        return self._value

    def set(self, value: _T) -> None:
        """Set the value and notify all waiters."""
        self._value = value
        self._event.set()

    def clear(self) -> None:
        """Clear the value, allowing it to be set again."""
        self._event.clear()
