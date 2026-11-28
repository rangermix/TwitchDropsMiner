from __future__ import annotations

import codecs
import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from src.drop_history import HISTORY_FILENAME, HISTORY_VERSION, DropHistory


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


def test_since_filter_compares_instants_instead_of_iso_strings(tmp_path):
    history = _fresh_history(tmp_path)
    history.record(_MockDrop(), _MockCampaign())
    history._entries[0]["claimed_at"] = "2026-02-03T02:30:00+00:00"
    offset = datetime.fromisoformat("2026-02-03T12:30:00+10:00")
    assert len(history.get_entries(since=offset)) == 1
    assert history.get_entries(since=offset + timedelta(microseconds=1)) == []
    assert len(history.get_entries(since=datetime(2026, 2, 3, 2, 30))) == 1


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


@pytest.mark.parametrize(
    "contents",
    [
        b'{"version": 1, "entries": [',
        b"[]",
        b'{"version": 2, "entries": []}',
        b'{"version": true, "entries": []}',
        b'{"version": 1, "entries": {}}',
        b'{"version": 1, "entries": [42]}',
        b'{"version": 1, "entries": [{"id": "stale"}]}',
        b"\xff",
    ],
)
def test_unusable_file_is_preserved_before_next_claim(tmp_path, contents):
    target = tmp_path / HISTORY_FILENAME
    target.write_bytes(contents)

    history = _fresh_history(tmp_path)
    assert history.total_count == 0
    assert history.record(_MockDrop(), _MockCampaign())
    assert target.with_name(f"{target.name}.bad").read_bytes() == contents
    assert DropHistory(tmp_path).get_entries()[0]["id"] == _MockDrop.id


@pytest.mark.parametrize(
    "overrides",
    [
        {"game": []},
        {"benefits": [1]},
        {"required_minutes": True},
        {"claimed_at": "invalid"},
        {"claimed_at": "2026-01-01T00:00:00"},
        {"drop_name": "\ud800"},
    ],
)
def test_invalid_entry_fields_are_preserved_as_unusable(tmp_path, overrides):
    history = _fresh_history(tmp_path)
    assert history.record(_MockDrop(), _MockCampaign())
    target = tmp_path / HISTORY_FILENAME
    payload = json.loads(target.read_bytes())
    payload["entries"][0].update(overrides)
    contents = json.dumps(payload).encode()
    target.write_bytes(contents)

    history = _fresh_history(tmp_path)
    assert history.total_count == 0
    assert target.with_name(f"{target.name}.bad").read_bytes() == contents


def test_quarantine_preserves_existing_backups(tmp_path):
    target = tmp_path / HISTORY_FILENAME
    first = target.with_name(f"{target.name}.bad")
    second = target.with_name(f"{target.name}.bad.1")
    first.write_bytes(b"first")
    second.write_bytes(b"second")
    target.write_bytes(b"third")

    assert _fresh_history(tmp_path).total_count == 0
    assert first.read_bytes() == b"first"
    assert second.read_bytes() == b"second"
    assert target.with_name(f"{target.name}.bad.2").read_bytes() == b"third"


def test_unreadable_file_is_left_untouched_and_saving_disabled(tmp_path, monkeypatch):
    target = tmp_path / HISTORY_FILENAME
    assert _fresh_history(tmp_path).record(_MockDrop(), _MockCampaign())
    contents = target.read_bytes()
    open_path = Path.open

    def refuse_read(path, *args, **kwargs):
        if path == target:
            raise PermissionError("held open")
        return open_path(path, *args, **kwargs)

    with monkeypatch.context() as context:
        context.setattr(Path, "open", refuse_read)
        history = _fresh_history(tmp_path)

    assert not history.record(_MockDrop(), _MockCampaign(), "new-drop")
    assert not history.record(_MockDrop(), _MockCampaign(), "new-drop")
    assert not history.clear()
    assert target.read_bytes() == contents
    assert list(tmp_path.iterdir()) == [target]


def test_failed_quarantine_leaves_original_file_untouched(tmp_path, monkeypatch):
    target = tmp_path / HISTORY_FILENAME
    target.write_bytes(b"{broken")
    replace_path = Path.replace

    def refuse_move(path, destination):
        if path == target:
            raise PermissionError("held open")
        return replace_path(path, destination)

    with monkeypatch.context() as context:
        context.setattr(Path, "replace", refuse_move)
        history = _fresh_history(tmp_path)

    assert not history.record(_MockDrop(), _MockCampaign())
    assert target.read_bytes() == b"{broken"
    assert list(tmp_path.iterdir()) == [target]


def test_valid_bom_history_preserves_entries_and_remains_writable(tmp_path):
    history = _fresh_history(tmp_path)
    assert history.record(_MockDrop(), _MockCampaign())
    target = tmp_path / HISTORY_FILENAME
    contents = codecs.BOM_UTF8 + target.read_bytes()
    target.write_bytes(contents)

    loaded = _fresh_history(tmp_path)
    assert loaded.get_entries() == history.get_entries()
    assert target.read_bytes() == contents
    assert loaded.record(_MockBroadcasterDrop(), _MockBroadcasterCampaign())
    assert DropHistory(tmp_path).total_count == 2
    assert list(tmp_path.iterdir()) == [target]


def test_failed_clear_preserves_saved_entries_and_pending_claim(tmp_path, monkeypatch):
    history = _fresh_history(tmp_path)
    assert history.record(_MockDrop(), _MockCampaign())
    target = tmp_path / HISTORY_FILENAME
    contents = target.read_bytes()

    def refuse_sync(_descriptor):
        raise OSError("disk full")

    with monkeypatch.context() as context:
        context.setattr(os, "fsync", refuse_sync)
        assert not history.record(_MockBroadcasterDrop(), _MockBroadcasterCampaign())
        assert not history.clear()

    assert history.total_count == 2
    assert target.read_bytes() == contents
    assert history.record(_MockBroadcasterDrop(), _MockBroadcasterCampaign())
    assert DropHistory(tmp_path).total_count == 2
    assert history.clear()
    assert DropHistory(tmp_path).total_count == 0
