"""Temporary game order chosen on the dashboard queue; never persisted."""

from __future__ import annotations

from typing import Any


class TemporaryGameOrder:
    """Rank games by a manual, runtime-only order ahead of the saved Mining list.

    The saved Mining list stays the permanent default. This order only reorders games
    that are already wanted; it never adds or removes one, and a restart clears it.
    """

    MAX_GAMES = 200
    MAX_NAME_LENGTH = 200

    def __init__(self) -> None:
        self._names: list[str] = []
        self._ranks: dict[str, int] = {}

    @property
    def active(self) -> bool:
        return bool(self._names)

    @property
    def names(self) -> list[str]:
        return list(self._names)

    @classmethod
    def normalize(cls, value: Any) -> list[str]:
        """Strip, drop blanks and case-insensitive duplicates; reject anything else."""
        if not isinstance(value, list) or len(value) > cls.MAX_GAMES:
            raise ValueError("invalid_game_order")
        names: list[str] = []
        seen: set[str] = set()
        for item in value:
            if not isinstance(item, str):
                raise ValueError("invalid_game_order")
            name = item.strip()
            if len(name) > cls.MAX_NAME_LENGTH:
                raise ValueError("invalid_game_order")
            if name and name.casefold() not in seen:
                seen.add(name.casefold())
                names.append(name)
        return names

    def set(self, value: Any) -> None:
        names = self.normalize(value)
        self._names = names
        self._ranks = {name.casefold(): index for index, name in enumerate(names)}

    def clear(self) -> bool:
        """Clear the order; return whether anything changed."""
        changed = self.active
        self.set([])
        return changed

    def rank(self, game_name: str | None) -> int:
        """Listed games rank by position; every other game ranks after them."""
        if game_name is None:
            return len(self._names)
        return self._ranks.get(game_name.casefold(), len(self._names))

    def info(self) -> dict[str, Any]:
        return {"active": self.active, "games": self.names}
