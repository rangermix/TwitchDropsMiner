"""PointsHistory: recording rules, bounds, account change, saving and damaged files."""

import json
import logging

import pytest

from src.services import points_history as mod
from src.services.points_history import CHANNELS_KEPT, SAMPLES_KEPT, PointsHistory, category


def make(path=None, account=7, now=1000.0):
    history = PointsHistory(path)
    history.use_account(account)
    clock = {"wall": now, "mono": 0.0}
    history._now = lambda: clock["wall"]
    history._monotonic = lambda: clock["mono"]
    return history, clock


def test_samples_append_skip_unchanged_and_replace_within_five_minutes():
    h, clock = make()
    h.sample(1, "a", 100)
    h.sample(1, "a", 100)  # unchanged
    clock["wall"] += 1000
    h.sample(1, "a", 100)  # unchanged, long after: still no new sample
    assert h.channels[1]["samples"] == [[1000, 100]]
    clock["wall"] -= 1000
    clock["wall"] += 299
    h.sample(1, "a", 150)  # replaces
    assert h.channels[1]["samples"] == [[1299, 150]]
    clock["wall"] += 300
    h.sample(1, "a", 160)  # appends
    assert h.channels[1]["samples"] == [[1299, 150], [1599, 160]]


def test_samples_are_capped_and_the_oldest_dropped():
    h, clock = make()
    for i in range(SAMPLES_KEPT + 5):
        clock["wall"] += 300
        h.sample(1, "a", i + 1)
    samples = h.channels[1]["samples"]
    assert len(samples) == SAMPLES_KEPT and samples[-1][1] == SAMPLES_KEPT + 5 and samples[0][1] == 6


def test_channels_are_capped_and_unlisted_ones_dropped_at_the_next_save():
    h, _ = make()
    for i in range(CHANNELS_KEPT + 3):
        h.sample(i, f"c{i}", 10)
    assert len(h.channels) == CHANNELS_KEPT and CHANNELS_KEPT not in h.channels
    h.save({"c0", "c1"})
    assert sorted(h.channels) == [0, 1]


@pytest.mark.parametrize(("reason", "expected"), [
    ("WATCH", "watch"), ("WATCH_BONUS", "watch"), ("CLAIM", "bonus"), ("RAID", "raid"),
    ("WATCH_STREAK", "streak"), ("SOMETHING_NEW", "other"), ("", "other"),
])
def test_gain_categories(reason, expected):
    assert category(reason) == expected
    h, _ = make()
    h.gain(1, "a", reason, 7)
    assert h.channels[1]["gains"][expected] == 7
    assert sum(h.channels[1]["gains"].values()) == 7


def test_bets_count_results_and_a_win_adds_to_the_bet_gains():
    h, _ = make()
    h.bet(1, "a", "WIN", 120)
    h.bet(1, "a", "LOSE")
    h.bet(1, "a", "REFUND")
    h.bet(1, "a", "DRAW")
    assert h.channels[1]["bets"] == {"won": 1, "lost": 1, "refunded": 1, "points_won": 120}
    assert h.channels[1]["gains"]["bet"] == 120


def test_another_account_starts_empty_and_replaces_the_file(tmp_path):
    path = tmp_path / "h.json"
    h, _ = make(path)
    h.sample(1, "a", 5)
    assert h.save({"a"})
    assert json.loads(path.read_text())["user_id"] == 7
    again = PointsHistory(path)
    assert again.use_account(7) is False and 1 in again.channels
    assert again.use_account(8) is True and again.channels == {}
    again.save({"a"})
    assert json.loads(path.read_text()) == {"version": 1, "user_id": 8, "channels": {}}


def test_save_only_when_changed_and_at_most_once_a_minute(tmp_path):
    path = tmp_path / "h.json"
    h, clock = make(path)
    h.sample(1, "a", 5)
    assert h.save({"a"})
    first = path.read_text()
    path.write_text("marker")
    assert h.save({"a"})  # unchanged: no write
    assert path.read_text() == "marker"
    clock["wall"] += 400
    h.sample(1, "a", 6)
    clock["mono"] += 59
    assert h.save({"a"}) is False and path.read_text() == "marker"  # too soon
    clock["mono"] += 1
    assert h.save({"a"}) and path.read_text() != "marker" and path.read_text() != first


def test_a_failed_write_warns_once_and_is_retried(tmp_path, monkeypatch, caplog):
    h, clock = make(tmp_path / "h.json")
    h.sample(1, "a", 5)
    calls = []

    def boom(path, payload):
        calls.append(1)
        raise PermissionError("secret path")

    monkeypatch.setattr(mod, "json_save", boom)
    with caplog.at_level(logging.WARNING, logger="TwitchDrops"):
        assert h.save({"a"}) is False
        clock["mono"] += 60
        assert h.save({"a"}) is False
    assert len(calls) == 2
    warnings = [r.getMessage() for r in caplog.records]
    assert warnings == ["Points history could not be saved (PermissionError)"]
    monkeypatch.undo()
    clock["mono"] += 60
    assert h.save({"a"}) is True and (tmp_path / "h.json").exists()


GOOD = {"login": "a", "samples": [[1, 2]], "gains": {"watch": 3}, "bets": {"won": 1}}
SECRET = "SECRETCONTENT"


@pytest.mark.parametrize("content", [
    "{not json " + SECRET,
    json.dumps([SECRET]),
    json.dumps({"version": 2, "user_id": 7, "channels": {}, "x": SECRET}),
    json.dumps({"version": 1, "user_id": SECRET, "channels": {}}),
    json.dumps({"version": 1, "user_id": True, "channels": {}}),
    json.dumps({"version": 1, "user_id": 7, "channels": SECRET}),
    json.dumps({"version": 1, "channels": {}}),
])
def test_a_damaged_file_is_kept_unchanged_and_never_logged(tmp_path, caplog, content):
    path = tmp_path / "h.json"
    path.write_text(content, encoding="utf8")
    before = path.read_bytes()
    with caplog.at_level(logging.WARNING, logger="TwitchDrops"):
        h = PointsHistory(path)
    h.use_account(7)
    h.sample(1, "a", 5)
    assert h.channels[1]["samples"]  # recorded in memory
    h._monotonic = lambda: 10_000.0
    assert h.save({"a"}) is False
    assert path.read_bytes() == before
    assert len([r for r in caplog.records if r.levelno == logging.WARNING]) == 1
    assert SECRET not in caplog.text


def test_bad_channel_entries_are_skipped_with_a_count_and_the_rest_kept(tmp_path, caplog):
    path = tmp_path / "h.json"
    channels = {
        "1": GOOD,
        "x": GOOD,
        "2": "nope",
        "3": {**GOOD, "samples": [[1, "2"]]},
        "4": {**GOOD, "gains": {"mystery": 1}},
        "5": {**GOOD, "bets": {"won": -1}},
        "6": {**GOOD, "login": 5},
    }
    path.write_text(json.dumps({"version": 1, "user_id": 7, "channels": channels}), encoding="utf8")
    with caplog.at_level(logging.WARNING, logger="TwitchDrops"):
        h = PointsHistory(path)
    assert list(h.channels) == [1] and h.account == 7
    assert h.channels[1]["gains"]["watch"] == 3 and h.channels[1]["bets"]["won"] == 1
    assert [r.getMessage() for r in caplog.records] == ["Points history file: 6 invalid entries skipped"]


def test_a_missing_file_is_empty_and_a_saved_file_loads_back(tmp_path):
    path = tmp_path / "h.json"
    h, _ = make(path)
    assert h.channels == {} and not h._load_failed
    h.sample(1, "a", 5)
    h.gain(1, "a", "RAID", 4)
    h.save({"a"})
    again = PointsHistory(path)
    assert again.account == 7 and again.channels == h.channels
