from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from src.drop_history import HISTORY_VERSION, DropHistory


class _MockBenefit:
    def __init__(self, name: str) -> None:
        self.name = name


class _MockDrop:
    id = "drop-1"
    name = "Watch 30 Minutes"
    required_minutes = 30
    benefits = [_MockBenefit("Cool Badge"), _MockBenefit("Launch Emote")]

    def rewards_text(self, delim: str = ", ") -> str:
        return delim.join(b.name for b in self.benefits)


class _MockGame:
    name = "Valorant"


class _MockCampaign:
    id = "camp-1"
    name = "Summer Drops"
    game = _MockGame()


class _MockBroadcasterDrop:
    """Drop whose benefits attribute raises, forcing the fallback path."""

    id = "drop-2"
    name = "Legacy Drop"
    required_minutes = 60

    @property
    def benefits(self):
        raise AttributeError("benefits missing")

    def rewards_text(self, delim: str = ", ") -> str:
        return "Legacy Reward"


class _MockBroadcasterCampaign:
    id = "camp-2"
    name = "Legacy Campaign"
    game = _MockGame()


def _fresh_history(tmp_path: Path) -> DropHistory:
    return DropHistory(tmp_path)


def test_record_and_total_count(tmp_path):
    history = _fresh_history(tmp_path)
    history.record(_MockDrop(), _MockCampaign())
    assert history.total_count == 1

    # Duplicate drop IDs are ignored, even across reloads
    history.record(_MockDrop(), _MockCampaign())
    assert history.total_count == 1


def test_persistence_across_instances(tmp_path):
    history = _fresh_history(tmp_path)
    history.record(_MockDrop(), _MockCampaign())

    reloaded = DropHistory(tmp_path)
    assert reloaded.total_count == 1
    assert reloaded.get_entries()[0]["drop_name"] == "Watch 30 Minutes"


def test_get_entries_filters(tmp_path):
    history = _fresh_history(tmp_path)
    history.record(_MockDrop(), _MockCampaign())

    assert history.get_entries() == history.get_entries(game="VALORANT")
    assert history.get_entries(game="warframe") == []

    since = datetime.now(timezone.utc) - timedelta(days=1)
    assert history.get_entries(since=since) != []
    future = datetime.now(timezone.utc) + timedelta(days=1)
    assert history.get_entries(since=future) == []

    assert len(history.get_entries(limit=1)) == 1


def test_benefits_fallback_when_benefits_attribute_missing(tmp_path):
    history = _fresh_history(tmp_path)
    history.record(_MockBroadcasterDrop(), _MockBroadcasterCampaign())

    entry = history.get_entries()[0]
    assert entry["benefits"] == ["Legacy Reward"]


def test_to_csv_headers_and_rows(tmp_path):
    history = _fresh_history(tmp_path)
    history.record(_MockDrop(), _MockCampaign())

    csv_content = history.to_csv()
    lines = csv_content.strip().splitlines()

    assert lines[0].startswith("claimed_at,game,campaign,drop_name,benefits")
    row = lines[1]
    assert "Valorant" in row
    assert "Cool Badge; Launch Emote" in row
    assert "drop-1" in row


def test_stats_by_game_and_month(tmp_path):
    history = _fresh_history(tmp_path)
    history.record(_MockDrop(), _MockCampaign())
    history.record(_MockBroadcasterDrop(), _MockBroadcasterCampaign())

    assert history.stats_by_game() == {"Valorant": 2}
    month = datetime.now(timezone.utc).strftime("%Y-%m")
    assert history.stats_by_month() == {month: 2}


def test_clear_deletes_entries_from_disk(tmp_path):
    history = _fresh_history(tmp_path)
    history.record(_MockDrop(), _MockCampaign())
    history.clear()
    assert history.total_count == 0

    reloaded = DropHistory(tmp_path)
    assert reloaded.total_count == 0


def test_unknown_version_file_is_ignored(tmp_path):
    payload = {"version": HISTORY_VERSION + 1, "entries": [{"id": "stale"}]}
    target = tmp_path / "drop_history.json"
    target.write_text(json.dumps(payload), encoding="utf-8")

    history = _fresh_history(tmp_path)
    assert history.total_count == 0


def test_corrupt_history_file_does_not_crash(tmp_path):
    (tmp_path / "drop_history.json").write_text("{not json", encoding="utf-8")

    history = _fresh_history(tmp_path)
    assert history.total_count == 0
