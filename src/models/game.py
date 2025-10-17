from __future__ import annotations

import re
from functools import cached_property
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from src.config.constants import JsonType


class Game:
    """Represents a Twitch game/category."""

    def __init__(self, data: JsonType):
        self.id: int = int(data["id"])
        self.name: str = data.get("displayName") or data["name"]
        if "slug" in data:
            self.slug = data["slug"]
        # Store box art URL if available (used for game icons in UI)
        self.box_art_url: str | None = data.get("boxArtURL")

    def __str__(self) -> str:
        return self.name

    def __repr__(self) -> str:
        return f"Game({self.id}, {self.name})"

    def __eq__(self, other: object) -> bool:
        if isinstance(other, self.__class__):
            return self.id == other.id
        return NotImplemented

    def __hash__(self) -> int:
        return self.id

    @cached_property
    def slug(self) -> str:
        """
        Converts the game name into a slug, useable for the GQL API.
        """
        # remove specific characters
        slug_text = re.sub(r'\'', '', self.name.lower())
        # remove non alpha-numeric characters
        slug_text = re.sub(r'\W+', '-', slug_text)
        # strip and collapse dashes
        slug_text = re.sub(r'-{2,}', '-', slug_text.strip('-'))
        return slug_text
