"""Channel points events of idle watching: their websocket topics, raids, moments and predictions."""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

import aiohttp

from src.config import (
    GQL_OPERATIONS,
    MAX_WEBSOCKETS,
    TOPICS_PER_CHANNEL,
    WS_TOPICS_LIMIT,
    WebsocketTopic,
)
from src.exceptions import ExitRequest, MinerException
from src.services.points_common import error_code, remember
from src.services.predictions import Predictions
from src.utils import task_wrapper


if TYPE_CHECKING:
    from src.config import GQLOperation, JsonType
    from src.core.client import Twitch
    from src.services.idle_watch import IdleWatchService


logger = logging.getLogger("TwitchDrops")

MUTATION_TIMEOUT = 30


class PointsEvents:
    """Subscribe the event topics of enabled features while the loop runs and act on their messages.

    Topics: `predictions-user-v1` and `predictions-channel-v1` of every live listed channel while
    predictions are on, `community-moments-channel-v1` of every live listed channel while moments are
    on, and `raid` of the idle-watched channel while raids are on. `sync()` on every tick adds and
    removes them; `clear()` removes them all when the loop stops. Topics never take the room the pool
    keeps for the stream topics of listed channels that are not tracked yet.
    """

    def __init__(self, twitch: Twitch, service: IdleWatchService) -> None:
        self._twitch = twitch
        self._service = service
        self.predictions = Predictions(twitch, service, self.send)
        # topic string -> topic subscribed by this class
        self._topics: dict[str, WebsocketTopic] = {}
        # the last sync could not add every topic; logged once until all fit again
        self._limited = False
        # raid and moment ids acted on (or tried): never sent twice
        self._raids: dict[str, None] = {}
        self._moments: dict[str, None] = {}

    def _wanted(self) -> list[WebsocketTopic]:
        svc, settings = self._service, self._twitch.settings
        if not svc.active or svc.points_paused:
            return []
        topics: list[WebsocketTopic] = []
        if settings.predictions_enabled and (user_id := self._twitch.logged_in_user_id()) is not None:
            topics.append(WebsocketTopic("User", "Predictions", user_id, self.predictions.process_result))
        for channel in svc.live_channels():
            if settings.predictions_enabled:
                topics.append(WebsocketTopic("Channel", "Predictions", channel.id, self.predictions.process_event))
            if settings.claim_moments:
                topics.append(WebsocketTopic("Channel", "CommunityMoments", channel.id, self.process_moment))
            if settings.follow_raids and channel is svc.watched:
                topics.append(WebsocketTopic("Channel", "Raid", channel.id, self.process_raid))
        return topics

    def _room(self) -> int:
        """Free topic slots, less the stream topics of listed logins not tracked yet."""
        tw = self._twitch
        used = sum(len(ws.topics) for ws in tw.websocket.websockets)
        tracked = {c.login for c in tw.channels.values()}
        pending = sum(login not in tracked for login in tw.settings.idle_channels)
        return MAX_WEBSOCKETS * WS_TOPICS_LIMIT - used - TOPICS_PER_CHANNEL * pending

    def sync(self) -> None:
        """Subscribe the topics the loop needs now and unsubscribe the rest."""
        wanted = {str(t): t for t in self._wanted()}
        gone = [name for name in self._topics if name not in wanted]
        if gone:
            self._twitch.websocket.remove_topics(gone)
            for name in gone:
                del self._topics[name]
        new = [t for name, t in wanted.items() if name not in self._topics]
        if not new:
            self._limited = False
            return
        pool = self._twitch.websocket
        add = new[: max(self._room(), 0)]
        limited = len(add) < len(new)
        try:
            pool.add_topics(add)
        except MinerException:
            # the pool keeps the topics that fit
            limited = True
        subscribed = {name for ws in pool.websockets for name in ws.topics}
        self._topics.update((str(t), t) for t in add if str(t) in subscribed)
        if limited and not self._limited:
            logger.warning("Topic limit reached: some channel points events are not followed")
            self._twitch.gui.points.log("topic_limit")
        self._limited = limited

    def clear(self) -> None:
        """Unsubscribe every topic and drop the open predictions when the loop stops."""
        if self._topics:
            self._twitch.websocket.remove_topics(list(self._topics))
            self._topics.clear()
        self._limited = False
        self.predictions.clear()

    def reset(self) -> None:
        """Forget the ids acted on with the account."""
        self.clear()
        self.predictions.reset()
        self._raids.clear()
        self._moments.clear()

    async def send(self, op: GQLOperation, *, single_attempt: bool = False) -> tuple[JsonType | None, str | None]:
        """Send a mutation: (reply, None), or (None, the failure's type name). Never resent here."""
        op.single_attempt = single_attempt
        try:
            return await asyncio.wait_for(self._twitch.gql_request(op), MUTATION_TIMEOUT), None
        except ExitRequest:
            raise
        except (MinerException, aiohttp.ClientError, TimeoutError) as exc:
            # exception type only: a reply body could carry account data
            logger.warning("Twitch request %s failed (%s)", op["operationName"], type(exc).__name__)
            return None, type(exc).__name__

    @task_wrapper
    async def process_raid(self, channel_id: int, message: JsonType) -> None:
        """Join a raid that starts on the idle-watched channel, once per raid id; the target is not watched.

        Shape: {"type": "raid_update_v2", "raid": {"id": "r1", "target_login": "x", ...}}
        """
        raid = message.get("raid") if isinstance(message, dict) and message.get("type") == "raid_update_v2" else None
        raid_id = raid.get("id") if isinstance(raid, dict) else None
        if not isinstance(raid, dict) or not isinstance(raid_id, str) or not raid_id or raid_id in self._raids:
            return
        target = raid.get("target_login") if isinstance(raid.get("target_login"), str) else "?"
        channel = self._service.idle_watched(channel_id)
        if channel is None or not self._service.may_act(channel, "follow_raids"):
            return
        remember(self._raids, raid_id)
        # sent once: it is visible to the streamer and the raid target
        response, code = await self.send(
            GQL_OPERATIONS["JoinRaid"].with_variables({"input": {"raidID": raid_id}}), single_attempt=True,
        )
        if response is not None:
            code = error_code(response, "joinRaid")
        if code is not None:
            logger.warning("Could not join the raid from %s (%s)", channel.name, code)
            self._twitch.gui.points.log("raid_failed", channel=channel.name, code=code)
            return
        logger.info("Joined the raid from %s to %s", channel.name, target)
        self._twitch.gui.points.log("raid_joined", channel=channel.name, target=target)

    @task_wrapper
    async def process_moment(self, channel_id: int, message: JsonType) -> None:
        """Claim a community moment on a live listed channel, once per moment id.

        Shape: {"type": "active", "data": {"moment_id": "m1", "channel_id": "1"}}
        """
        data = message.get("data") if isinstance(message, dict) and message.get("type") == "active" else None
        moment_id = data.get("moment_id") if isinstance(data, dict) else None
        if not isinstance(moment_id, str) or not moment_id or moment_id in self._moments:
            return
        channel = self._service.listed(str(channel_id))
        if channel is None or not self._service.may_act(channel, "claim_moments"):
            return
        remember(self._moments, moment_id)
        response, code = await self.send(
            GQL_OPERATIONS["CommunityMomentCallout_Claim"].with_variables({"input": {"momentID": moment_id}}),
        )
        if response is not None:
            code = error_code(response, "claimCommunityMoment")
        if code is not None:
            logger.warning("Could not claim a moment on %s (%s)", channel.name, code)
            return
        logger.info("Claimed a moment on %s", channel.name)
        self._twitch.gui.points.log("moment_claimed", channel=channel.name)
