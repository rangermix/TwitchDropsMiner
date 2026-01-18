from __future__ import annotations

import asyncio
import json
import logging
from contextlib import suppress
from time import time
from typing import TYPE_CHECKING

import aiohttp

from src.config import PING_INTERVAL, PING_TIMEOUT, WS_TOPICS_LIMIT
from src.exceptions import WebsocketClosed
from src.i18n import _
from src.utils import (
    CHARS_ASCII,
    AwaitableValue,
    ExponentialBackoff,
    chunk,
    create_nonce,
    format_traceback,
    json_minify,
    task_wrapper,
)


if TYPE_CHECKING:
    from src.config import JsonType, WebsocketTopic
    from src.core.client import Twitch
    from src.web.gui_manager import WebsocketStatus


WSMsgType = aiohttp.WSMsgType
logger = logging.getLogger("TwitchDrops")
ws_logger = logging.getLogger("TwitchDrops.websocket")


class Websocket:
    """
    Manages a single websocket connection to Twitch's PubSub service.

    Handles connection lifecycle, topic subscriptions, ping/pong heartbeat,
    and message routing to registered topic handlers.
    """

    def __init__(self, pool, index: int):
        """
        Initialize a Websocket instance.

        Args:
            pool: WebsocketPool instance that owns this websocket
            index: Numeric index for logging and identification
        """
        from src.websocket.pool import WebsocketPool

        self._pool: WebsocketPool = pool
        self._twitch: Twitch = pool._twitch
        self._ws_gui: WebsocketStatus = self._twitch.gui.websockets
        self._state_lock = asyncio.Lock()
        # websocket index
        self._idx: int = index
        # current websocket connection
        self._ws: AwaitableValue[aiohttp.ClientWebSocketResponse] = AwaitableValue()
        # set when the websocket needs to be closed or reconnect
        self._closed = asyncio.Event()
        self._reconnect_requested = asyncio.Event()
        # set when the topics changed
        self._topics_changed = asyncio.Event()
        # ping timestamps
        self._next_ping: float = time()
        self._max_pong: float = self._next_ping + PING_TIMEOUT.total_seconds()
        # main task, responsible for receiving messages, sending them, and websocket ping
        self._handle_task: asyncio.Task[None] | None = None
        # topics stuff
        self.topics: dict[str, WebsocketTopic] = {}
        self._submitted: set[WebsocketTopic] = set()
        # notify GUI
        self.set_status(_.t["gui"]["websocket"]["disconnected"])

    @property
    def connected(self) -> bool:
        """Check if the websocket is currently connected."""
        return self._ws.has_value()

    def wait_until_connected(self):
        """Wait until the websocket is connected."""
        return self._ws.wait()

    def set_status(self, status: str | None = None, refresh_topics: bool = False):
        """
        Update the websocket status in the GUI.

        Args:
            status: New status message, or None to keep current
            refresh_topics: If True, update the topic count in the GUI
        """
        self._twitch.gui.websockets.update(
            self._idx, status=status, topics=(len(self.topics) if refresh_topics else None)
        )

    def request_reconnect(self):
        """Request a websocket reconnection."""
        # reset our ping interval, so we send a PING after reconnect right away
        self._next_ping = time()
        self._reconnect_requested.set()

    async def start(self):
        """Start the websocket connection and wait until connected."""
        async with self._state_lock:
            self.start_nowait()
            await self.wait_until_connected()

    def start_nowait(self):
        """Start the websocket connection without waiting."""
        if self._handle_task is None or self._handle_task.done():
            self._handle_task = asyncio.create_task(self._handle())

    async def stop(self, *, remove: bool = False):
        """
        Stop the websocket connection.

        Args:
            remove: If True, clear topics and remove from GUI
        """
        async with self._state_lock:
            if self._closed.is_set():
                return
            self._closed.set()
            ws = self._ws.get_with_default(None)
            if ws is not None:
                self.set_status(_.t["gui"]["websocket"]["disconnecting"])
                await ws.close()
            if self._handle_task is not None:
                with suppress(asyncio.TimeoutError, asyncio.CancelledError):
                    await asyncio.wait_for(self._handle_task, timeout=2)
                self._handle_task = None
            if remove:
                self.topics.clear()
                self._topics_changed.set()
                # TODO: WebsocketStatusManager doesn't have a remove() method yet
                # self._twitch.gui.websockets.remove(self._idx)

    def stop_nowait(self, *, remove: bool = False):
        """
        Stop the websocket connection without waiting.

        Args:
            remove: If True, clear topics and remove from GUI
        """
        asyncio.create_task(self.stop(remove=remove))

    async def _backoff_connect(self, ws_url: str, **kwargs):
        """
        Connect to websocket with exponential backoff retry logic.

        Args:
            ws_url: Websocket URL to connect to
            **kwargs: Additional arguments passed to ExponentialBackoff

        Yields:
            Connected websocket instances
        """
        session = await self._twitch.get_session()
        backoff = ExponentialBackoff(**kwargs)
        proxy = self._twitch.settings.proxy or None
        ws_logger.info(
            f"Websocket[{self._idx}] connecting with {'no' if proxy is None else proxy} proxy"
        )
        for delay in backoff:
            try:
                async with session.ws_connect(ws_url, proxy=proxy) as websocket:
                    yield websocket
                    backoff.reset()
            except (
                asyncio.TimeoutError,
                aiohttp.ClientResponseError,
                aiohttp.ClientConnectionError,
            ):
                ws_logger.info(
                    f"Websocket[{self._idx}] connection problem (sleep: {round(delay)}s)"
                )
                await asyncio.sleep(delay)
            except RuntimeError:
                ws_logger.warning(
                    f"Websocket[{self._idx}] exiting backoff connect loop "
                    "because session is closed (RuntimeError)"
                )
                break

    @task_wrapper(critical=True)
    async def _handle(self):
        """Main websocket handler that manages connection lifecycle and message processing."""
        # ensure we're logged in before connecting
        self.set_status(_.t["gui"]["websocket"]["initializing"])
        await self._twitch.wait_until_login()
        self.set_status(_.t["gui"]["websocket"]["connecting"])
        ws_logger.debug(f"Websocket[{self._idx}] connecting...")
        self._closed.clear()
        # Connect/Reconnect loop
        async for websocket in self._backoff_connect(
            "wss://pubsub-edge.twitch.tv/v1",
            maximum=3 * 60,  # 3 minutes maximum backoff time
        ):
            self._ws.set(websocket)
            self._reconnect_requested.clear()
            # NOTE: _topics_changed doesn't start set,
            # because there's no initial topics we can sub to right away
            self.set_status(_.t["gui"]["websocket"]["connected"])
            ws_logger.debug(f"Websocket[{self._idx}] connected.")
            try:
                try:
                    while not self._reconnect_requested.is_set():
                        await self._handle_ping()
                        await self._handle_topics()
                        await self._handle_recv()
                finally:
                    self._ws.clear()
                    self._submitted.clear()
                    # set _topics_changed to let the next WS connection resub to the topics
                    self._topics_changed.set()
                # A reconnect was requested
            except WebsocketClosed as exc:
                if exc.received:
                    # server closed the connection, not us - reconnect
                    ws_logger.warning(
                        f"Websocket[{self._idx}] to wss://pubsub-edge.twitch.tv/v1 closed unexpectedly: {websocket.close_code}"
                    )
                elif self._closed.is_set():
                    # we closed it - exit
                    ws_logger.debug(
                        f"Websocket[{self._idx}] to wss://pubsub-edge.twitch.tv/v1 stopped."
                    )
                    self.set_status(_.t["gui"]["websocket"]["disconnected"])
                    return
            except Exception:
                ws_logger.exception(
                    f"Exception in Websocket[{self._idx}] to wss://pubsub-edge.twitch.tv/v1"
                )
            self.set_status(_.t["gui"]["websocket"]["reconnecting"])
            ws_logger.warning(
                f"Websocket[{self._idx}] to wss://pubsub-edge.twitch.tv/v1 reconnecting..."
            )

    async def _handle_ping(self):
        """Handle ping/pong heartbeat to keep connection alive."""
        now = time()
        if now >= self._next_ping:
            self._next_ping = now + PING_INTERVAL.total_seconds()
            self._max_pong = now + PING_TIMEOUT.total_seconds()  # wait for a PONG for up to 10s
            await self.send({"type": "PING"})
        elif now >= self._max_pong:
            # it's been more than 10s and there was no PONG
            ws_logger.warning(f"Websocket[{self._idx}] didn't receive a PONG, reconnecting...")
            self.request_reconnect()

    async def _handle_topics(self):
        """Handle topic subscription changes (LISTEN/UNLISTEN messages)."""
        if not self._topics_changed.is_set():
            # nothing to do
            return
        self._topics_changed.clear()
        self.set_status(refresh_topics=True)
        auth_state = await self._twitch.get_auth()
        current: set[WebsocketTopic] = set(self.topics.values())
        # handle removed topics
        removed = self._submitted.difference(current)
        if removed:
            topics_list = list(map(str, removed))
            ws_logger.debug(f"Websocket[{self._idx}]: Removing topics: {', '.join(topics_list)}")
            for topics in chunk(topics_list, 10):
                await self.send(
                    {
                        "type": "UNLISTEN",
                        "data": {
                            "topics": topics,
                            "auth_token": auth_state.access_token,
                        },
                    }
                )
            self._submitted.difference_update(removed)
        # handle added topics
        added = current.difference(self._submitted)
        if added:
            topics_list = list(map(str, added))
            ws_logger.debug(f"Websocket[{self._idx}]: Adding topics: {', '.join(topics_list)}")
            for topics in chunk(topics_list, 10):
                await self.send(
                    {
                        "type": "LISTEN",
                        "data": {
                            "topics": topics,
                            "auth_token": auth_state.access_token,
                        },
                    }
                )
            self._submitted.update(added)

    async def _gather_recv(self, messages: list[JsonType], timeout: float = 0.5):
        """
        Gather incoming messages over the timeout specified.

        Args:
            messages: List to append received messages to (modified in-place)
            timeout: How long to gather messages for in seconds

        Raises:
            WebsocketClosed: When the websocket connection closes
        """
        ws = self._ws.get_with_default(None)
        assert ws is not None
        while True:
            raw_message: aiohttp.WSMessage = await ws.receive(timeout=timeout)
            ws_logger.debug(f"Websocket[{self._idx}] received: {raw_message}")
            if raw_message.type is WSMsgType.TEXT:
                message: JsonType = json.loads(raw_message.data)
                messages.append(message)
            elif raw_message.type is WSMsgType.CLOSE:
                raise WebsocketClosed(received=True, raw_message=raw_message.data)
            elif raw_message.type is WSMsgType.CLOSED:
                raise WebsocketClosed(received=False, raw_message=raw_message.data)
            elif raw_message.type is WSMsgType.CLOSING:
                pass  # skip these
            elif raw_message.type is WSMsgType.ERROR:
                ws_logger.error(
                    f"Websocket[{self._idx}] error: {format_traceback(raw_message.data)}"
                )
                raise WebsocketClosed(raw_message=raw_message.data)
            else:
                ws_logger.error(f"Websocket[{self._idx}] error: Unknown message: {raw_message}")

    def _handle_message(self, message):
        """
        Route a received MESSAGE to the appropriate topic handler.

        Args:
            message: Websocket message dict with topic and message data
        """
        # request the assigned topic to process the response
        topic = self.topics.get(message["data"]["topic"])
        if topic is not None:
            # use a task to not block the websocket
            asyncio.create_task(topic(json.loads(message["data"]["message"])))

    async def _handle_recv(self):
        """Handle receiving and processing messages from the websocket."""
        # listen over 0.5s for incoming messages
        messages: list[JsonType] = []
        with suppress(asyncio.TimeoutError):
            await self._gather_recv(messages, timeout=0.5)
        # process them
        for message in messages:
            msg_type = message["type"]
            if msg_type == "MESSAGE":
                self._handle_message(message)
            elif msg_type == "PONG":
                # move the timestamp to something much later
                self._max_pong = self._next_ping
            elif msg_type == "RESPONSE":
                # no special handling for these (for now)
                pass
            elif msg_type == "RECONNECT":
                # We've received a reconnect request
                ws_logger.warning(f"Websocket[{self._idx}] requested reconnect.")
                self.request_reconnect()
            else:
                ws_logger.warning(f"Websocket[{self._idx}] received unknown payload: {message}")

    def add_topics(self, topics_set: set[WebsocketTopic]):
        """
        Add topics to this websocket, up to the limit.

        Args:
            topics_set: Set of topics to add (modified in-place, removing added topics)
        """
        changed: bool = False
        while topics_set and len(self.topics) < WS_TOPICS_LIMIT:
            topic = topics_set.pop()
            self.topics[str(topic)] = topic
            changed = True
        if changed:
            self._topics_changed.set()

    def remove_topics(self, topics_set: set[str]):
        """
        Remove topics from this websocket.

        Args:
            topics_set: Set of topic strings to remove (modified in-place)
        """
        existing = topics_set.intersection(self.topics.keys())
        if not existing:
            # nothing to remove from here
            return
        topics_set.difference_update(existing)
        for topic in existing:
            del self.topics[topic]
        self._topics_changed.set()

    async def send(self, message: JsonType):
        """
        Send a JSON message to the websocket.

        Args:
            message: JSON-serializable message dict
        """
        ws = self._ws.get_with_default(None)
        assert ws is not None
        if message["type"] != "PING":
            message["nonce"] = create_nonce(CHARS_ASCII, 30)
        await ws.send_json(message, dumps=json_minify)
        ws_logger.debug(f"Websocket[{self._idx}] sent: {message}")
