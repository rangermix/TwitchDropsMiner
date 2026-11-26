"""Points tab backend: the state payload and its broadcasts, the points log and the pause API."""

import asyncio
import json
import string
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import src.web.app as web
from src.i18n.translator import LANG_PATH
from src.web.managers.console import ConsoleOutputManager
from src.web.managers.points import PointsManager
from tests.test_wanted_queue_order import client  # noqa: F401 (pytest fixture)


def chan(cid, login, online=True):
    return SimpleNamespace(id=cid, login=login, name=login.upper(), online=online)


def manager(idle=("a", "b", "c")):
    broadcaster = AsyncMock()
    twitch = SimpleNamespace(
        channels={1: chan(1, "a"), 2: chan(2, "b", online=False)},
        settings=SimpleNamespace(idle_channels=list(idle)),
        idle_watch=SimpleNamespace(balances={1: 900}, points_paused=False, active=True),
    )
    log = ConsoleOutputManager(broadcaster, event="points_output", echo=False)
    return PointsManager(broadcaster, twitch, log), broadcaster, twitch


def emitted(broadcaster, event):
    return [c.args[1] for c in broadcaster.emit.await_args_list if c.args[0] == event]


@pytest.mark.asyncio
async def test_state_payload_lists_every_listed_channel_in_list_order():
    points, _, _ = manager()
    assert points.get_state() == {"paused": False, "active": True, "channels": [
        {"login": "a", "name": "A", "live": True, "balance": 900, "last_gain": None},
        {"login": "b", "name": "B", "live": False, "balance": None, "last_gain": None},
        # listed but not tracked (yet): its login as name, offline, unknown balance
        {"login": "c", "name": "c", "live": False, "balance": None, "last_gain": None},
    ]}


@pytest.mark.asyncio
async def test_gain_sets_last_gain_and_broadcasts_once_per_change():
    points, broadcaster, twitch = manager()
    points.gain("b", 10, "WATCH")
    await asyncio.sleep(0)
    [state] = emitted(broadcaster, "points_update")
    gain = state["channels"][1]["last_gain"]
    assert (gain["points"], gain["reason"]) == (10, "WATCH")
    assert "T" in gain["at"] and gain["at"][-6] in "+-"  # ISO time with offset
    points.refresh()  # unchanged: nothing sent
    await asyncio.sleep(0)
    assert len(emitted(broadcaster, "points_update")) == 1
    twitch.idle_watch.balances[2] = 5
    points.refresh()
    await asyncio.sleep(0)
    assert emitted(broadcaster, "points_update")[-1]["channels"][1]["balance"] == 5


@pytest.mark.asyncio
async def test_balance_stays_shown_after_a_cleanup_untracks_the_channel():
    points, _, twitch = manager()
    points.get_state()
    del twitch.channels[1]
    row = points.get_state()["channels"][0]
    assert (row["name"], row["live"], row["balance"]) == ("a", False, 900)
    points.clear()  # account change
    twitch.idle_watch.balances.clear()
    assert points.get_state()["channels"][0]["balance"] is None


@pytest.mark.asyncio
async def test_pause_change_logs_and_broadcasts_both_events():
    points, broadcaster, twitch = manager()
    twitch.idle_watch.points_paused = True
    points.paused_changed(True)
    await asyncio.sleep(0)
    assert emitted(broadcaster, "points_paused_update") == [{"paused": True}]
    assert emitted(broadcaster, "points_update")[-1]["paused"] is True
    [line] = emitted(broadcaster, "points_output")
    assert line["message"].endswith("| Channel points paused")


@pytest.mark.asyncio
async def test_initial_state_is_the_state_plus_the_log_history():
    points, broadcaster, _ = manager()
    with patch("src.web.managers.console.logger") as logger:
        points.log("gain", channel="A", points=10, reason="WATCH")
        await asyncio.sleep(0)
    logger.info.assert_not_called()  # the service logs its own English line
    initial = points.get_initial()
    assert initial == {**points.get_state(), "log": initial["log"]}
    [line] = initial["log"]
    assert line.endswith("| +10 on A (WATCH)")
    # the points log is its own event, not the main console
    assert [c.args[0] for c in broadcaster.emit.await_args_list] == ["points_output"]


# every points log line part 2 relies on, with its placeholders
PLACEHOLDERS = {
    "paused": set(), "resumed": set(),
    "gain": {"points", "channel", "reason"},
    "bonus_claimed": {"channel"},
    "visit_started": {"channel"}, "recovery_visit_started": {"channel"}, "visit_skipped": {"channel"},
    "streak_shared": {"channel"},
    "bet_placed": {"points", "outcome", "channel", "title"},
    "bet_won": {"points", "channel", "title"},
    "bet_lost": {"channel", "title"}, "bet_refunded": {"channel", "title"}, "bet_skipped": {"channel", "title"},
    "bet_failed": {"channel", "code"}, "bet_unknown": {"points", "channel", "title"},
    "raid_joined": {"channel", "target"}, "raid_failed": {"channel", "code"},
    "moment_claimed": {"channel"},
    "chat_joined": {"channel"}, "chat_left": {"channel"},
    "topic_limit": set(),
}


def placeholders(text):
    return {name for _, name, _, _ in string.Formatter().parse(text) if name is not None}


def flat(tree, prefix=""):
    for key, value in tree.items():
        if isinstance(value, dict):
            yield from flat(value, f"{prefix}{key}.")
        else:
            yield f"{prefix}{key}", value


LOCALES = sorted(LANG_PATH.glob("*.json"))


@pytest.mark.parametrize("path", LOCALES, ids=[p.stem for p in LOCALES])
def test_every_points_line_has_its_placeholders_in_every_locale(path):
    lang = json.loads(path.read_text(encoding="utf-8"))
    lines = lang["points"]["log"]
    assert set(lines) == set(PLACEHOLDERS)
    for key, text in lines.items():
        assert placeholders(text) == PLACEHOLDERS[key], key
    # the Points tab strings keep the English placeholders (none today: a stray brace would break format())
    english = dict(flat(json.loads((LANG_PATH / "English.json").read_text(encoding="utf-8"))["gui"]["points"]))
    tab = dict(flat(lang["gui"]["points"]))
    assert set(tab) == set(english)
    for key, text in tab.items():
        assert isinstance(text, str) and placeholders(text) == placeholders(english[key]), key


@pytest.mark.asyncio
async def test_console_event_name_and_echo():
    broadcaster = AsyncMock()
    with patch("src.web.managers.console.logger") as logger:
        ConsoleOutputManager(broadcaster).print("main")
        ConsoleOutputManager(broadcaster, event="points_output", echo=False).print("points")
        await asyncio.sleep(0)
    assert [c.args[0] for c in broadcaster.emit.await_args_list] == ["console_output", "points_output"]
    logger.info.assert_called_once_with("main")


# --- POST /api/points/pause ---


class TestPauseAPI:
    @staticmethod
    def attach(client, monkeypatch):  # noqa: F811
        browser, _ = client
        idle = SimpleNamespace(points_paused=False)
        idle.set_points_paused = MagicMock(side_effect=lambda value: setattr(idle, "points_paused", value))
        monkeypatch.setattr(web, "twitch_client", SimpleNamespace(idle_watch=idle))
        return browser, idle

    def test_pauses_and_resumes(self, client, monkeypatch):  # noqa: F811
        browser, idle = self.attach(client, monkeypatch)
        assert browser.post("/api/points/pause", json={"paused": True}).json() == {"success": True, "paused": True}
        assert browser.post("/api/points/pause", json={"paused": False}).json() == {"success": True, "paused": False}
        assert [c.args for c in idle.set_points_paused.call_args_list] == [(True,), (False,)]

    @pytest.mark.parametrize("body", [
        {}, {"paused": None}, {"paused": "true"}, {"paused": 1}, {"paused": 0}, {"paused": [True]},
        {"pause": True}, [True], "true",
    ])
    def test_rejects_bad_bodies_without_a_change(self, client, monkeypatch, body):  # noqa: F811
        browser, idle = self.attach(client, monkeypatch)
        reply = browser.post("/api/points/pause", json=body)
        assert reply.status_code == 422
        assert reply.json() == {"detail": "invalid_request"}
        idle.set_points_paused.assert_not_called()

    def test_unavailable_without_client(self, client, monkeypatch):  # noqa: F811
        browser, _ = client
        monkeypatch.setattr(web, "twitch_client", None)
        assert browser.post("/api/points/pause", json={"paused": True}).status_code == 503
