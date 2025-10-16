"""Image cache for web interface (minimal implementation)."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from src.web.gui_manager import WebGUIManager


class ImageCache:
    """Minimal image cache for web mode.

    In the web interface, campaign images are referenced by their URLs directly
    rather than being cached locally. This class maintains API compatibility
    with desktop GUI implementations while simply passing through URLs.
    """

    def __init__(self, manager: WebGUIManager):
        self._manager = manager

    async def get(self, url: str) -> str:
        """Get image URL (returns the URL directly in web mode).

        Args:
            url: The image URL to retrieve

        Returns:
            The same URL (no caching needed for web display)
        """
        # In web mode, we just return the URL
        return url
