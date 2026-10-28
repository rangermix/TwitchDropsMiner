from __future__ import annotations

import re
from copy import deepcopy
from dataclasses import dataclass
from typing import Any, Literal, TypedDict, cast

from yarl import URL

from src.config import DEFAULT_LANG, SETTINGS_PATH
from src.config.reward_priority import RewardPriority
from src.utils import DropIgnorePolicy, json_load, json_save, merge_json


class InventoryFilters(TypedDict):
    game_name_search: list[str]
    show_active: bool
    show_benefit_badge: bool
    show_benefit_emote: bool
    show_benefit_item: bool
    show_benefit_other: bool
    show_expired: bool
    show_finished: bool
    link_status: Literal["all", "linked", "not_linked"]
    show_upcoming: bool


default_settings = {
    "allow_unlinked_campaigns": False,
    "connection_quality": 1,
    "dark_mode": False,
    "discord_webhook_url": "",
    "drop_name_blacklist": [],
    "games_to_watch": [],
    "language": DEFAULT_LANG,
    "priority_list_only": True,
    "reward_priority": [],
    "inventory_filters": {
        "game_name_search": [],
        "show_active": False,
        "show_benefit_badge": True,
        "show_benefit_emote": True,
        "show_benefit_item": True,
        "show_benefit_other": True,
        "show_expired": False,
        "show_finished": False,
        "link_status": "all",
        "show_upcoming": True,
    },
    "inventory_list_view": False,
    "minimum_refresh_interval_minutes": 30,
    "mining_benefits": {
        "BADGE": True,
        "DIRECT_ENTITLEMENT": True,
        "EMOTE": True,
        "UNKNOWN": True,
    },
    "mining_hours": {
        "mode": "always",
        "start": "08:00",
        "end": "22:00",
    },
    "proxy": "",
    "telegram_bot_token": "",
    "telegram_chat_id": "",
}


class MiningHoursSettings:
    """Validate the complete daily schedule before it changes live policy."""

    @staticmethod
    def normalize(value: Any) -> dict[str, str]:
        if (
            not isinstance(value, dict)
            or set(value) != {"mode", "start", "end"}
            or value.get("mode") not in ("always", "range")
            or any(
                not isinstance(value.get(key), str)
                or re.fullmatch(r"(?:[01][0-9]|2[0-3]):[0-5][0-9]", value[key]) is None
                for key in ("start", "end")
            )
        ):
            raise ValueError("Invalid mining hours")
        return dict(value)


class InventoryFilterSettings:
    """Normalize display filters and migrate the previous restrictive checkbox."""

    LINK_STATUSES = ("all", "linked", "not_linked")

    @classmethod
    def validate_update(cls, updates: Any) -> dict[str, Any]:
        """Reject a filter update that is not an object or names an unknown link mode."""
        if not isinstance(updates, dict) or (
            "link_status" in updates and updates["link_status"] not in cls.LINK_STATUSES
        ):
            raise ValueError("Invalid inventory filters")
        return updates

    @classmethod
    def normalize(
        cls, updates: dict[str, Any], current: InventoryFilters | None = None
    ) -> InventoryFilters:
        values: dict[str, Any] = deepcopy(dict(current or {}))
        if "link_status" not in updates and "show_only_not_linked" in updates:
            values["link_status"] = (
                "not_linked" if updates["show_only_not_linked"] is True else "all"
            )
        values.update(updates)
        if values.get("link_status") not in cls.LINK_STATUSES:
            values["link_status"] = "all"
        template = default_settings["inventory_filters"]
        assert isinstance(template, dict)
        merge_json(values, template)
        return cast(InventoryFilters, values)


@dataclass
class Settings:
    allow_unlinked_campaigns: bool
    connection_quality: int
    dark_mode: bool
    discord_webhook_url: str
    drop_name_blacklist: list[str]
    games_to_watch: list[str]
    language: str
    reward_priority: list[str]
    priority_list_only: bool
    inventory_filters: InventoryFilters
    inventory_list_view: bool
    minimum_refresh_interval_minutes: int
    mining_benefits: dict[str, bool]
    mining_hours: dict[str, str]
    proxy: str
    telegram_bot_token: str
    telegram_chat_id: str

    def __init__(self):
        self.load()

    def load(self):
        # TODO: remvoe customized serde in the future
        # Migrate filters before the generic merge discards their previous key.
        settings = json_load(SETTINGS_PATH, deepcopy(default_settings), merge=False)
        filters = settings.get("inventory_filters")
        settings["inventory_filters"] = InventoryFilterSettings.normalize(
            filters if isinstance(filters, dict) else {}
        )
        if "mining_hours" in settings:
            settings["mining_hours"] = MiningHoursSettings.normalize(settings["mining_hours"])
        settings["reward_priority"] = RewardPriority.normalize(settings.get("reward_priority", []))
        merge_json(settings, default_settings)
        settings.pop("allow_helper_connection", None)
        for key, value in settings.items():
            if value is URL:
                setattr(self, key, str(value))
            else:
                setattr(self, key, value)
        self.drop_name_blacklist = DropIgnorePolicy.normalize_keywords(
            self.drop_name_blacklist
        )

    def save(self) -> None:
        self.reward_priority = RewardPriority.normalize(self.reward_priority)
        self.mining_hours = MiningHoursSettings.normalize(self.mining_hours)
        self.drop_name_blacklist = DropIgnorePolicy.normalize_keywords(
            self.drop_name_blacklist
        )
        values = vars(self).copy()
        json_save(SETTINGS_PATH, values, sort=True)
