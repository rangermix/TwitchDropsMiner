"""Chat presence of idle watching: sit in the idle-watched channel's chat without ever sending a message."""

from __future__ import annotations

import asyncio
import logging
import re
import time
from typing import TYPE_CHECKING

import aiohttp

from src.config import GQLRawQuery
from src.exceptions import MinerException
from src.utils import ExponentialBackoff, task_wrapper


if TYPE_CHECKING:
    from src.core.client import Twitch
    from src.models.channel import Channel
    from src.services.idle_watch import IdleWatchService


logger = logging.getLogger("TwitchDrops")

CHAT_URL = "wss://irc-ws.chat.twitch.tv:443"
CHAT_BACKOFF_MAX = 300
# the reconnect wait starts over only after a connection stayed up this long
CHAT_STABLE_SECONDS = 60
# Twitch pings about every 5 minutes; a connection silent for longer is dead
CHAT_READ_TIMEOUT = 600
LOGIN_OPERATION = "ChatPresenceLogin"
LOGIN_QUERY = "query ChatPresenceLogin { currentUser { login } }"
# a Twitch login; anything else could inject IRC commands
LOGIN_PATTERN = re.compile(r"[a-z0-9_]{1,25}")


class ChatPresence:
    """One IRC websocket in the chat of the idle-watched channel (setting `join_chat`).

    It sends PASS (the session's access token), NICK (the account's login), JOIN and PONG only. It
    leaves on a switch, when the loop stops or pauses, and reconnects with a backoff of at most
    CHAT_BACKOFF_MAX seconds while the channel is still wanted; the backoff starts over only after a
    connection stayed up CHAT_STABLE_SECONDS. After a refused login it does not connect again until the
    account, the token or the `join_chat` setting changes. The token is never logged.
    """

    def __init__(self, twitch: Twitch, service: IdleWatchService) -> None:
        self._twitch = twitch
        self._service = service
        self._channel: Channel | None = None
        self._task: asyncio.Task[None] | None = None
        self._joined = False
        # the account's login, read once per account
        self._login: str | None = None
        # (login, token) of a login Twitch refused: no connect with them again
        self._refused: tuple[str, str] | None = None

    def settings_changed(self) -> None:
        """An idle option was saved: turning `join_chat` off (and on again) allows a new try after a refused login."""
        if not self._twitch.settings.join_chat:
            self._refused = None

    def follow(self, channel: Channel | None) -> None:
        """Sit in channel's chat (None: leave the current one)."""
        if channel is not None and channel is self._channel and self._task is not None and not self._task.done():
            return
        self.leave()
        if channel is not None:
            self._channel = channel
            self._task = asyncio.create_task(self._run(channel))

    def leave(self) -> None:
        task, self._task = self._task, None
        if task is not None:
            task.cancel()
        if self._channel is not None:
            self._left(self._channel)
        self._channel = None

    def reset(self) -> None:
        self.leave()
        self._login = None
        self._refused = None

    def _left(self, channel: Channel) -> None:
        if self._joined:
            self._joined = False
            logger.info("Left the chat of %s", channel.name)
            self._twitch.gui.points.log("chat_left", channel=channel.name)

    def _wanted(self, channel: Channel) -> bool:
        return channel is self._service.watched and self._service.may_act(channel, "join_chat")

    @task_wrapper
    async def _run(self, channel: Channel) -> None:
        backoff = ExponentialBackoff(maximum=CHAT_BACKOFF_MAX)
        # checked before every (re)connect: a reconnect is a new join
        while self._wanted(channel):
            started = time.monotonic()
            try:
                again = await self._session(channel)
            except RuntimeError:
                # the HTTP session closed (shutdown)
                again = False
            except (MinerException, aiohttp.ClientError, OSError, TimeoutError) as exc:
                # a failed auth check (MinerException) is retried like a lost connection
                logger.warning("Chat of %s disconnected (%s), reconnecting", channel.name, type(exc).__name__)
                again = True
            # every end of a session leaves the chat; a cancel is logged by leave()
            self._left(channel)
            if not again:
                # a refused login (no reconnect with the same account and token) or a shutdown
                return
            # a connection that drops right after it opened keeps the wait growing
            if time.monotonic() - started >= CHAT_STABLE_SECONDS:
                backoff.reset()
            await asyncio.sleep(next(backoff))

    async def _account_login(self) -> str | None:
        if self._login is None:
            response = await self._service.gql(GQLRawQuery(LOGIN_QUERY, operation_name=LOGIN_OPERATION))
            try:
                login = response["data"]["currentUser"]["login"]  # type: ignore[index,call-overload]
            except (KeyError, TypeError):
                login = None
            if isinstance(login, str) and LOGIN_PATTERN.fullmatch(login):
                self._login = login
        return self._login

    async def _session(self, channel: Channel) -> bool:
        """One connection until Twitch closes it; False when the login is refused (also an earlier refusal)."""
        login = await self._account_login()
        if login is None:
            logger.warning("Chat of %s not joined: the account login is unknown", channel.name)
            return True
        token = (await self._twitch.get_auth()).access_token
        if self._refused == (login, token):
            return False
        session = await self._twitch.get_session()
        joined = False
        async with session.ws_connect(CHAT_URL, proxy=self._twitch.settings.proxy or None) as ws:
            await ws.send_str(f"PASS oauth:{token}")
            await ws.send_str(f"NICK {login}")
            await ws.send_str(f"JOIN #{channel.login}")
            while True:
                message = await ws.receive(timeout=CHAT_READ_TIMEOUT)
                if message.type is not aiohttp.WSMsgType.TEXT:
                    return True
                for line in message.data.split("\r\n"):
                    if line.startswith("PING"):
                        await ws.send_str("PONG" + line[4:])
                    elif line.startswith(":tmi.twitch.tv NOTICE * :"):
                        # a refused login (bad or expired token); the line is not logged
                        logger.warning("Chat of %s refused the login; no retry until the account, token or join_chat changes", channel.name)
                        self._refused = (login, token)
                        return False
                    elif not joined and line.startswith(f":{login}!") and f" JOIN #{channel.login}" in line:
                        joined = self._joined = True
                        logger.info("Joined the chat of %s", channel.name)
                        self._twitch.gui.points.log("chat_joined", channel=channel.name)
