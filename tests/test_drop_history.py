from __future__ import annotations

import codecs
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


def _record_after_bad_file(tmp_path, content):
    target = tmp_path / "drop_history.json"
    target.write_text(content, encoding="utf-8")
    history = _fresh_history(tmp_path)
    assert history.total_count == 0
    history.record(_MockDrop(), _MockCampaign())
    return target, tmp_path / "drop_history.json.bad"


def test_truncated_file_is_kept_aside_not_overwritten(tmp_path):
    content = '{"version": 1, "entries": [{"id": "old"'
    target, bad = _record_after_bad_file(tmp_path, content)
    assert bad.read_text(encoding="utf-8") == content
    assert len(json.loads(target.read_text(encoding="utf-8"))["entries"]) == 1


def test_unusable_file_shapes_are_moved_aside(tmp_path):
    for content in ("[]", json.dumps({"version": HISTORY_VERSION + 1, "entries": []}),
                    json.dumps({"version": HISTORY_VERSION, "entries": {}})):
        folder = tmp_path / str(abs(hash(content)))
        folder.mkdir()
        _, bad = _record_after_bad_file(folder, content)
        assert bad.read_text(encoding="utf-8") == content


def test_valid_file_is_loaded_without_bad_copy(tmp_path):
    (tmp_path / "drop_history.json").write_text(
        json.dumps({"version": HISTORY_VERSION, "entries": [{"id": "x"}]}), encoding="utf-8")
    assert _fresh_history(tmp_path).total_count == 1
    assert not (tmp_path / "drop_history.json.bad").exists()


def test_save_is_disabled_when_bad_file_cannot_be_moved(tmp_path, monkeypatch):
    target = tmp_path / "drop_history.json"
    target.write_text("{broken", encoding="utf-8")
    real_replace = Path.replace

    def replace(self, dest):
        if self == target:
            raise PermissionError("held open")
        return real_replace(self, dest)

    monkeypatch.setattr(Path, "replace", replace)
    history = _fresh_history(tmp_path)
    assert history.record(_MockDrop(), _MockCampaign()) is False
    assert target.read_text(encoding="utf-8") == "{broken"


def test_existing_bad_file_is_not_overwritten(tmp_path):
    (tmp_path / "drop_history.json.bad").write_text("first", encoding="utf-8")
    (tmp_path / "drop_history.json").write_text("{second", encoding="utf-8")
    _fresh_history(tmp_path)
    assert (tmp_path / "drop_history.json.bad").read_text(encoding="utf-8") == "first"
    assert (tmp_path / "drop_history.json.bad.1").read_text(encoding="utf-8") == "{second"


def test_unreadable_file_is_left_untouched_and_saving_stays_disabled(tmp_path, monkeypatch, caplog):
    target = tmp_path / "drop_history.json"
    content = json.dumps({"version": HISTORY_VERSION, "entries": [{"id": "x"}]})
    target.write_text(content, encoding="utf-8")
    real_open = Path.open

    def deny(self, *args, **kwargs):
        if self == target:
            raise PermissionError("held open")
        return real_open(self, *args, **kwargs)

    monkeypatch.setattr(Path, "open", deny)
    history = _fresh_history(tmp_path)
    monkeypatch.undo()
    assert not (tmp_path / "drop_history.json.bad").exists()
    # A duplicate id keeps reporting the failed write instead of claiming success.
    assert history.record(_MockDrop(), _MockCampaign()) is False
    assert history.record(_MockDrop(), _MockCampaign()) is False
    history.clear()
    assert "cleared" not in caplog.text
    assert target.read_text(encoding="utf-8") == content


def test_non_dict_entries_are_moved_aside(tmp_path):
    target = tmp_path / "drop_history.json"
    target.write_text(json.dumps({"version": HISTORY_VERSION, "entries": [1, "x"]}), encoding="utf-8")
    history = _fresh_history(tmp_path)
    assert history.total_count == 0
    assert (tmp_path / "drop_history.json.bad").exists()
    assert history.record(_MockDrop(), _MockCampaign()) is True


def test_valid_file_with_utf8_bom_is_loaded(tmp_path):
    target = tmp_path / "drop_history.json"
    data = {"version": HISTORY_VERSION, "entries": [{"id": "old", "claimed_at": "2026-01-01T00:00:00+00:00"}]}
    target.write_bytes(codecs.BOM_UTF8 + json.dumps(data).encode("utf-8"))
    history = _fresh_history(tmp_path)
    assert history.total_count == 1
    assert not (tmp_path / "drop_history.json.bad").exists()
