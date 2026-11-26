"""Balance history, gains per source and bet results of the listed channels (`data/points_history.json`)."""

from __future__ import annotations

import copy
import json
import logging
import time
from typing import TYPE_CHECKING, Any

from src.utils import json_save


if TYPE_CHECKING:
    from collections.abc import Collection
    from pathlib import Path


logger = logging.getLogger("TwitchDrops")

HISTORY_VERSION = 1
SAMPLES_KEPT = 288
CHANNELS_KEPT = 20
# a sample this close to the start of the last bucket updates its balance, so 288 samples span a day or more
SAMPLE_MERGE_SECONDS = 300
SAVE_INTERVAL_SECONDS = 60
CATEGORIES = ("watch", "bonus", "raid", "streak", "bet", "other")
# points_won is gross, as Twitch sends it (the stake included); gains["bet"] holds the net
BET_KEYS = ("won", "lost", "refunded", "points_won")
# the reason codes are the ones of IdleWatchService (STREAK_REASON, CLAIM_REASON) and the tab's reasons table
REASON_CATEGORIES = {"CLAIM": "bonus", "RAID": "raid", "WATCH_STREAK": "streak"}


def category(reason: str) -> str:
    """The gain category of a Twitch reason code; a WATCH variant counts as watching, an unknown code as other."""
    if reason in REASON_CATEGORIES:
        return REASON_CATEGORIES[reason]
    return "watch" if "WATCH" in reason else "other"


def _count(value: object) -> bool:
    return type(value) is int and value >= 0


class PointsHistory:
    """Per listed channel: balance samples, gains by category and bet counts, kept for one account.

    Saved like the visits file: only when changed and at most once a minute, never over a file that
    could not be read.
    """

    def __init__(self, path: Path | None = None) -> None:
        self._path = path
        self._now = time.time
        self._monotonic = time.monotonic
        self.account: int | None = None
        # channel id -> {"login", "samples", "gains", "bets"}
        self.channels: dict[int, dict[str, Any]] = {}
        # rises on every change, so the dashboard side can tell whether it has to send again
        self.revision = 0
        # the file exists but is unusable: it is never written, recording continues in memory
        self._load_failed = False
        self._load()
        self._saved = copy.deepcopy(self._payload())
        self._save_failed = False
        self._tried_at: float | None = None

    def _load(self) -> None:
        if self._path is None:
            return
        skipped, damaged = 0, None
        account: int | None = None
        channels: dict[int, dict[str, Any]] = {}
        try:
            raw = json.loads(self._path.read_text(encoding="utf8"))
            if not isinstance(raw, dict) or raw.get("version") != HISTORY_VERSION or not isinstance(raw.get("channels"), dict):
                raise ValueError
            account = raw.get("user_id")
            if type(account) is not int or account <= 0:
                raise ValueError
            for key, entry in raw["channels"].items():
                parsed = self._parse(key, entry)
                if parsed is None or len(channels) >= CHANNELS_KEPT:
                    skipped += 1
                else:
                    channels[parsed[0]] = parsed[1]
        except FileNotFoundError:
            return
        except (OSError, ValueError, RecursionError) as exc:
            # exception type only: the file is not echoed into the log
            account, channels, skipped, damaged = None, {}, 0, type(exc).__name__
        if skipped:
            logger.warning("Points history file: %d invalid entries skipped", skipped)
        if damaged is not None:
            self._load_failed = True
            logger.warning("Points history file unreadable (%s): fix or delete it and restart; until then it is not saved", damaged)
        self.account, self.channels = account, channels

    @staticmethod
    def _parse(key: str, entry: object) -> tuple[int, dict[str, Any]] | None:
        if not (key.isascii() and key.isdigit()) or not isinstance(entry, dict):
            return None
        login, samples, gains, bets = entry.get("login"), entry.get("samples"), entry.get("gains"), entry.get("bets")
        if not isinstance(login, str) or not login or not isinstance(samples, list) or not isinstance(gains, dict) or not isinstance(bets, dict):
            return None
        if not all(type(s) is list and len(s) == 2 and _count(s[0]) and _count(s[1]) for s in samples):
            return None
        # the net of the bets can be negative
        if not all(k in CATEGORIES and (_count(v) or (k == "bet" and type(v) is int)) for k, v in gains.items()) or not all(k in BET_KEYS and _count(v) for k, v in bets.items()):
            return None
        return int(key), {
            "login": login,
            "samples": [list(s) for s in samples[-SAMPLES_KEPT:]],
            "gains": {c: gains.get(c, 0) for c in CATEGORIES},
            "bets": {k: bets.get(k, 0) for k in BET_KEYS},
        }

    def _payload(self) -> dict[str, Any]:
        return {
            "version": HISTORY_VERSION,
            "user_id": self.account,
            "channels": {str(i): c for i, c in self.channels.items()},
        }

    def state(self) -> dict[str, Any]:
        """The channels as the dashboard gets them, by channel id."""
        return {"channels": {str(i): c for i, c in self.channels.items()}}

    def use_account(self, account: int | None) -> bool:
        """Start empty when another account than the file's logged in; True when the history was cleared."""
        if account is None or account == self.account:
            return False
        cleared = self.account is not None
        if cleared:
            self.channels.clear()
            self.revision += 1
        self.account = account
        return cleared

    def _entry(self, channel_id: int, login: str) -> dict[str, Any] | None:
        entry = self.channels.get(channel_id)
        if entry is None:
            if len(self.channels) >= CHANNELS_KEPT:
                return None
            entry = self.channels[channel_id] = {
                "login": login, "samples": [], "gains": dict.fromkeys(CATEGORIES, 0), "bets": dict.fromkeys(BET_KEYS, 0),
            }
        entry["login"] = login
        self.revision += 1
        return entry

    def sample(self, channel_id: int, login: str, balance: int) -> None:
        """Record a balance; unchanged is skipped, one in the same 5-minute slot as the last sample updates its
        balance and keeps its time (a steady stream of changes still gives one sample per slot)."""
        samples = self.channels.get(channel_id, {}).get("samples", [])
        if samples and samples[-1][1] == balance:
            return
        entry = self._entry(channel_id, login)
        if entry is None:
            return
        samples, now = entry["samples"], int(self._now())
        if samples and now // SAMPLE_MERGE_SECONDS == samples[-1][0] // SAMPLE_MERGE_SECONDS:
            samples[-1][1] = balance
        else:
            samples.append([now, balance])
            del samples[:-SAMPLES_KEPT]

    def gain(self, channel_id: int, login: str, reason: str, points: int) -> None:
        """Add a points-earned gain; a prediction payout is counted by bet() from the result message."""
        if points < 0 or "PREDICTION" in reason or (entry := self._entry(channel_id, login)) is None:
            return
        entry["gains"][category(reason)] += points

    def bet(self, channel_id: int, login: str, result: str, points_won: int = 0, stake: int = 0) -> None:
        """Count a bet TDM placed: result is WIN, LOSE or REFUND; points_won (gross) is Twitch's payout.

        gains["bet"] gets the net: a win its payout less the stake, a loss minus the stake, a refund 0."""
        key = {"WIN": "won", "LOSE": "lost", "REFUND": "refunded"}.get(result)
        if key is None or (entry := self._entry(channel_id, login)) is None:
            return
        entry["bets"][key] += 1
        if key == "won":
            entry["gains"]["bet"] += points_won - stake
            entry["bets"]["points_won"] += points_won
        elif key == "lost":
            entry["gains"]["bet"] -= stake

    def save(self, logins: Collection[str], *, force: bool = False) -> bool:
        """Drop unlisted channels, then write when changed and the last attempt is a minute old (force: now,
        when idle watching stops or at shutdown). A damaged file is never written.

        True when the file holds the history (or there is nothing to write).
        """
        gone = [i for i, c in self.channels.items() if c["login"] not in logins]
        for i in gone:
            del self.channels[i]
        if gone:
            self.revision += 1
        if self._load_failed or self._path is None or self.account is None:
            return False
        payload = self._payload()
        if payload == self._saved:
            return True
        now = self._monotonic()
        if not force and self._tried_at is not None and now - self._tried_at < SAVE_INTERVAL_SECONDS:
            return False
        self._tried_at = now
        try:
            json_save(self._path, payload)
        except OSError as exc:
            # once per failing streak: a later tick tries again
            if not self._save_failed:
                logger.warning("Points history could not be saved (%s)", type(exc).__name__)
            self._save_failed = True
            return False
        self._saved, self._save_failed = copy.deepcopy(payload), False
        return True
