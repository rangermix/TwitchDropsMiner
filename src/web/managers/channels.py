"""Channel list manager for tracking and displaying available channels."""

from __future__ import annotations

import asyncio
from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    from src.web.managers.broadcaster import WebSocketBroadcaster
    from src.models.channel import Channel


class ChannelListManager:
    """Manages the list of available channels in the web interface.

    Tracks all discovered channels with their online status, game, viewers,
    and drop eligibility. Broadcasts real-time updates when channels change
    or when the watched channel switches.
    """

    def __init__(self, broadcaster: WebSocketBroadcaster, gui_manager=None):
        self._broadcaster = broadcaster
        self._channels: dict[int, dict[str, Any]] = {}
        self._watching_id: int | None = None
        self._selected_id: int | None = None
        self._gui_manager = gui_manager

    def display(self, channel: Channel, *, add: bool = False):
        """Add or update a channel in the display list.

        Args:
            channel: The channel to display
            add: If True, emit channel_add event; otherwise emit channel_update
        """
        channel_data = {
            "id": channel.id,
            "name": channel.name,
            "game": channel.game.name if channel.game else None,
            "game_id": channel.game.id if channel.game else None,
            "game_icon": channel.game.box_art_url if channel.game else None,
            "viewers": channel.viewers,
            "online": channel.online,
            "drops_enabled": channel.drops_enabled,
            "acl_based": channel.acl_based,
            "watching": channel.id == self._watching_id
        }
        self._channels[channel.id] = channel_data
        asyncio.create_task(
            self._broadcaster.emit("channel_update" if not add else "channel_add", channel_data)
        )

    def remove(self, channel: Channel):
        """Remove a channel from the display list.

        Args:
            channel: The channel to remove
        """
        if channel.id in self._channels:
            del self._channels[channel.id]
            asyncio.create_task(
                self._broadcaster.emit("channel_remove", {"id": channel.id})
            )

    def clear(self):
        """Clear all channels from the display list."""
        self._channels.clear()
        asyncio.create_task(
            self._broadcaster.emit("channels_clear", {})
        )

    def set_watching(self, channel: Channel):
        """Mark a channel as currently being watched.

        Args:
            channel: The channel now being watched
        """
        self._watching_id = channel.id
        asyncio.create_task(
            self._broadcaster.emit("channel_watching", {"id": channel.id})
        )

    def clear_watching(self):
        """Clear the currently watched channel indicator."""
        self._watching_id = None
        asyncio.create_task(
            self._broadcaster.emit("channel_watching_clear", {})
        )

    def get_selection(self) -> Channel | None:
        """Get user's channel selection from web GUI.

        Returns:
            The selected Channel if one exists, None otherwise
        """
        if self._gui_manager is None:
            return None

        # Get the selected channel ID from the parent GUI manager
        selected_id = self._gui_manager.get_selected_channel_id()
        if selected_id is None:
            return None

        # Get the Channel object from the Twitch client
        from src.core.client import Twitch
        if hasattr(self._gui_manager, '_twitch'):
            twitch: Twitch = self._gui_manager._twitch
            return twitch.channels.get(selected_id)

        return None

    def batch_update(self, channels: list[Channel]):
        """Replace all channels atomically with a new list.

        This prevents UI flicker by updating all channels in one operation
        instead of clearing and gradually re-adding them.

        Args:
            channels: List of channels to display
        """
        # Build new channels dict
        new_channels = {}
        channels_data = []

        for channel in channels:
            channel_data = {
                "id": channel.id,
                "name": channel.name,
                "game": channel.game.name if channel.game else None,
                "game_id": channel.game.id if channel.game else None,
                "game_icon": channel.game.box_art_url if channel.game else None,
                "viewers": channel.viewers,
                "online": channel.online,
                "drops_enabled": channel.drops_enabled,
                "acl_based": channel.acl_based,
                "watching": channel.id == self._watching_id
            }
            new_channels[channel.id] = channel_data
            channels_data.append(channel_data)

        # Atomically replace all channels
        self._channels = new_channels

        # Emit batch update event
        asyncio.create_task(
            self._broadcaster.emit("channels_batch_update", {"channels": channels_data})
        )

    def get_channels(self) -> list[dict[str, Any]]:
        """Get all currently tracked channels.

        Returns:
            List of channel data dictionaries
        """
        return list(self._channels.values())
