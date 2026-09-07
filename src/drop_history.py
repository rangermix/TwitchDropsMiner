"""
drop_history.py — Claimed drop history with CSV/JSON export.

Usage:
    from src.drop_history import DropHistory

    history = DropHistory(data_dir)          # opens or creates history.json
    history.record(drop, campaign)           # call right after drop.claim()
    history.to_csv()                         # returns CSV string
    history.get_entries(game="Valorant")     # filtered entries list
"""

from __future__ import annotations

import csv
import io
import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, TypedDict


if TYPE_CHECKING:
    from src.models.campaign import DropsCampaign
    from src.models.drop import TimedDrop

logger = logging.getLogger("TwitchDrops.drop_history")

# Name of the persistent history file stored inside the data directory
HISTORY_FILENAME = "drop_history.json"

# Increment this if the file schema ever changes so old files can be migrated
HISTORY_VERSION = 1


class HistoryEntry(TypedDict):
    id: str              # Twitch drop ID — used as the deduplication key
    claimed_at: str      # ISO-8601 timestamp, always UTC
    game: str
    campaign: str
    drop_name: str
    benefits: list[str]  # human-readable reward names
    required_minutes: int
    campaign_id: str


class DropHistory:
    """
    Manages ``data/drop_history.json``.

    File format::

        {
            "version": 1,
            "entries": [ <HistoryEntry>, ... ]   # oldest first, newest last
        }

    All writes go through a .tmp file that is atomically renamed, so a
    crash mid-write cannot corrupt the history.
    """

    def __init__(self, data_dir: Path) -> None:
        self._path = data_dir / HISTORY_FILENAME
        self._entries: list[HistoryEntry] = []
        self._load()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def record(self, drop: TimedDrop, campaign: DropsCampaign) -> None:
        """
        Persist a claimed drop to history.
        Call this immediately after ``await drop.claim()`` returns.
        Duplicate entries (same drop.id) are silently ignored.
        """
        drop_id: str = drop.id

        # Skip if this drop was already recorded (e.g. restart replaying events)
        if any(e["id"] == drop_id for e in self._entries):
            logger.debug(f"Drop {drop_id} already in history, skipping.")
            return

        # Collect reward names; fall back gracefully if the API shape differs
        benefits: list[str] = []
        try:
            benefits = [b.name for b in drop.benefits]
        except Exception:
            try:
                # rewards_text() returns a single formatted string — wrap in a list
                benefits = [drop.rewards_text()]
            except Exception:
                benefits = []

        entry: HistoryEntry = {
            "id": drop_id,
            "claimed_at": datetime.now(timezone.utc).isoformat(),
            "game": campaign.game.name,
            "campaign": campaign.name,
            # Prefer the structured .name attribute; fall back to rewards_text()
            "drop_name": drop.name if hasattr(drop, "name") else drop.rewards_text(),
            "benefits": benefits,
            "required_minutes": drop.required_minutes,
            "campaign_id": campaign.id,
        }

        self._entries.append(entry)
        self._save()
        logger.info(f"Recorded drop: {entry['game']} — {entry['drop_name']}")

    def get_entries(
        self,
        game: str | None = None,
        campaign_id: str | None = None,
        since: datetime | None = None,
        limit: int | None = None,
    ) -> list[HistoryEntry]:
        """
        Return history entries with optional filtering.
        Results are sorted newest-first (reverse insertion order).

        Args:
            game:        Case-insensitive game name filter.
            campaign_id: Exact campaign ID filter.
            since:       Only entries claimed on or after this UTC datetime.
            limit:       Maximum number of entries to return.
        """
        # Reverse so the caller always gets the most recent entries first
        result = list(reversed(self._entries))

        if game:
            result = [e for e in result if e["game"].lower() == game.lower()]
        if campaign_id:
            result = [e for e in result if e["campaign_id"] == campaign_id]
        if since:
            # ISO-8601 strings sort lexicographically when zero-padded, which they are
            since_str = since.isoformat()
            result = [e for e in result if e["claimed_at"] >= since_str]
        if limit is not None:
            result = result[:limit]

        return result

    def to_csv(
        self,
        game: str | None = None,
        since: datetime | None = None,
    ) -> str:
        """
        Generate a CSV string from all (or filtered) history entries.
        Returns a string ready to be sent as an HTTP response or written to disk.
        The output is encoded with a UTF-8 BOM when sent via the export endpoint
        so that Excel opens it without a manual encoding step.
        """
        entries = self.get_entries(game=game, since=since)

        output = io.StringIO()
        writer = csv.writer(output, quoting=csv.QUOTE_MINIMAL)

        # Column headers — keep in sync with the row below
        writer.writerow([
            "claimed_at",
            "game",
            "campaign",
            "drop_name",
            "benefits",
            "required_minutes",
            "drop_id",
            "campaign_id",
        ])

        for e in entries:
            writer.writerow([
                e["claimed_at"],
                e["game"],
                e["campaign"],
                e["drop_name"],
                "; ".join(e["benefits"]),   # multiple rewards joined with semicolons
                e["required_minutes"],
                e["id"],
                e["campaign_id"],
            ])

        return output.getvalue()

    @property
    def total_count(self) -> int:
        """Total number of recorded drops (no filters applied)."""
        return len(self._entries)

    def stats_by_game(self) -> dict[str, int]:
        """Return {game_name: drop_count}, sorted by count descending."""
        counts: dict[str, int] = {}
        for e in self._entries:
            counts[e["game"]] = counts.get(e["game"], 0) + 1
        return dict(sorted(counts.items(), key=lambda x: x[1], reverse=True))

    def stats_by_month(self) -> dict[str, int]:
        """Return {YYYY-MM: drop_count}, sorted chronologically."""
        counts: dict[str, int] = {}
        for e in self._entries:
            # Slice the ISO-8601 string to get just the year-month portion
            month = e["claimed_at"][:7]   # e.g. "2026-08"
            counts[month] = counts.get(month, 0) + 1
        return dict(sorted(counts.items()))

    def clear(self) -> None:
        """Delete all history entries from memory and disk."""
        self._entries = []
        self._save()
        logger.warning("Drop history cleared.")

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _load(self) -> None:
        """Read history from disk. Silently starts fresh if the file is absent."""
        if not self._path.exists():
            logger.debug(f"No history file at {self._path}, starting fresh.")
            return
        try:
            with self._path.open("r", encoding="utf-8") as f:
                data = json.load(f)
            if data.get("version") == HISTORY_VERSION:
                self._entries = data.get("entries", [])
                logger.debug(f"Loaded {len(self._entries)} history entries.")
            else:
                # Future versions may add a migration path here
                logger.warning(
                    f"Unknown history version {data.get('version')}, ignoring file."
                )
        except (json.JSONDecodeError, OSError) as exc:
            logger.error(f"Failed to load drop history: {exc}")

    def _save(self) -> None:
        """
        Write the current state to disk atomically.
        Writes to a .tmp file first, then renames it over the real file.
        This ensures the history file is never left in a half-written state
        even if the process is killed during the write.
        """
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self._path.with_suffix(".json.tmp")
            with tmp.open("w", encoding="utf-8") as f:
                json.dump(
                    {"version": HISTORY_VERSION, "entries": self._entries},
                    f,
                    ensure_ascii=False,
                    indent=2,
                )
            # Atomic rename — on POSIX this is guaranteed by the OS
            tmp.replace(self._path)
        except OSError as exc:
            logger.error(f"Failed to save drop history: {exc}")
