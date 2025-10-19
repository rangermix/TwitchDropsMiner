"""String manipulation utility functions."""

from __future__ import annotations

import random
import string
from collections import OrderedDict, abc
from typing import TypeVar


# Character sets for nonce generation
CHARS_ASCII = string.ascii_letters + string.digits
CHARS_HEX_LOWER = string.digits + "abcdef"
CHARS_HEX_UPPER = string.digits + "ABCDEF"

_T = TypeVar("_T")


def create_nonce(chars: str, length: int) -> str:
    """Generate a random nonce string of specified length from given characters."""
    return "".join(random.choices(chars, k=length))


def chunk(to_chunk: abc.Iterable[_T], chunk_length: int) -> abc.Generator[list[_T], None, None]:
    """Split an iterable into chunks of a specified length."""
    list_to_chunk: list[_T] = list(to_chunk)
    for i in range(0, len(list_to_chunk), chunk_length):
        yield list_to_chunk[i : i + chunk_length]


def deduplicate(iterable: abc.Iterable[_T]) -> list[_T]:
    """Remove duplicates from an iterable while preserving order."""
    return list(OrderedDict.fromkeys(iterable).keys())
