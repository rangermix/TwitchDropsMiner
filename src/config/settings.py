from __future__ import annotations

import contextlib
import logging
import re
from copy import deepcopy
from dataclasses import dataclass
from typing import Any, ClassVar, Literal, TypedDict, cast

from yarl import URL

from src.config import DEFAULT_LANG, SETTINGS_PATH
from src.config.reward_priority import RewardPriority
from src.utils import DropIgnorePolicy, json_load, json_save, merge_json


logger = logging.getLogger("TwitchDrops")


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
    "claim_channel_points": True,
    # off by default: a share posts a visible notice in the streamer's chat under the user's name
    "share_watch_streaks": False,
    "games_to_watch": [],
    "idle_channels": [],
    # channel points options of idle watching (PointsSettings); actions that spend points or are
    # visible to others are off by default
    "idle_priority": "list",
    "idle_streak_first": True,
    "streak_recovery": True,
    "follow_raids": False,
    "predictions_enabled": False,
    "prediction_strategy": "smart",
    "prediction_percent": 5,
    "prediction_max_points": 1000,
    "prediction_min_balance": 0,
    "prediction_min_voters": 0,
    "prediction_min_odds": 1.0,
    "prediction_timing": "before_close",
    "prediction_delay_seconds": 6,
    "prediction_timer_percent": 50,
    "prediction_fixed_option": 1,
    "claim_moments": True,
    "join_chat": False,
    "language": DEFAULT_LANG,
    "last_notified_releases": {"telegram": "", "discord": ""},
    "prefer_followed_channels": False,
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
    "mining_opt_ins_revision": 0,
    "notification_events": {
        "drop_claimed": True,
        "new_release": False,
    },
    "notification_events_revision": 0,
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


class IdleChannelSettings:
    """Validate the channels watched when nothing can be mined."""

    LOGIN = re.compile(r"[a-z0-9_]{1,25}")
    MAX_CHANNELS = 20

    @classmethod
    def normalize(cls, value: Any) -> list[str]:
        # ASCII is checked before lower(): the Kelvin sign would lowercase to an ASCII "k".
        if type(value) is not list or any(type(item) is not str or not item.isascii() for item in value):
            raise ValueError("invalid_idle_channels")
        logins = list(dict.fromkeys(item.strip().lower() for item in value))
        if len(logins) > cls.MAX_CHANNELS or any(cls.LOGIN.fullmatch(login) is None for login in logins):
            raise ValueError("invalid_idle_channels")
        return logins

    @classmethod
    def salvage(cls, value: Any) -> list[str]:
        """Keep the valid entries of a hand-edited list, by the normalize rules, capped."""
        logins: list[str] = []
        for item in value if type(value) is list else []:
            with contextlib.suppress(ValueError):
                logins += cls.normalize([item])
        return list(dict.fromkeys(logins))[: cls.MAX_CHANNELS]


class PointsSettings:
    """Validate the channel points options of idle watching."""

    CHOICES: ClassVar[dict[str, tuple[str, ...]]] = {
        "idle_priority": ("list", "points_low", "points_high", "subscribed"),
        "prediction_strategy": ("smart", "most_voted", "high_odds", "favourite", "fixed"),
        "prediction_timing": ("before_close", "after_open", "percent"),
    }
    # inclusive bounds; Twitch takes 10..250000 points per prediction
    RANGES: ClassVar[dict[str, tuple[int, int]]] = {
        "prediction_percent": (1, 100),
        "prediction_max_points": (10, 250000),
        "prediction_min_balance": (0, 10_000_000),
        "prediction_min_voters": (0, 1_000_000),
        "prediction_delay_seconds": (0, 3600),
        "prediction_timer_percent": (1, 99),
        "prediction_fixed_option": (1, 10),
    }
    # inclusive bounds of the options that take an int or a float
    NUMBERS: ClassVar[dict[str, tuple[float, float]]] = {"prediction_min_odds": (1.0, 100.0)}
    FLAGS = ("idle_streak_first", "streak_recovery", "follow_raids", "predictions_enabled", "claim_moments", "join_chat")
    KEYS = (*CHOICES, *RANGES, *NUMBERS, *FLAGS)

    @classmethod
    def normalize(cls, key: str, value: Any) -> Any:
        """Return value when it is valid for key; a bool is never an int here."""
        if key in cls.CHOICES:
            valid = type(value) is str and value in cls.CHOICES[key]
        elif key in cls.RANGES:
            low, high = cls.RANGES[key]
            valid = type(value) is int and low <= value <= high
        elif key in cls.NUMBERS:
            floor, ceiling = cls.NUMBERS[key]
            # a NaN fails both comparisons
            valid = type(value) in (int, float) and floor <= value <= ceiling
        else:
            valid = key in cls.FLAGS and type(value) is bool
        if not valid:
            raise ValueError(f"invalid_{key}")
        return value

    @classmethod
    def salvage(cls, settings: dict[str, Any]) -> None:
        """Replace invalid hand-edited values with their defaults, one warning per key."""
        for key in cls.KEYS:
            if key not in settings:
                continue
            try:
                cls.normalize(key, settings[key])
            except ValueError:
                default = default_settings[key]
                settings[key] = default
                shown = ("on" if default else "off") if isinstance(default, bool) else default
                logger.warning("Ignoring invalid %s in settings; using the default (%s)", key, shown)


class NotificationEventSettings:
    """Validate which events send Discord and Telegram notifications."""

    @staticmethod
    def normalize(value: Any, current: dict[str, bool] | None = None) -> dict[str, bool]:
        """Merge a partial update of known boolean events into the current choices."""
        template = default_settings["notification_events"]
        assert isinstance(template, dict)
        if not isinstance(value, dict) or any(
            key not in template or not isinstance(enabled, bool) for key, enabled in value.items()
        ):
            raise ValueError("Invalid notification events")
        return {**template, **(current or {}), **value}


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
    claim_channel_points: bool
    share_watch_streaks: bool
    games_to_watch: list[str]
    idle_channels: list[str]
    idle_priority: str
    idle_streak_first: bool
    streak_recovery: bool
    follow_raids: bool
    predictions_enabled: bool
    prediction_strategy: str
    prediction_percent: int
    prediction_max_points: int
    prediction_min_balance: int
    prediction_min_voters: int
    prediction_min_odds: float
    prediction_timing: str
    prediction_delay_seconds: int
    prediction_timer_percent: int
    prediction_fixed_option: int
    claim_moments: bool
    join_chat: bool
    language: str
    last_notified_releases: dict[str, str]
    reward_priority: list[str]
    prefer_followed_channels: bool
    priority_list_only: bool
    inventory_filters: InventoryFilters
    inventory_list_view: bool
    minimum_refresh_interval_minutes: int
    mining_benefits: dict[str, bool]
    mining_hours: dict[str, str]
    mining_opt_ins_revision: int
    notification_events: dict[str, bool]
    notification_events_revision: int
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
        # Drops outrank this optional list: a bad hand-edited value must not block startup.
        raw_idle = settings.get("idle_channels", [])
        try:
            settings["idle_channels"] = IdleChannelSettings.normalize(raw_idle)
        except ValueError:
            kept = IdleChannelSettings.salvage(raw_idle)
            settings["idle_channels"] = kept
            # the count only: channel names stay out of the logs
            logger.warning("Ignoring invalid idle_channels entries in settings; kept %d channels", len(kept))
        if not isinstance(settings.get("claim_channel_points", True), bool):
            # merge_json below replaces the wrong type with the default (on)
            logger.warning("Ignoring invalid claim_channel_points in settings; using the default (on)")
        if not isinstance(settings.get("share_watch_streaks", False), bool):
            logger.warning("Ignoring invalid share_watch_streaks in settings; using the default (off)")
        PointsSettings.salvage(settings)
        # Early builds stored a single release version. Preserve their announce-once behavior.
        last_notified = settings.pop("last_notified_release", None)
        if isinstance(last_notified, str) and "last_notified_releases" not in settings:
            settings["last_notified_releases"] = {"telegram": last_notified, "discord": last_notified}
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
        self.idle_channels = IdleChannelSettings.normalize(self.idle_channels)
        for key in PointsSettings.KEYS:
            PointsSettings.normalize(key, getattr(self, key))
        self.drop_name_blacklist = DropIgnorePolicy.normalize_keywords(
            self.drop_name_blacklist
        )
        values = vars(self).copy()
        json_save(SETTINGS_PATH, values, sort=True)
