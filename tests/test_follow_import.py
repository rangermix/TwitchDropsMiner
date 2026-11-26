"""Import of the followed channels into the idle list: paging, merge, limits, errors and privacy."""

import asyncio
import logging
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient

import src.web.app as web
from src.config import GQL_OPERATIONS
from src.exceptions import GQLException
from src.services.follow_import import FollowImporter
from src.web.auth import DashboardOrigin


def page(logins, *, more=False, cursor=True):
    edges = [{"node": {"login": login}, **({"cursor": f"cur-{login}"} if cursor else {})} for login in logins]
    return {"data": {"user": {"follows": {"edges": edges, "pageInfo": {"hasNextPage": more}}}}}


def pages(*batches):
    """Answers in order; every batch except the last announces a next page."""
    return [page(batch, more=i < len(batches) - 1) for i, batch in enumerate(batches)]


@pytest.fixture
def env(tmp_path, monkeypatch):
    from collections import deque

    auth = web.web_auth
    for name, value in {"path": tmp_path / "web_auth.json", "password_hash": "", "sessions": {},
                        "lock": asyncio.Lock(), "attempts": deque(), "origin": DashboardOrigin()}.items():
        monkeypatch.setattr(auth, name, value)
    twitch = SimpleNamespace(
        settings=SimpleNamespace(idle_channels=["keep1", "keep2"]),
        logged_in_user_id=lambda: 7,
        gql_request=AsyncMock(),
    )

    def update(data):
        twitch.settings.idle_channels = data["idle_channels"]

    gui = SimpleNamespace(settings=SimpleNamespace(update_settings=MagicMock(side_effect=update)))
    monkeypatch.setattr(web, "twitch_client", twitch)
    monkeypatch.setattr(web, "gui_manager", gui)
    with TestClient(web.socket_app, headers={"X-TDM-Request": "1"}) as browser:
        yield browser, twitch, gui


def run(env, answers):
    browser, twitch, gui = env
    twitch.gql_request.side_effect = answers
    return browser.post("/api/points/import-follows")


def test_one_page_is_appended_after_the_existing_list(env):
    reply = run(env, pages(["new1", "new2"]))
    assert reply.status_code == 200
    assert reply.json() == {"success": True, "added": 2, "already_listed": 0, "over_limit": 0}
    assert env[1].settings.idle_channels == ["keep1", "keep2", "new1", "new2"]
    [op] = [c.args[0] for c in env[1].gql_request.call_args_list]
    assert op["operationName"] == "ChannelFollows"
    assert op["variables"] == {"limit": 100, "order": "ASC", "cursor": ""}


def test_the_operation_is_the_known_persisted_query():
    op = GQL_OPERATIONS["ChannelFollows"]
    assert op["extensions"]["persistedQuery"]["sha256Hash"] == (
        "eecf815273d3d949e5cf0085cc5084cd8a1b5b7b6f7990cf43cb0beadf546907"
    )


def test_several_pages_follow_the_cursor_in_order(env):
    reply = run(env, pages(["a1", "a2"], ["b1"], ["c1", "c2"]))
    assert reply.json()["added"] == 5
    assert env[1].settings.idle_channels == ["keep1", "keep2", "a1", "a2", "b1", "c1", "c2"]
    cursors = [c.args[0]["variables"]["cursor"] for c in env[1].gql_request.call_args_list]
    assert cursors == ["", "cur-a2", "cur-b1"]


def test_at_most_twenty_pages_are_read(env):
    env[1].settings.idle_channels = []
    answers = [page([f"u{i}"], more=True) for i in range(30)]
    reply = run(env, answers)
    assert env[1].gql_request.call_count == FollowImporter.MAX_PAGES == 20
    assert reply.json() == {"success": True, "added": 20, "already_listed": 0, "over_limit": 0}


def test_a_page_without_a_cursor_ends_the_read(env):
    reply = run(env, [page(["a1"], more=True, cursor=False), page(["never"])])
    assert reply.json()["added"] == 1
    assert env[1].gql_request.call_count == 1


def test_an_empty_page_ends_the_read(env):
    reply = run(env, [page([], more=True), page(["never"])])
    assert reply.json() == {"success": True, "added": 0, "already_listed": 0, "over_limit": 0}
    env[2].settings.update_settings.assert_not_called()


def test_duplicates_listed_logins_and_invalid_logins(env):
    answers = pages(["Keep1", "new1", "NEW1", "bad login", "", "k" * 26, "ünï", "Kelvin", "new2"], ["new1", "x-y"])
    reply = run(env, answers)
    # "Kelvin" with the Kelvin sign is not ASCII, so it is ignored, not folded to "kelvin"
    assert reply.json() == {"success": True, "added": 2, "already_listed": 1, "over_limit": 0}
    assert env[1].settings.idle_channels == ["keep1", "keep2", "new1", "new2"]


def test_the_limit_of_twenty_counts_what_did_not_fit(env):
    names = [f"n{i}" for i in range(25)]
    reply = run(env, pages(names[:15], names[15:]))
    assert reply.json() == {"success": True, "added": 18, "already_listed": 0, "over_limit": 7}
    assert env[1].settings.idle_channels == ["keep1", "keep2", *names[:18]]


def test_a_full_list_adds_nothing_and_saves_nothing(env):
    env[1].settings.idle_channels = [f"f{i}" for i in range(20)]
    reply = run(env, pages(["new1", "f1"]))
    assert reply.json() == {"success": True, "added": 0, "already_listed": 1, "over_limit": 1}
    env[2].settings.update_settings.assert_not_called()


def test_not_logged_in_is_a_409_and_reads_nothing(env):
    env[1].logged_in_user_id = lambda: None
    reply = run(env, pages(["a"]))
    assert (reply.status_code, reply.json()) == (409, {"detail": "not_logged_in"})
    env[1].gql_request.assert_not_called()


@pytest.mark.parametrize("failure", [
    GQLException("x"), TimeoutError(), {"errors": [{"message": "boom"}], "data": None}, {"data": {"user": None}},
    {"data": {"user": {"follows": {"edges": "x", "pageInfo": {}}}}}, [], None,
])
def test_a_failed_read_is_a_502_and_changes_nothing(env, failure):
    # the second page fails: the first page's logins are not kept
    reply = run(env, [page(["a1"], more=True), failure])
    assert (reply.status_code, reply.json()) == (502, {"detail": "follows_unavailable"})
    assert env[1].settings.idle_channels == ["keep1", "keep2"]
    env[2].settings.update_settings.assert_not_called()


def test_a_failed_save_is_a_500(env):
    env[2].settings.update_settings.side_effect = OSError
    reply = run(env, pages(["a1"]))
    assert (reply.status_code, reply.json()) == (500, {"detail": "settings_save_failed"})


def test_unavailable_without_a_client(env, monkeypatch):
    monkeypatch.setattr(web, "twitch_client", None)
    assert env[0].post("/api/points/import-follows").status_code == 503


def test_the_dashboard_header_is_required(env):
    browser = env[0]
    browser.headers.pop("X-TDM-Request")
    assert browser.post("/api/points/import-follows").status_code in (401, 403)


def test_no_follow_login_reaches_any_log_record(env, caplog):
    secret = ["zz_secret_one", "zz_secret_two"]
    env[1].gql_request.side_effect = [page(secret, more=True), GQLException(" ".join(secret))]
    with caplog.at_level(logging.DEBUG):
        run(env, [page(secret, more=True), GQLException(" ".join(secret))])
        env[1].gql_request.side_effect = pages(secret, ["zz_secret_three"])
        env[1].settings.idle_channels = []
        env[0].post("/api/points/import-follows")
    text = "\n".join(r.getMessage() + str(r.args) for r in caplog.records)
    assert "zz_secret" not in text
    assert "Followed channels import: 3 added, 0 already listed, 0 over the limit" in text


def test_the_reads_run_inside_private_discovery(env):
    from src.api.gql_client import _private_discovery

    seen = []

    async def answer(_op):
        seen.append(_private_discovery.get())
        return page(["a1"])

    run(env, answer)
    assert seen == [True]
    assert _private_discovery.get() is False
