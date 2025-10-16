from __future__ import annotations

import asyncio
import logging
from typing import Any, Literal, TYPE_CHECKING

from src.exceptions import MinerException
from src.config import MAX_WEBSOCKETS, WS_TOPICS_LIMIT
from src.websocket.websocket import Websocket

if TYPE_CHECKING:
    from collections import abc

    from src.core.client import Twitch
    from src.config import WebsocketTopic


logger = logging.getLogger("TwitchDrops")


class WebsocketPool:
    """
    Manages a pool of websocket connections to distribute topics across multiple connections.

    Twitch limits the number of topics per websocket, so this pool automatically
    creates additional websockets as needed to handle all subscribed topics.
    """

    def __init__(self, twitch: Twitch):
        """
        Initialize the websocket pool.

        Args:
            twitch: Twitch client instance
        """
        self._twitch: Twitch = twitch
        self._running = asyncio.Event()
        self.websockets: list[Websocket] = []

    @property
    def running(self) -> bool:
        """Check if the pool is currently running."""
        return self._running.is_set()

    def wait_until_connected(self) -> abc.Coroutine[Any, Any, Literal[True]]:
        """Wait until the pool is running and connections are established."""
        return self._running.wait()

    async def start(self):
        """Start all websockets in the pool."""
        self._running.set()
        await asyncio.gather(*(ws.start() for ws in self.websockets))

    async def stop(self, *, clear_topics: bool = False):
        """
        Stop all websockets in the pool.

        Args:
            clear_topics: If True, clear all topics and remove websockets from GUI
        """
        self._running.clear()
        await asyncio.gather(*(ws.stop(remove=clear_topics) for ws in self.websockets))

    def add_topics(self, topics: abc.Iterable[WebsocketTopic]):
        """
        Add topics to the pool, distributing across websockets as needed.

        Creates new websocket connections if existing ones are at capacity.
        Raises MinerException if the maximum number of topics/websockets is reached.

        Args:
            topics: Iterable of topics to add

        Raises:
            MinerException: If maximum topics limit is reached
        """
        # ensure no topics end up duplicated
        topics_set = set(topics)
        if not topics_set:
            # nothing to add
            return
        topics_set.difference_update(*(ws.topics.values() for ws in self.websockets))
        if not topics_set:
            # none left to add
            return
        for ws_idx in range(MAX_WEBSOCKETS):
            if ws_idx < len(self.websockets):
                # just read it back
                ws = self.websockets[ws_idx]
            else:
                # create new
                ws = Websocket(self, ws_idx)
                if self.running:
                    ws.start_nowait()
                self.websockets.append(ws)
            # ask websocket to take any topics it can - this modifies the set in-place
            ws.add_topics(topics_set)
            # see if there's any leftover topics for the next websocket connection
            if not topics_set:
                return
        # if we're here, there were leftover topics after filling up all websockets
        raise MinerException("Maximum topics limit has been reached")

    def remove_topics(self, topics: abc.Iterable[str]):
        """
        Remove topics from the pool.

        Automatically stops and removes websockets that become empty after topic removal.
        Recycles topics from removed websockets to maintain efficient connection usage.

        Args:
            topics: Iterable of topic strings to remove
        """
        topics_set = set(topics)
        if not topics_set:
            # nothing to remove
            return
        for ws in self.websockets:
            ws.remove_topics(topics_set)
        # count up all the topics - if we happen to have more websockets connected than needed,
        # stop the last one and recycle topics from it - repeat until we have enough
        recycled_topics: list[WebsocketTopic] = []
        while True:
            count = sum(len(ws.topics) for ws in self.websockets)
            if count <= (len(self.websockets) - 1) * WS_TOPICS_LIMIT:
                ws = self.websockets.pop()
                recycled_topics.extend(ws.topics.values())
                ws.stop_nowait(remove=True)
            else:
                break
        if recycled_topics:
            self.add_topics(recycled_topics)
