"""
Inventory service for managing campaigns, drops, and inventory fetching.

This service handles fetching campaign data from Twitch's GraphQL API,
managing the inventory state, and determining active campaigns.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Any

from dateutil.parser import isoparse

from src.api import GQLClient
from src.config import GQL_OPERATIONS
from src.exceptions import ExitRequest
from src.i18n import _
from src.models import DropsCampaign
from src.utils import chunk


if TYPE_CHECKING:
    from src.config import JsonType
    from src.core.client import Twitch
    from src.models.channel import Channel


logger = logging.getLogger("TwitchDrops")


class InventoryService:
    """
    Service responsible for inventory and campaign management.

    Handles:
    - Fetching campaign details from GraphQL
    - Fetching inventory (in-progress campaigns)
    - Determining active campaign for a channel
    - Managing campaign data and claimed benefits
    """

    def __init__(self, twitch: Twitch) -> None:
        """
        Initialize the inventory service.

        Args:
            twitch: The Twitch client instance
        """
        self._twitch = twitch

    async def fetch_campaigns(
        self, campaigns_chunk: list[tuple[str, JsonType]]
    ) -> dict[str, JsonType]:
        """
        Fetch detailed campaign data for a chunk of campaign IDs.

        Args:
            campaigns_chunk: List of (campaign_id, campaign_data) tuples

        Returns:
            Dictionary mapping campaign IDs to their detailed data
        """
        campaign_ids: dict[str, JsonType] = dict(campaigns_chunk)
        auth_state = await self._twitch.get_auth()

        response_list_raw = await self._twitch.gql_request(
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
            (campaign_data := response_json["data"]["user"]["dropCampaign"])["id"]: campaign_data
            for response_json in response_list
        }

        return GQLClient.merge_data(campaign_ids, fetched_data)

    async def fetch_inventory(self) -> None:
        """
        Fetch the complete inventory including campaigns and drops.

        This method:
        1. Fetches in-progress campaigns (inventory)
        2. Fetches available campaigns
        3. Fetches detailed data for each campaign
        4. Creates DropsCampaign objects
        5. Updates GUI with campaign information
        6. Sets up maintenance triggers for campaign timing changes
        """
        status_update = self._twitch.gui.status.update
        status_update(_("gui", "status", "fetching_inventory"))

        # fetch in-progress campaigns (inventory)
        response = await self._twitch.gql_request(GQL_OPERATIONS["Inventory"])
        inventory: JsonType = response["data"]["currentUser"]["inventory"]
        ongoing_campaigns: list[JsonType] = inventory["dropCampaignsInProgress"] or []

        # this contains claimed benefit edge IDs, not drop IDs
        claimed_benefits: dict[str, datetime] = {
            b["id"]: isoparse(b["lastAwardedAt"]) for b in inventory["gameEventDrops"]
        }

        inventory_data: dict[str, JsonType] = {c["id"]: c for c in ongoing_campaigns}

        # fetch general available campaigns data (campaigns)
        response = await self._twitch.gql_request(GQL_OPERATIONS["Campaigns"])
        available_list: list[JsonType] = response["data"]["currentUser"]["dropCampaigns"] or []
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

        # use the merged data to create campaign objects
        campaigns: list[DropsCampaign] = [
            DropsCampaign(self._twitch, campaign_data, claimed_benefits)
            for campaign_data in inventory_data.values()
        ]
        campaigns.sort(key=lambda c: c.active, reverse=True)
        campaigns.sort(key=lambda c: c.upcoming and c.starts_at or c.ends_at)
        campaigns.sort(key=lambda c: c.eligible, reverse=True)

        self._twitch._drops.clear()
        self._twitch.gui.inv.clear()
        self._twitch.inventory.clear()
        self._twitch._mnt_triggers.clear()
        switch_triggers: set[datetime] = set()
        next_hour = datetime.now(timezone.utc) + timedelta(hours=1)

        # add the campaigns to the internal inventory
        for campaign in campaigns:
            self._twitch._drops.update({drop.id: drop for drop in campaign.drops})
            if campaign.can_earn_within(next_hour):
                switch_triggers.update(campaign.time_triggers)
            self._twitch.inventory.append(campaign)
            self._twitch._campaigns[campaign.id] = campaign

        # concurrently add the campaigns into the GUI
        # NOTE: this fetches pictures from the CDN, so might be slow without a cache
        status_update(
            _("gui", "status", "adding_campaigns").format(counter=f"(0/{len(campaigns)})")
        )
        add_campaign_tasks: list[asyncio.Task[None]] = [
            asyncio.create_task(self._twitch.gui.inv.add_campaign(campaign))
            for campaign in campaigns
        ]

        try:
            for i, coro in enumerate(asyncio.as_completed(add_campaign_tasks), start=1):
                await coro
                status_update(
                    _("gui", "status", "adding_campaigns").format(counter=f"({i}/{len(campaigns)})")
                )
                # this is needed here explicitly, because cache reads from disk don't raise this
                from src.config import State

                if self._twitch._state == State.EXIT:
                    raise ExitRequest()
        except Exception:
            # asyncio.as_completed doesn't cancel tasks on errors
            for task in add_campaign_tasks:
                task.cancel()
            raise

        self._twitch._mnt_triggers.extend(sorted(switch_triggers))

        # trim out all triggers that we're already past
        now = datetime.now(timezone.utc)
        while self._twitch._mnt_triggers and self._twitch._mnt_triggers[0] <= now:
            self._twitch._mnt_triggers.popleft()

        # NOTE: maintenance task is restarted at the end of each inventory fetch
        if self._twitch._mnt_task is not None and not self._twitch._mnt_task.done():
            self._twitch._mnt_task.cancel()
        self._twitch._mnt_task = asyncio.create_task(
            self._twitch._maintenance_service.run_maintenance_task()
        )

    def get_active_campaign(self, channel: Channel | None = None) -> DropsCampaign | None:
        """
        Determine the active campaign for a given channel (or watching channel).

        Returns the campaign with the least remaining minutes that can be earned
        on the specified channel. This is used to determine which drop is actively
        being progressed.

        Args:
            channel: The channel to check (defaults to watching channel)

        Returns:
            The active DropsCampaign, or None if no campaign can be earned
        """
        if not self._twitch.wanted_games:
            return None

        watching_channel = self._twitch.watching_channel.get_with_default(channel)
        if watching_channel is None:
            # if we aren't watching anything, we can't earn any drops
            return None

        campaigns: list[DropsCampaign] = []
        for campaign in self._twitch.inventory:
            if campaign.can_earn(watching_channel):
                campaigns.append(campaign)

        if campaigns:
            campaigns.sort(key=lambda c: c.remaining_minutes)
            return campaigns[0]

        return None
