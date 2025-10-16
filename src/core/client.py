from __future__ import annotations

import json
import asyncio
import logging
from time import time
from copy import deepcopy
from functools import partial
from collections import abc, deque, OrderedDict
from datetime import datetime, timedelta, timezone
from contextlib import suppress
from typing import Any, Literal, Final, NoReturn, TYPE_CHECKING
from dateutil.parser import isoparse
from src.web.gui_manager import WebGUIManager

import aiohttp

from src.i18n import _
from src.models.channel import Channel
from src.websocket import WebsocketPool
from src.models.campaign import DropsCampaign
from src.auth import _AuthState
from src.api import HTTPClient, GQLClient
from src.services.maintenance import MaintenanceService
from src.services.channel_service import ChannelService
from src.services.message_handlers import MessageHandlerService
from src.services.inventory_service import InventoryService
from src.services.watch_service import WatchService
from src.exceptions import (
    ExitRequest,
    ReloadRequest,
    RequestException,
    GQLException,
    MinerException,
)
from src.utils import (
    chunk,
    task_wrapper,
    AwaitableValue,
)
from src.config import (
    CALL,
    MAX_INT,
    DUMP_PATH,
    MAX_CHANNELS,
    GQL_OPERATIONS,
    WATCH_INTERVAL,
    State,
    ClientType,
    WebsocketTopic,
)

if TYPE_CHECKING:
    from src.models.game import Game
    from src.models.channel import Stream
    from src.config.settings import Settings
    from src.models.drop import TimedDrop
    from src.config import ClientInfo, JsonType, GQLOperation


logger = logging.getLogger("TwitchDrops")
gql_logger = logging.getLogger("TwitchDrops.gql")


class SkipExtraJsonDecoder(json.JSONDecoder):
    def decode(self, s: str, _w: Any = None) -> Any:  # type: ignore[override]
        # skip whitespace check
        obj, end = self.raw_decode(s)
        return obj


def _safe_loads(s: str) -> Any:
    """JSON loads that skips extra data after the first valid JSON object."""
    return json.loads(s, cls=SkipExtraJsonDecoder)


class Twitch:
    def __init__(self, settings: Settings):
        self.settings: Settings = settings
        # State management
        self._state: State = State.IDLE
        self._state_change = asyncio.Event()
        self.wanted_games: list[Game] = []
        self.inventory: list[DropsCampaign] = []
        self._drops: dict[str, TimedDrop] = {}
        self._campaigns: dict[str, DropsCampaign] = {}
        self._mnt_triggers: deque[datetime] = deque()
        # Client type and auth
        self._client_type: ClientInfo = ClientType.ANDROID_APP
        self._auth_state: _AuthState = _AuthState(self)
        # GUI (will be set by main.py)
        self.gui: WebGUIManager = None # type: ignore[assignment]
        # API clients (will be initialized after GUI is set)
        self._http_client: HTTPClient | None = None
        self._gql_client: GQLClient | None = None
        # Storing and watching channels
        self.channels: OrderedDict[int, Channel] = OrderedDict()
        self.watching_channel: AwaitableValue[Channel] = AwaitableValue()
        self._watching_task: asyncio.Task[None] | None = None
        self._watching_restart = asyncio.Event()
        # Websocket
        self.websocket = WebsocketPool(self)
        # Maintenance task
        self._mnt_task: asyncio.Task[None] | None = None
        # Services
        self._maintenance_service: MaintenanceService = MaintenanceService(self)
        self._channel_service: ChannelService = ChannelService(self)
        self._message_handler_service: MessageHandlerService = MessageHandlerService(self)
        self._inventory_service: InventoryService = InventoryService(self)
        self._watch_service: WatchService = WatchService(self)

    def _ensure_api_clients(self) -> None:
        """Ensure API clients are initialized (called after GUI is set)."""
        if self._http_client is None:
            self._http_client = HTTPClient(self.settings, self.gui, self._client_type)
        if self._gql_client is None:
            self._gql_client = GQLClient(self._http_client, self._auth_state, self._client_type)

    async def get_session(self):
        """
        Get the HTTP session (for backward compatibility).

        Delegates to HTTPClient.
        """
        self._ensure_api_clients()
        assert self._http_client is not None
        return await self._http_client.get_session()

    def request(self, method: str, url: str | Any, **kwargs):
        """
        Make an HTTP request (for backward compatibility).

        Delegates to HTTPClient.
        """
        self._ensure_api_clients()
        assert self._http_client is not None
        return self._http_client.request(method, url, **kwargs)

    async def shutdown(self) -> None:
        start_time = time()
        self.stop_watching()
        if self._watching_task is not None:
            self._watching_task.cancel()
            self._watching_task = None
        if self._mnt_task is not None:
            self._mnt_task.cancel()
            self._mnt_task = None
        # stop websocket and close HTTP session
        await self.websocket.stop(clear_topics=True)
        if self._http_client is not None:
            await self._http_client.close()
        self._drops.clear()
        self.channels.clear()
        self.inventory.clear()
        self._auth_state.clear()
        self.wanted_games.clear()
        self._mnt_triggers.clear()
        # wait at least half a second + whatever it takes to complete the closing
        # this allows aiohttp to safely close the session
        await asyncio.sleep(start_time + 0.5 - time())

    def wait_until_login(self) -> abc.Coroutine[Any, Any, Literal[True]]:
        """Wait until the user is logged in."""
        return self._auth_state._logged_in.wait()

    def change_state(self, state: State) -> None:
        """Change the current state of the miner."""
        if self._state is not State.EXIT:
            # prevent state changing once we switch to exit state
            self._state = state
        self._state_change.set()

    def state_change(self, state: State) -> abc.Callable[[], None]:
        """Return a callable that changes state when invoked (deferred call for GUI usage)."""
        return partial(self.change_state, state)

    def close(self) -> None:
        """
        Called when the application is requested to close by the user,
        usually by the console or application window being closed.
        """
        self.change_state(State.EXIT)

    def prevent_close(self) -> None:
        """
        Called when the application window has to be prevented from closing, even after the user
        closes it with X. Usually used solely to display tracebacks from the closing sequence.
        """
        self.gui.prevent_close()

    def print(self, message: str) -> None:
        """Print a message in the GUI."""
        self.gui.print(message)

    def save(self, *, force: bool = False) -> None:
        """Save the application state (settings and GUI state)."""
        self.gui.save(force=force)
        self.settings.save(force=force)

    def get_priority(self, channel: Channel) -> int:
        """
        Return a priority number for a given channel based on games_to_watch order.

        0 has the highest priority (first in games_to_watch list).
        Higher numbers -> lower priority.
        MAX_INT (a really big number) signifies the lowest possible priority.
        """
        if (
            (game := channel.game) is None  # None when OFFLINE or no game set
            or game not in self.wanted_games  # we don't care about the played game
        ):
            return MAX_INT
        return self.wanted_games.index(game)

    @staticmethod
    def _viewers_key(channel: Channel) -> int:
        """Sort key for channels by viewer count (descending)."""
        if (viewers := channel.viewers) is not None:
            return viewers
        return -1

    def _remove_channel_topics(self, channels: abc.Iterable[Channel]) -> None:
        """Remove websocket topics for a list of channels."""
        topics_to_remove: list[str] = []
        for channel in channels:
            topics_to_remove.append(
                WebsocketTopic.as_str("Channel", "StreamState", channel.id)
            )
            topics_to_remove.append(
                WebsocketTopic.as_str("Channel", "StreamUpdate", channel.id)
            )
        if topics_to_remove:
            self.websocket.remove_topics(topics_to_remove)

    async def run(self) -> None:
        """Main entry point for the miner - handles reload and exit requests."""
        if self.settings.dump:
            with open(DUMP_PATH, 'w', encoding="utf8"):
                # replace the existing file with an empty one
                pass
        while True:
            try:
                await self._run()
                break
            except ReloadRequest:
                await self.shutdown()
            except ExitRequest:
                break
            except aiohttp.ContentTypeError as exc:
                raise RequestException(_("login", "unexpected_content")) from exc

    async def _run(self) -> None:
        """
        Main method that runs the whole client.

        Here, we manage several things, specifically:
        • Fetching the drops inventory to make sure that everything we can claim, is claimed
        • Selecting a stream to watch, and watching it
        • Changing the stream that's being watched if necessary
        """
        self.gui.start()
        # Initialize API clients now that GUI is available
        self._ensure_api_clients()
        auth_state = await self.get_auth()
        await self.websocket.start()
        # NOTE: watch task is explicitly restarted on each new run
        if self._watching_task is not None:
            self._watching_task.cancel()
        self._watching_task = asyncio.create_task(self._watch_loop())
        # Add default topics
        self.websocket.add_topics([
            WebsocketTopic("User", "Drops", auth_state.user_id, self.process_drops),
            WebsocketTopic(
                "User", "Notifications", auth_state.user_id, self.process_notifications
            ),
        ])
        full_cleanup: bool = False
        channels: Final[OrderedDict[int, Channel]] = self.channels
        self.change_state(State.INVENTORY_FETCH)
        while True:
            if self._state is State.IDLE:
                if self.settings.dump:
                    self.gui.close()
                    continue
                self.gui.tray.change_icon("idle")
                self.gui.status.update(_("gui", "status", "idle"))
                self.stop_watching()
                # clear the flag and wait until it's set again
                self._state_change.clear()
            elif self._state is State.INVENTORY_FETCH:
                self.gui.tray.change_icon("maint")
                # ensure the websocket is running
                await self.websocket.start()
                await self.fetch_inventory()
                self.gui.set_games(set(campaign.game for campaign in self.inventory))
                # Save state on every inventory fetch
                self.save()
                self.change_state(State.GAMES_UPDATE)
            elif self._state is State.GAMES_UPDATE:
                # claim drops from expired and active campaigns
                logger.info("Checking for claimable drops")
                logger.debug("Campaigns in inventory: %s", self.inventory)
                for campaign in self.inventory:
                    if not campaign.upcoming:
                        for drop in campaign.drops:
                            if drop.can_claim:
                                await drop.claim()
                # figure out which games we want based on games_to_watch whitelist
                self.wanted_games.clear()
                games_to_watch: list[str] = self.settings.games_to_watch
                next_hour: datetime = datetime.now(timezone.utc) + timedelta(hours=1)
                logger.info("fames_to_watch: %s", games_to_watch)
                logger.info("inventory has %d eligible campaigns", sum(1 for c in self.inventory if c.eligible))
                logger.info("inventories: %s", self.inventory)

                # Log detailed game -> campaigns -> channels mapping
                logger.info("=== Active Campaigns Mapping ===")
                from collections import defaultdict
                game_campaign_map: dict[str, list[tuple[DropsCampaign, list[str]]]] = defaultdict(list)
                for campaign in self.inventory:
                    if campaign.eligible and not campaign.finished:
                        logger.info("eligible Campaign: %s - %s", campaign.name, campaign.game.name)
                    if campaign.can_earn_within(next_hour):
                        channel_names = []
                        if campaign.allowed_channels:
                            channel_names = [ch.name for ch in campaign.allowed_channels]
                        else:
                            channel_names = ["<directory>"]
                        game_campaign_map[campaign.game.name].append((campaign, channel_names))

                for game_name in sorted(game_campaign_map.keys()):
                    logger.info(f"Game: {game_name}")
                    for campaign, channel_list in game_campaign_map[game_name]:
                        status_info = f"{'ACTIVE' if campaign.active else 'UPCOMING'}"
                        ends_info = campaign.ends_at.astimezone().strftime('%Y-%m-%d %H:%M')
                        channel_info = f"{len(channel_list)} channels" if channel_list[0] != "<directory>" else "directory"
                        logger.info(f"  └─ Campaign: {campaign.name} [{status_info}] (ends: {ends_info})")
                        logger.info(f"     Channels: {channel_info}")
                        if channel_list[0] != "<directory>" and len(channel_list) <= 10:
                            logger.info(f"     └─ {', '.join(channel_list)}")
                        elif channel_list[0] != "<directory>":
                            logger.info(f"     └─ {', '.join(channel_list[:10])} ... (+{len(channel_list)-10} more)")
                logger.info("=== End Campaigns Mapping ===")

                # Build wanted_games list preserving the order from games_to_watch
                for game_name in games_to_watch:
                    # Find campaigns for this game (case-insensitive matching)
                    game_name_lower: str = game_name.lower()
                    for campaign in self.inventory:
                        game: Game = campaign.game
                        if (
                            game.name.lower() == game_name_lower
                            and game not in self.wanted_games  # isn't already there
                            and campaign.can_earn_within(next_hour)  # can be progressed within the next hour
                        ):
                            self.wanted_games.append(game)
                            break  # Only add each game once

                if self.wanted_games:
                    logger.info(
                        "Wanted games: %s",
                        ", ".join(game.name for game in self.wanted_games)
                    )
                else:
                    logger.warning(
                        "No wanted games found! games_to_watch=%s, eligible_campaigns=%d",
                        games_to_watch,
                        sum(1 for c in self.inventory if c.eligible and c.can_earn_within(next_hour))
                    )

                full_cleanup = True
                self.restart_watching()
                self.change_state(State.CHANNELS_CLEANUP)
            elif self._state is State.CHANNELS_CLEANUP:
                self.gui.status.update(_("gui", "status", "cleanup"))
                if not self.wanted_games or full_cleanup:
                    # no games selected or we're doing full cleanup: remove everything
                    to_remove_channels: list[Channel] = list(channels.values())
                else:
                    # remove all channels that:
                    to_remove_channels = [
                        channel
                        for channel in channels.values()
                        if (
                            not channel.acl_based  # aren't ACL-based
                            and (
                                channel.offline  # and are offline
                                # or online but aren't streaming the game we want anymore
                                or (channel.game is None or channel.game not in self.wanted_games)
                            )
                        )
                    ]
                full_cleanup = False
                if to_remove_channels:
                    self._remove_channel_topics(to_remove_channels)
                    for channel in to_remove_channels:
                        del channels[channel.id]
                        channel.remove()
                    del to_remove_channels
                if self.wanted_games:
                    self.change_state(State.CHANNELS_FETCH)
                else:
                    # with no games available, we switch to IDLE after cleanup
                    self.print(_("status", "no_campaign"))
                    self.change_state(State.IDLE)
            elif self._state is State.CHANNELS_FETCH:
                self.gui.status.update(_("gui", "status", "gathering"))
                # start with all current channels, clear the memory and GUI
                new_channels: set[Channel] = set(channels.values())
                channels.clear()
                self.gui.channels.clear()
                # gather and add ACL channels from campaigns
                # NOTE: we consider only campaigns that can be progressed
                # NOTE: we use another set so that we can set them online separately
                no_acl: set[Game] = set()
                acl_channels: set[Channel] = set()
                next_hour = datetime.now(timezone.utc) + timedelta(hours=1)
                for campaign in self.inventory:
                    if (
                        campaign.game in self.wanted_games
                        and campaign.can_earn_within(next_hour)
                    ):
                        if campaign.allowed_channels:
                            acl_channels.update(campaign.allowed_channels)
                        else:
                            no_acl.add(campaign.game)
                # remove all ACL channels that already exist from the other set
                acl_channels.difference_update(new_channels)
                # use the other set to set them online if possible
                await self.bulk_check_online(acl_channels)
                # finally, add them as new channels
                new_channels.update(acl_channels)
                for game in no_acl:
                    # for every campaign without an ACL, for it's game,
                    # add a list of live channels with drops enabled
                    new_channels.update(await self.get_live_streams(game, drops_enabled=True))
                # sort them descending by viewers, by priority and by game priority
                # NOTE: Viewers sort also ensures ONLINE channels are sorted to the top
                # NOTE: We can drop using the set now, because there's no more channels being added
                ordered_channels: list[Channel] = sorted(
                    new_channels, key=self._viewers_key, reverse=True
                )
                ordered_channels.sort(key=lambda ch: ch.acl_based, reverse=True)
                ordered_channels.sort(key=self.get_priority)
                # ensure that we won't end up with more channels than we can handle
                # NOTE: we trim from the end because that's where the non-priority,
                # offline (or online but low viewers) channels end up
                to_remove_channels = ordered_channels[MAX_CHANNELS:]
                ordered_channels = ordered_channels[:MAX_CHANNELS]
                if to_remove_channels:
                    # tracked channels and gui were cleared earlier, so no need to do it here
                    # just make sure to unsubscribe from their topics
                    self._remove_channel_topics(to_remove_channels)
                    del to_remove_channels
                # set our new channel list
                for channel in ordered_channels:
                    channels[channel.id] = channel
                    channel.display(add=True)
                # subscribe to these channel's state updates
                to_add_topics: list[WebsocketTopic] = []
                for channel_id in channels:
                    to_add_topics.append(
                        WebsocketTopic(
                            "Channel", "StreamState", channel_id, self.process_stream_state
                        )
                    )
                    to_add_topics.append(
                        WebsocketTopic(
                            "Channel", "StreamUpdate", channel_id, self.process_stream_update
                        )
                    )
                self.websocket.add_topics(to_add_topics)
                # relink watching channel after cleanup,
                # or stop watching it if it no longer qualifies
                # NOTE: this replaces 'self.watching_channel's internal value with the new object
                watching_channel = self.watching_channel.get_with_default(None)
                if watching_channel is not None:
                    new_watching: Channel | None = channels.get(watching_channel.id)
                    if new_watching is not None and self.can_watch(new_watching):
                        self.watch(new_watching, update_status=False)
                    else:
                        # we've removed a channel we were watching
                        self.stop_watching()
                    del new_watching
                # pre-display the active drop with a substracted minute
                for channel in channels.values():
                    # check if there's any channels we can watch first
                    if self.can_watch(channel):
                        if (
                            (active_campaign := self.get_active_campaign(channel)) is not None
                            and (active_drop := active_campaign.first_drop) is not None
                        ):
                            active_drop.display(countdown=False, subone=True)
                        break
                self.change_state(State.CHANNEL_SWITCH)
                del (
                    no_acl,
                    acl_channels,
                    new_channels,
                    to_add_topics,
                    ordered_channels,
                    watching_channel,
                )
            elif self._state is State.CHANNEL_SWITCH:
                if self.settings.dump:
                    self.gui.close()
                    continue
                self.gui.status.update(_("gui", "status", "switching"))

                # Determine the best channel to watch
                new_watching: Channel | None = None
                selected_channel: Channel | None = self.gui.channels.get_selection()

                if selected_channel is not None and self.can_watch(selected_channel):
                    # User-selected channel takes priority
                    new_watching = selected_channel
                else:
                    # Auto-select best channel based on priority
                    for channel in sorted(channels.values(), key=self.get_priority):
                        if self.can_watch(channel) and self.should_switch(channel):
                            new_watching = channel
                            break

                watching_channel: Channel | None = self.watching_channel.get_with_default(None)

                if new_watching is not None:
                    # Switch to new channel
                    self.watch(new_watching)
                    self._state_change.clear()
                elif watching_channel is not None and self.can_watch(watching_channel):
                    # Continue watching current channel
                    self.gui.status.update(
                        _("status", "watching").format(channel=watching_channel.name)
                    )
                    self._state_change.clear()
                else:
                    # No channels available to watch
                    self.print(_("status", "no_channel"))
                    self.change_state(State.IDLE)
            elif self._state is State.EXIT:
                self.gui.tray.change_icon("pickaxe")
                self.gui.status.update(_("gui", "status", "exiting"))
                # we've been requested to exit the application
                break
            await self._state_change.wait()

    async def _watch_sleep(self, delay: float) -> None:
        # we use wait_for here to allow an asyncio.sleep-like that can be ended prematurely
        self._watching_restart.clear()
        with suppress(asyncio.TimeoutError):
            await asyncio.wait_for(self._watching_restart.wait(), timeout=delay)

    @task_wrapper(critical=True)
    async def _watch_loop(self) -> NoReturn:
        interval: float = WATCH_INTERVAL.total_seconds()
        while True:
            channel: Channel = await self.watching_channel.get()
            if not channel.online:
                # if the channel isn't online anymore, we stop watching it
                self.stop_watching()
                continue
            # logger.log(CALL, f"Sending watch payload to: {channel.name}")
            succeeded: bool = await channel.send_watch()
            last_sent: float = time()
            if not succeeded:
                logger.log(CALL, f"Watch requested failed for channel: {channel.name}")
            # wait ~20 seconds for a progress update
            await asyncio.sleep(20)
            if self.gui.progress.minute_almost_done():
                # If the previous update was more than ~60s ago, and the progress tracker
                # isn't counting down anymore, that means Twitch has temporarily
                # stopped reporting drop's progress. To ensure the timer keeps at least somewhat
                # accurate time, we can use GQL to query for the current drop,
                # or even "pretend" mining as a last resort option.
                handled: bool = False

                # Solution 1: use GQL to query for the currently mined drop status
                try:
                    context = await self.gql_request(
                        GQL_OPERATIONS["CurrentDrop"].with_variables(
                            {"channelID": str(channel.id)}
                        )
                    )
                    assert isinstance(context, dict)
                    drop_data: JsonType | None = (
                        context["data"]["currentUser"]["dropCurrentSession"]  # type: ignore[index]
                    )
                except GQLException:
                    drop_data = None
                if drop_data is not None:
                    gql_drop: TimedDrop | None = self._drops.get(drop_data["dropID"])
                    if gql_drop is not None and gql_drop.can_earn(channel):
                        gql_drop.update_minutes(drop_data["currentMinutesWatched"])
                        drop_text: str = (
                            f"{gql_drop.name} ({gql_drop.campaign.game}, "
                            f"{gql_drop.current_minutes}/{gql_drop.required_minutes})"
                        )
                        logger.log(CALL, f"Drop progress from GQL: {drop_text}")
                        handled = True

                # Solution 2: If GQL fails, figure out which campaign we're most likely mining
                # right now, and then bump up the minutes on it's drops
                if not handled:
                    if (active_campaign := self.get_active_campaign(channel)) is not None:
                        active_campaign.bump_minutes(channel)
                        # NOTE: This usually gets overwritten below
                        drop_text = f"Unknown drop ({active_campaign.game})"
                        if (active_drop := active_campaign.first_drop) is not None:
                            active_drop.display()
                            drop_text = (
                                f"{active_drop.name} ({active_drop.campaign.game}, "
                                f"{active_drop.current_minutes}/{active_drop.required_minutes})"
                            )
                        logger.log(CALL, f"Drop progress from active search: {drop_text}")
                        handled = True
                    else:
                        logger.log(CALL, "No active drop could be determined")
            await self._watch_sleep(interval - min(time() - last_sent, interval))

    @task_wrapper(critical=True)
    async def _maintenance_task(self) -> None:
        now = datetime.now(timezone.utc)
        next_period = now + timedelta(minutes=1)
        while True:
            # exit if there's no need to repeat the loop
            now = datetime.now(timezone.utc)
            if now >= next_period:
                break
            next_trigger = next_period
            while self._mnt_triggers and self._mnt_triggers[0] <= next_trigger:
                next_trigger = self._mnt_triggers.popleft()
            trigger_type: str = "Reload" if next_trigger == next_period else "Cleanup"
            logger.log(
                CALL,
                (
                    "Maintenance task waiting until: "
                    f"{next_trigger.astimezone().strftime('%X')} ({trigger_type})"
                )
            )
            await asyncio.sleep((next_trigger - now).total_seconds())
            # exit after waiting, before the actions
            now = datetime.now(timezone.utc)
            if now >= next_period:
                break
            if next_trigger != next_period:
                logger.log(CALL, "Maintenance task requests channels cleanup")
                self.change_state(State.CHANNELS_CLEANUP)
        # this triggers a restart of this task every (up to) 60 minutes
        logger.log(CALL, "Maintenance task requests a reload")
        self.change_state(State.INVENTORY_FETCH)

    def can_watch(self, channel: Channel) -> bool:
        """
        Determines if the given channel qualifies as a watching candidate.
        """
        if not self.wanted_games:
            return False
        # exit early if stream is offline or drops aren't enabled
        if not channel.online or not channel.drops_enabled:
            return False
        # check if we can progress any campaign for the played game
        if channel.game is None or channel.game not in self.wanted_games:
            return False
        for campaign in self.inventory:
            if campaign.can_earn(channel):
                return True
        return False

    def should_switch(self, channel: Channel) -> bool:
        """
        Determines if the given channel qualifies as a switch candidate.
        """
        watching_channel = self.watching_channel.get_with_default(None)
        if watching_channel is None:
            return True
        channel_order = self.get_priority(channel)
        watching_order = self.get_priority(watching_channel)
        return (
            # this channel's game is higher order than the watching one's
            channel_order < watching_order
            or channel_order == watching_order  # or the order is the same
            # and this channel is ACL-based and the watching channel isn't
            and channel.acl_based > watching_channel.acl_based
        )

    def watch(self, channel: Channel, *, update_status: bool = True) -> None:
        """Start watching a specific channel."""
        self.gui.tray.change_icon("active")
        self.gui.channels.set_watching(channel)
        self.watching_channel.set(channel)
        if update_status:
            status_text: str = _("status", "watching").format(channel=channel.name)
            self.print(status_text)
            self.gui.status.update(status_text)

    def stop_watching(self) -> None:
        """Stop watching the current channel."""
        self.gui.clear_drop()
        self.watching_channel.clear()
        self.gui.channels.clear_watching()

    def restart_watching(self) -> None:
        """Restart the watch loop (forces immediate re-send of watch payload)."""
        self.gui.progress.stop_timer()
        self._watching_restart.set()

    @task_wrapper
    async def process_stream_state(self, channel_id: int, message: JsonType) -> None:
        """Process websocket stream state updates (viewcount, stream-up, stream-down)."""
        msg_type: str = message["type"]
        channel: Channel | None = self.channels.get(channel_id)
        if channel is None:
            logger.error(f"Stream state change for a non-existing channel: {channel_id}")
            return
        if msg_type == "viewcount":
            if not channel.online:
                # if it's not online for some reason, set it so
                channel.check_online()
            else:
                viewers = message["viewers"]
                channel.viewers = viewers
                channel.display()
                # logger.debug(f"{channel.name} viewers: {viewers}")
        elif msg_type == "stream-down":
            channel.set_offline()
        elif msg_type == "stream-up":
            channel.check_online()
        elif msg_type == "commercial":
            # skip these
            pass
        else:
            logger.warning(f"Unknown stream state: {msg_type}")

    @task_wrapper
    async def process_stream_update(self, channel_id: int, message: JsonType) -> None:
        """Process websocket broadcast settings updates (game/title changes)."""
        # message = {
        #     "channel_id": "12345678",
        #     "type": "broadcast_settings_update",
        #     "channel": "channel._login",
        #     "old_status": "Old title",
        #     "status": "New title",
        #     "old_game": "Old game name",
        #     "game": "New game name",
        #     "old_game_id": 123456,
        #     "game_id": 123456
        # }
        channel: Channel | None = self.channels.get(channel_id)
        if channel is None:
            logger.error(f"Broadcast settings update for a non-existing channel: {channel_id}")
            return
        if message["old_game"] != message["game"]:
            game_change = f", game changed: {message['old_game']} -> {message['game']}"
        else:
            game_change = ''
        logger.log(CALL, f"Channel update from websocket: {channel.name}{game_change}")
        # There's no information about channel tags here, but this event is triggered
        # when the tags change. We can use this to just update the stream data after the change.
        # Use 'check_online' to introduce a delay, allowing for multiple title and tags
        # changes before we update. This eventually calls 'on_channel_update' below.
        channel.check_online()

    def on_channel_update(
        self, channel: Channel, stream_before: Stream | None, stream_after: Stream | None
    ) -> None:
        """
        Called by a Channel when its status is updated (ONLINE, OFFLINE, title/tags change).

        NOTE: 'stream_before' gets deallocated once this function finishes.
        """
        watching_channel: Channel | None = self.watching_channel.get_with_default(None)
        is_watching_this: bool = watching_channel is not None and watching_channel == channel

        # Channel going from OFFLINE to ONLINE
        if stream_before is None and stream_after is not None:
            if self.can_watch(channel) and self.should_switch(channel):
                self.print(_("status", "goes_online").format(channel=channel.name))
                self.watch(channel)
            else:
                logger.info(f"{channel.name} goes ONLINE")

        # Channel going from ONLINE to OFFLINE
        elif stream_before is not None and stream_after is None:
            if is_watching_this:
                self.print(_("status", "goes_offline").format(channel=channel.name))
                self.change_state(State.CHANNEL_SWITCH)
            else:
                logger.info(f"{channel.name} goes OFFLINE")

        # Channel staying ONLINE but with updates
        elif stream_before is not None and stream_after is not None:
            drops_status: str = (
                f"(🎁: {stream_before.drops_enabled and '✔' or '❌'} -> "
                f"{stream_after.drops_enabled and '✔' or '❌'})"
            )

            if is_watching_this and not self.can_watch(channel):
                # Watching this channel but can't watch it anymore
                logger.info(f"{channel.name} status updated, switching... {drops_status}")
                self.change_state(State.CHANNEL_SWITCH)
            elif not is_watching_this:
                # Not watching this channel
                logger.info(f"{channel.name} status updated {drops_status}")
                if self.can_watch(channel) and self.should_switch(channel):
                    self.watch(channel)

        # Channel was OFFLINE and stays OFFLINE
        else:
            logger.log(CALL, f"{channel.name} stays OFFLINE")

        channel.display()

    @task_wrapper
    async def process_drops(self, user_id: int, message: JsonType) -> None:
        """Process websocket drop progress and claim updates."""
        # Message examples:
        # {"type": "drop-progress", data: {"current_progress_min": 3, "required_progress_min": 10}}
        # {"type": "drop-claim", data: {"drop_instance_id": ...}}
        msg_type: str = message["type"]
        if msg_type not in ("drop-progress", "drop-claim"):
            return
        drop_id: str = message["data"]["drop_id"]
        drop: TimedDrop | None = self._drops.get(drop_id)
        watching_channel: Channel | None = self.watching_channel.get_with_default(None)
        if msg_type == "drop-claim":
            if drop is None:
                logger.error(
                    f"Received a drop claim ID for a non-existing drop: {drop_id}\n"
                    f"Drop claim ID: {message['data']['drop_instance_id']}"
                )
                return
            drop.update_claim(message["data"]["drop_instance_id"])
            campaign = drop.campaign
            await drop.claim()
            drop.display()
            # About 4-20s after claiming the drop, next drop can be started
            # by re-sending the watch payload. We can test for it by fetching the current drop
            # via GQL, and then comparing drop IDs.
            await asyncio.sleep(4)
            if watching_channel is not None:
                for attempt in range(8):
                    context = await self.gql_request(
                        GQL_OPERATIONS["CurrentDrop"].with_variables(
                            {"channelID": str(watching_channel.id)}
                        )
                    )
                    assert isinstance(context, dict)
                    drop_data: JsonType | None = (
                        context["data"]["currentUser"]["dropCurrentSession"]  # type: ignore[index]
                    )
                    if drop_data is None or drop_data["dropID"] != drop.id:
                        break
                    await asyncio.sleep(2)
            if campaign.can_earn(watching_channel):
                self.restart_watching()
            else:
                self.change_state(State.INVENTORY_FETCH)
            return
        assert msg_type == "drop-progress"
        if drop is not None:
            drop_text = (
                f"{drop.name} ({drop.campaign.game}, "
                f"{message['data']['current_progress_min']}/"
                f"{message['data']['required_progress_min']})"
            )
        else:
            drop_text = "<Unknown>"
        logger.log(CALL, f"Drop update from websocket: {drop_text}")
        if drop is not None and drop.can_earn(self.watching_channel.get_with_default(None)):
            # the received payload is for the drop we expected
            drop.update_minutes(message["data"]["current_progress_min"])

    @task_wrapper
    async def process_notifications(self, user_id: int, message: JsonType) -> None:
        """Process websocket notification updates."""
        if message["type"] == "create-notification":
            data: JsonType = message["data"]["notification"]
            if data["type"] == "user_drop_reward_reminder_notification":
                self.change_state(State.INVENTORY_FETCH)
                await self.gql_request(
                    GQL_OPERATIONS["NotificationsDelete"].with_variables(
                        {"input": {"id": data["id"]}}
                    )
                )

    async def get_auth(self) -> _AuthState:
        """Get authentication state (validates token if needed)."""
        await self._auth_state.validate()
        return self._auth_state

    async def gql_request(
        self, ops: GQLOperation | list[GQLOperation]
    ) -> JsonType | list[JsonType]:
        """
        Execute GraphQL request(s).

        Delegates to GQLClient for execution.
        """
        self._ensure_api_clients()
        assert self._gql_client is not None
        return await self._gql_client.request(ops)

    async def fetch_campaigns(
        self, campaigns_chunk: list[tuple[str, JsonType]]
    ) -> dict[str, JsonType]:
        campaign_ids: dict[str, JsonType] = dict(campaigns_chunk)
        auth_state = await self.get_auth()
        response_list_raw = await self.gql_request(
            [
                GQL_OPERATIONS["CampaignDetails"].with_variables(
                    {"channelLogin": str(auth_state.user_id), "dropID": cid}
                )
                for cid in campaign_ids
            ]
        )
        # Ensure we have a list
        response_list: list[JsonType] = (
            response_list_raw if isinstance(response_list_raw, list) else [response_list_raw]
        )
        fetched_data: dict[str, JsonType] = {
            (campaign_data := response_json["data"]["user"]["dropCampaign"])["id"]: campaign_data  # type: ignore[index]
            for response_json in response_list
        }
        return GQLClient.merge_data(campaign_ids, fetched_data)

    async def fetch_inventory(self) -> None:
        status_update = self.gui.status.update
        status_update(_("gui", "status", "fetching_inventory"))
        # fetch in-progress campaigns (inventory)
        response = await self.gql_request(GQL_OPERATIONS["Inventory"])
        assert isinstance(response, dict)
        inventory: JsonType = response["data"]["currentUser"]["inventory"]  # type: ignore[index]
        ongoing_campaigns: list[JsonType] = inventory["dropCampaignsInProgress"] or []
        # this contains claimed benefit edge IDs, not drop IDs
        claimed_benefits: dict[str, datetime] = {
            b["id"]: isoparse(b["lastAwardedAt"]) for b in inventory["gameEventDrops"]
        }
        inventory_data: dict[str, JsonType] = {c["id"]: c for c in ongoing_campaigns}
        # fetch general available campaigns data (campaigns)
        response = await self.gql_request(GQL_OPERATIONS["Campaigns"])
        assert isinstance(response, dict)
        available_list: list[JsonType] = response["data"]["currentUser"]["dropCampaigns"] or []  # type: ignore[index]
        applicable_statuses = ("ACTIVE", "UPCOMING")
        available_campaigns: dict[str, JsonType] = {
            c["id"]: c
            for c in available_list
            if c["status"] in applicable_statuses  # that are currently not expired
        }
        # fetch detailed data for each campaign, in chunks
        status_update(_("gui", "status", "fetching_campaigns"))
        fetch_campaigns_tasks: list[asyncio.Task[Any]] = [
            asyncio.create_task(self.fetch_campaigns(campaigns_chunk))
            for campaigns_chunk in chunk(available_campaigns.items(), 20)
        ]
        try:
            for coro in asyncio.as_completed(fetch_campaigns_tasks):
                chunk_campaigns_data = await coro
                # merge the inventory and campaigns datas together
                inventory_data = GQLClient.merge_data(inventory_data, chunk_campaigns_data)
        except Exception:
            # asyncio.as_completed doesn't cancel tasks on errors
            for task in fetch_campaigns_tasks:
                task.cancel()
            raise
        # filter out invalid campaigns
        for campaign_id in list(inventory_data.keys()):
            if inventory_data[campaign_id]["game"] is None:
                del inventory_data[campaign_id]

        if self.settings.dump:
            # dump the campaigns data to the dump file
            with open(DUMP_PATH, 'a', encoding="utf8") as file:
                # we need to pre-process the inventory dump a little
                dump_data: JsonType = deepcopy(inventory_data)
                for campaign_data in dump_data.values():
                    # replace ACL lists with a simple text description
                    if (
                        campaign_data["allow"]
                        and campaign_data["allow"].get("isEnabled", True)
                        and campaign_data["allow"]["channels"]
                    ):
                        # simply count the channels included in the ACL
                        campaign_data["allow"]["channels"] = (
                            f"{len(campaign_data['allow']['channels'])} channels"
                        )
                    # replace drop instance IDs, so they don't include user IDs
                    for drop_data in campaign_data["timeBasedDrops"]:
                        if "self" in drop_data and drop_data["self"]["dropInstanceID"]:
                            drop_data["self"]["dropInstanceID"] = "..."
                json.dump(dump_data, file, indent=4, sort_keys=True)
                file.write("\n\n")  # add 2x new line spacer
                json.dump(claimed_benefits, file, indent=4, sort_keys=True, default=str)

        # use the merged data to create campaign objects
        campaigns: list[DropsCampaign] = [
            DropsCampaign(self, campaign_data, claimed_benefits)
            for campaign_data in inventory_data.values()
        ]
        campaigns.sort(key=lambda c: c.active, reverse=True)
        campaigns.sort(key=lambda c: c.upcoming and c.starts_at or c.ends_at)
        campaigns.sort(key=lambda c: c.eligible, reverse=True)

        self._drops.clear()
        self.gui.inv.clear()
        self.inventory.clear()
        self._mnt_triggers.clear()
        switch_triggers: set[datetime] = set()
        next_hour = datetime.now(timezone.utc) + timedelta(hours=1)
        # add the campaigns to the internal inventory
        for campaign in campaigns:
            self._drops.update({drop.id: drop for drop in campaign.drops})
            if campaign.can_earn_within(next_hour):
                switch_triggers.update(campaign.time_triggers)
            self.inventory.append(campaign)
            self._campaigns[campaign.id] = campaign
        # concurrently add the campaigns into the GUI
        # NOTE: this fetches pictures from the CDN, so might be slow without a cache
        status_update(
            _("gui", "status", "adding_campaigns").format(counter=f"(0/{len(campaigns)})")
        )
        add_campaign_tasks: list[asyncio.Task[None]] = [
            asyncio.create_task(self.gui.inv.add_campaign(campaign))
            for campaign in campaigns
        ]
        try:
            for i, coro in enumerate(asyncio.as_completed(add_campaign_tasks), start=1):
                await coro
                status_update(
                    _("gui", "status", "adding_campaigns").format(
                        counter=f"({i}/{len(campaigns)})"
                    )
                )
                # this is needed here explicitly, because cache reads from disk don't raise this
                if self.gui.close_requested:
                    raise ExitRequest()
        except Exception:
            # asyncio.as_completed doesn't cancel tasks on errors
            for task in add_campaign_tasks:
                task.cancel()
            raise
        self._mnt_triggers.extend(sorted(switch_triggers))
        # trim out all triggers that we're already past
        now = datetime.now(timezone.utc)
        while self._mnt_triggers and self._mnt_triggers[0] <= now:
            self._mnt_triggers.popleft()
        # NOTE: maintenance task is restarted at the end of each inventory fetch
        if self._mnt_task is not None and not self._mnt_task.done():
            self._mnt_task.cancel()
        self._mnt_task = asyncio.create_task(self._maintenance_task())

    def get_active_campaign(self, channel: Channel | None = None) -> DropsCampaign | None:
        if not self.wanted_games:
            return None
        watching_channel = self.watching_channel.get_with_default(channel)
        if watching_channel is None:
            # if we aren't watching anything, we can't earn any drops
            return None
        campaigns: list[DropsCampaign] = []
        for campaign in self.inventory:
            if campaign.can_earn(watching_channel):
                campaigns.append(campaign)
        if campaigns:
            campaigns.sort(key=lambda c: c.remaining_minutes)
            return campaigns[0]
        return None

    async def get_live_streams(
        self, game: Game, *, limit: int = 20, drops_enabled: bool = True
    ) -> list[Channel]:
        filters: list[str] = []
        if drops_enabled:
            filters.append("DROPS_ENABLED")
        try:
            response = await self.gql_request(
                GQL_OPERATIONS["GameDirectory"].with_variables({
                    "limit": limit,
                    "slug": game.slug,
                    "options": {
                        "includeRestricted": ["SUB_ONLY_LIVE"],
                        "systemFilters": filters,
                    },
                })
            )
        except GQLException as exc:
            raise MinerException(f"Game: {game.slug}") from exc
        assert isinstance(response, dict)
        if "game" in response["data"]:  # type: ignore[operator]
            return [
                Channel.from_directory(
                    self, stream_channel_data["node"], drops_enabled=drops_enabled
                )
                for stream_channel_data in response["data"]["game"]["streams"]["edges"]  # type: ignore[index]
                if stream_channel_data["node"]["broadcaster"] is not None
            ]
        return []

    async def bulk_check_online(self, channels: abc.Iterable[Channel]):
        """
        Utilize batch GQL requests to check ONLINE status for a lot of channels at once.
        Also handles the drops_enabled check.
        """
        acl_streams_map: dict[int, JsonType] = {}
        stream_gql_ops: list[GQLOperation] = [channel.stream_gql for channel in channels]
        if not stream_gql_ops:
            # shortcut for nothing to process
            # NOTE: Have to do this here, becase "channels" can be any iterable
            return
        stream_gql_tasks: list[asyncio.Task[JsonType | list[JsonType]]] = [
            asyncio.create_task(self.gql_request(stream_gql_chunk))
            for stream_gql_chunk in chunk(stream_gql_ops, 20)
        ]
        try:
            for coro in asyncio.as_completed(stream_gql_tasks):
                response = await coro
                response_list: list[JsonType] = response if isinstance(response, list) else [response]
                for response_json in response_list:
                    channel_data: JsonType = response_json["data"]["user"]  # type: ignore[index]
                    if channel_data is not None:
                        acl_streams_map[int(channel_data["id"])] = channel_data
        except Exception:
            # asyncio.as_completed doesn't cancel tasks on errors
            for task in stream_gql_tasks:
                task.cancel()
            raise
        # for all channels with an active stream, check the available drops as well
        # acl_available_drops_map: dict[int, list[JsonType]] = {}
        # available_gql_ops: list[GQLOperation] = [
        #     GQL_OPERATIONS["AvailableDrops"].with_variables({"channelID": str(channel_id)})
        #     for channel_id, channel_data in acl_streams_map.items()
        #     if channel_data["stream"] is not None  # only do this for ONLINE channels
        # ]
        # available_gql_tasks: list[asyncio.Task[list[JsonType]]] = [
        #     asyncio.create_task(self.gql_request(available_gql_chunk))
        #     for available_gql_chunk in chunk(available_gql_ops, 20)
        # ]
        # try:
        #     for coro in asyncio.as_completed(available_gql_tasks):
        #         response_list = await coro
        #         for response_json in response_list:
        #             available_info: JsonType = response_json["data"]["channel"]
        #             acl_available_drops_map[int(available_info["id"])] = (
        #                 available_info["viewerDropCampaigns"] or []
        #             )
        # except Exception:
        #     # asyncio.as_completed doesn't cancel tasks on errors
        #     for task in available_gql_tasks:
        #         task.cancel()
        #     raise
        for channel in channels:
            channel_id = channel.id
            if channel_id not in acl_streams_map:
                continue
            channel_data = acl_streams_map[channel_id]
            if channel_data["stream"] is None:
                continue
            # available_drops: list[JsonType] = acl_available_drops_map[channel_id]
            # channel.external_update(channel_data, available_drops)
            channel.external_update(channel_data, [])
