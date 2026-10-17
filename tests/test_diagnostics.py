"""Operational diagnosis is deidentified before retention and never exports credentials."""

import asyncio
import copy
import importlib
import io
import json
import logging
import os
import stat
import threading
import tracemalloc
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import aiohttp
import pytest

from src.api.http_client import HTTPClient
from src.auth.browser_session import BrowserSession
from src.auth.imported_session import SessionTransport
from src.auth.server_renewal import SDKIssuer
from src.auth.server_seed import ServerSeed
from src.auth.session_bundle import SessionError
from src.config import GQL_OPERATIONS, ClientType, State
from src.config.operations import CHANNEL_BAN_QUERY
from src.config.settings import default_settings
from src.diagnostics import Diagnostics
from src.services.telegram_service import TelegramNotifier
from src.utils.async_helpers import AwaitableValue
from src.websocket.websocket import Websocket
from tests.test_banned_channels import _ban_response, _channel
from tests.test_server_renewal import sdk_peer
from tests.test_server_seed import seed_data
from tests.test_watch_drop_filtering import _campaign, _drop
from tests.test_web_auth import client as client
from tests.test_web_auth import hashed as hashed
from tests.test_web_auth import login
from tests.test_web_auth import protected as protected


CANARY = "private-canary-do-not-export"
web = importlib.import_module("src.web.app")


def diagnostic_miner(tmp_path):
    settings = SimpleNamespace(**copy.deepcopy(default_settings))
    settings.proxy = f"http://user:{CANARY}@private.host:8080"
    settings.telegram_bot_token = CANARY
    settings.telegram_chat_id = "123456789"
    result = SimpleNamespace(
        settings=settings, _state=State.IDLE, _inventory_loaded=True,
        watching_channel=AwaitableValue(), wanted_games=[], channels={}, inventory=[],
        _manual_target_channel=None, _channel_service=SimpleNamespace(banned_channels=[]),
        session_controller=SimpleNamespace(status=lambda: {"state": "ready", "username": CANARY, "session_id": CANARY}),
        gui=SimpleNamespace(channels=MagicMock()),
    )
    result.diagnostics = Diagnostics(tmp_path)
    return result


def add_campaign(miner, *, drop_data=None):
    campaign = _campaign(CANARY, [drop_data or _drop("drop-canary", CANARY, 30)])
    campaign._twitch.settings = miner.settings
    miner.inventory = [campaign]
    miner.wanted_games = [campaign.game]
    miner.settings.games_to_watch = [campaign.game.name]
    return campaign


def test_api_credentials_unknown_fields_error_text_and_identities_are_never_retained(tmp_path):
    miner = diagnostic_miner(tmp_path)
    channel = _channel(miner)
    miner.channels[channel.id] = channel
    diagnostics = miner.diagnostics
    raw = {"data": {"user": {"id": "1", "displayName": CANARY, "self": {
        "banStatus": {"isPermanent": False, "expiresAt": "2099-01-01T00:00:00Z"}}},
        "currentMinutesWatched": 7, "requiredMinutesWatched": 30,
        "access_token": CANARY, "headers": {"Authorization": CANARY}, "cookies": CANARY,
        "custom-" + CANARY: CANARY, "description": "https://private.host/" + CANARY,
        "userID": 123456789, "dropInstanceID": CANARY},
        "errors": [{"message": f"private response {CANARY}", "path": [CANARY]}]}
    diagnostics.record_http("POST", "https://gql.twitch.tv/gql?secret=" + CANARY, 200,
                            body=json.dumps([raw]).encode(), operations=[{"query": CHANNEL_BAN_QUERY,
                                                                       "variables": {"password": CANARY}}])
    retained = diagnostics.apis["twitch_gql:ChannelBanStatus"]["samples"][0]["response"]
    assert retained["data"]["access_token"] == "<redacted>"
    assert retained["data"]["currentMinutesWatched"] == 7
    assert retained["data"]["user"]["self"]["banStatus"]["isPermanent"] is False
    assert CANARY not in json.dumps(diagnostics.apis)
    assert "123456789" not in json.dumps(diagnostics.apis)
    first, second = diagnostics.snapshot(miner), diagnostics.snapshot(miner)
    reference = first["apis"][0]["samples"][0]["response"]["data"]["user"]["id"]
    assert reference == first["channels"][0]["id"]
    assert reference != second["channels"][0]["id"]
    assert CANARY not in json.dumps(first)
    assert "private.host" not in json.dumps(first)


def test_snapshot_projects_real_campaign_and_drop_boolean_fields_strictly(tmp_path):
    miner = diagnostic_miner(tmp_path)
    data = _drop("drop", CANARY, 30)
    data["self"] = {"isClaimed": CANARY, "currentMinutesWatched": 7, "dropInstanceID": CANARY}
    campaign = add_campaign(miner, drop_data=data)
    campaign.linked = CANARY
    miner.settings.connection_quality = CANARY
    snapshot = miner.diagnostics.snapshot(miner)
    assert snapshot["campaigns"][0]["linked"] is None
    assert snapshot["campaigns"][0]["drops"][0]["claimed"] is None
    assert snapshot["settings"]["connection_quality"] is None
    assert CANARY not in json.dumps(snapshot)


def test_snapshot_retains_authoritative_and_estimated_progress_and_ban_metadata(tmp_path):
    miner = diagnostic_miner(tmp_path)
    campaign = add_campaign(miner)
    drop = next(iter(campaign.drops))
    drop.real_current_minutes = 7
    drop.extra_current_minutes = 2
    channel = _channel(miner)
    channel.update_ban_status(_ban_response(1, {"isPermanent": False, "expiresAt": "2099-01-01T00:00:00Z"}))
    miner._channel_service.banned_channels = [channel]
    snapshot = miner.diagnostics.snapshot(miner)
    result = snapshot["campaigns"][0]["drops"][0]
    assert result["required_minutes"] == 30
    assert result["reported_minutes"] == 7
    assert result["estimated_minutes"] == 2
    assert snapshot["channels"][0]["banned"] is True
    assert snapshot["channels"][0]["ban_permanent"] is False
    assert snapshot["channels"][0]["ban_expires_at"].startswith("2099-01-01")


def test_settings_prerequisites_and_response_shapes_have_explicit_limits(tmp_path):
    miner = diagnostic_miner(tmp_path)
    campaign = add_campaign(miner)
    drop = next(iter(campaign.drops))
    drop.precondition_drops = [str(index) for index in range(10001)]
    miner.settings.games_to_watch = [CANARY] * 10001
    miner.settings.drop_name_blacklist = [CANARY] * 10001
    miner.wanted_games = [campaign.game] * 10001
    snapshot = miner.diagnostics.snapshot(miner)
    assert len(snapshot["settings"]["games_to_watch"]) == 256
    assert snapshot["settings"]["games_to_watch_total"] == 10001
    assert len(snapshot["settings"]["ignored_keywords"]) == 256
    assert snapshot["settings"]["ignored_keywords_total"] == 10001
    assert len(snapshot["miner"]["wanted_games"]) == 256
    assert snapshot["miner"]["wanted_games_total"] == 10001
    assert len(snapshot["campaigns"][0]["drops"][0]["preconditions"]) == 64
    assert snapshot["campaigns"][0]["drops"][0]["preconditions_total"] == 10001
    diagnostics = miner.diagnostics
    oversized = diagnostics.sanitize({"data": [CANARY] * 10001})
    assert oversized["data"][-1] == {"__truncated__": 9937}
    deep = {}
    for _ in range(50):
        deep = {"data": deep}
    assert "<truncated>" in json.dumps(diagnostics.sanitize(deep))
    assert len(json.dumps(diagnostics.sanitize({str(index): CANARY for index in range(10001)}))) < 10000


def test_api_samples_are_per_operation_and_watch_traffic_does_not_displace_inventory(tmp_path):
    diagnostics = Diagnostics(tmp_path)
    ops = [GQL_OPERATIONS["Inventory"], {"query": CHANNEL_BAN_QUERY}]
    diagnostics.record_http("POST", BrowserSession.GQL_URL, 200,
                            payload=[{"data": {"currentMinutesWatched": 3}}, {"data": {"user": None}}], operations=ops)
    inventory_key = next(key for key in diagnostics.apis if key.startswith("twitch_gql") and "ChannelBanStatus" not in key)
    for _ in range(1000):
        diagnostics.record("watch_segment", status=200)
    assert diagnostics.apis[inventory_key]["count"] == 1
    assert diagnostics.apis["watch_segment"]["count"] == 1000
    assert len(diagnostics.apis["watch_segment"]["samples"]) == 2
    original = diagnostics.reference(CANARY)
    diagnostics.clear()
    assert not diagnostics.apis and not diagnostics.logs
    assert original != diagnostics.reference(CANARY)


@pytest.mark.parametrize("body,outcome", [(b"<html>" + CANARY.encode(), "response"),
                                            (b"{invalid" + CANARY.encode(), "response"),
                                            (b"X" * 262145, "truncated")], ids=["html", "malformed-json", "oversized"])
def test_non_json_and_large_response_bodies_are_metadata_only(tmp_path, body, outcome):
    diagnostics = Diagnostics(tmp_path)
    diagnostics.record_http("GET", "https://secret.host/" + CANARY, 200, body=body)
    sample = diagnostics.apis["other_http"]["samples"][0]
    assert sample["response"] is None and sample["outcome"] == outcome
    assert sample["body_bytes"] == len(body)
    assert CANARY not in json.dumps(diagnostics.apis)


def test_parsed_api_payload_cannot_retain_an_unbounded_version_leaf(tmp_path):
    diagnostics = Diagnostics(tmp_path)
    diagnostics.record_http("GET", "https://api.github.com/releases/latest", 200,
                            payload={"tag_name": "v" + "1" * (diagnostics.MAX_BODY_BYTES * 4) + ".2.3"})
    assert len(json.dumps(diagnostics.apis)) < 1000
    assert diagnostics.apis["github_version"]["samples"][0]["response"]["tag_name"].startswith("ref-")


def test_parsed_api_payload_does_not_normalize_unbounded_untrusted_field_names(tmp_path):
    diagnostics = Diagnostics(tmp_path)
    payload = {"x-" * (diagnostics.MAX_BODY_BYTES * 4): CANARY}
    tracemalloc.start()
    try:
        diagnostics.record_http("POST", BrowserSession.GQL_URL, 200, payload=payload)
        _current, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert peak < diagnostics.MAX_BODY_BYTES * 4
    assert len(json.dumps(diagnostics.apis)) < 1000


def test_log_capture_never_formats_messages_tracebacks_or_paths(tmp_path):
    diagnostics = Diagnostics(tmp_path)
    record = logging.LogRecord("TwitchDrops", logging.ERROR, f"/private/{CANARY}.py", 23,
                               "Authorization %s", (CANARY,), (ValueError, ValueError(CANARY), None))
    record.getMessage = MagicMock(side_effect=AssertionError("must not format secrets"))
    diagnostics._log_handler.emit(record)
    assert diagnostics.logs[0]["line"] == 23
    assert CANARY not in json.dumps(list(diagnostics.logs))
    diagnostics.start_logs()
    diagnostics.start_logs()
    assert logging.getLogger("TwitchDrops").handlers.count(diagnostics._log_handler) == 1
    diagnostics.stop_logs()
    assert diagnostics._log_handler not in logging.getLogger("TwitchDrops").handlers


@pytest.mark.asyncio
async def test_save_is_atomic_private_relative_and_rate_limited(tmp_path):
    miner = diagnostic_miner(tmp_path)
    filename = await miner.diagnostics.save(miner, {"browser": "Firefox", "online": True})
    saved = tmp_path / filename
    assert saved.parent == tmp_path / "diagnostics"
    assert json.loads(saved.read_text())["browser"]["browser"] == "Firefox"
    assert CANARY not in saved.read_text()
    assert not list(saved.parent.glob(".diagnosis-*"))
    if os.name == "posix":
        assert stat.S_IMODE(saved.stat().st_mode) == 0o600
        assert stat.S_IMODE(saved.parent.stat().st_mode) == 0o700
    with pytest.raises(BlockingIOError):
        await miner.diagnostics.save(miner)


def test_saved_files_have_a_final_byte_cap_and_retention_preserves_other_files(tmp_path, monkeypatch):
    miner = diagnostic_miner(tmp_path)
    diagnostics = miner.diagnostics
    diagnostics.record("twitch_gql", {"data": {"edges": [CANARY] * 64}})
    snapshot = diagnostics.snapshot(miner)
    monkeypatch.setattr(diagnostics, "MAX_DUMP_BYTES", len(json.dumps(snapshot, indent=2)) - 300)
    filename = diagnostics._write(snapshot)
    stored = json.loads((tmp_path / filename).read_text())
    assert (tmp_path / filename).stat().st_size <= diagnostics.MAX_DUMP_BYTES
    assert stored["truncation"]["api_bodies"] == 1
    assert stored["apis"][0]["samples"][0]["response"] == "<truncated:dump-size>"
    monkeypatch.setattr(diagnostics, "MAX_DUMP_BYTES", 8 * 1024 * 1024)
    unrelated = tmp_path / "diagnostics" / "my-report.json"
    unrelated.write_text("keep this file")
    for _ in range(12):
        latest = diagnostics._write(diagnostics.snapshot(miner))
    assert (tmp_path / latest).exists()
    assert len(list((tmp_path / "diagnostics").glob("diagnosis-*.json"))) == 10
    assert unrelated.read_text() == "keep this file"


def test_failed_atomic_write_cleans_temp_file_without_touching_other_data(tmp_path, monkeypatch):
    miner = diagnostic_miner(tmp_path)
    monkeypatch.setattr("src.diagnostics.os.replace", MagicMock(side_effect=OSError(CANARY)))
    with pytest.raises(OSError):
        miner.diagnostics._write(miner.diagnostics.snapshot(miner))
    assert list((tmp_path / "diagnostics").iterdir()) == []


@pytest.mark.skipif(os.name != "posix", reason="requires POSIX symlink/permission checks")
def test_diagnostics_refuses_a_symlink_outside_data_directory(tmp_path):
    miner = diagnostic_miner(tmp_path / "data")
    miner.diagnostics.data_dir.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (miner.diagnostics.data_dir / "diagnostics").symlink_to(outside, target_is_directory=True)
    with pytest.raises(OSError):
        miner.diagnostics._write(miner.diagnostics.snapshot(miner))
    assert list(outside.iterdir()) == []


@pytest.mark.asyncio
async def test_cancelled_save_drains_thread_and_keeps_admission_lock(tmp_path, monkeypatch):
    miner = diagnostic_miner(tmp_path)
    diagnostics = miner.diagnostics
    loop = asyncio.get_running_loop()
    entered, finished = asyncio.Event(), asyncio.Event()
    release = threading.Event()
    actual_write = diagnostics._write

    def write(snapshot):
        loop.call_soon_threadsafe(entered.set)
        assert release.wait(5), "test must release the writer"
        filename = actual_write(snapshot)
        loop.call_soon_threadsafe(finished.set)
        return filename

    monkeypatch.setattr(diagnostics, "_write", write)
    task = asyncio.create_task(diagnostics.save(miner))
    try:
        await asyncio.wait_for(entered.wait(), 2)
        task.cancel()
        await asyncio.sleep(0)
        task.cancel()
        await asyncio.sleep(0)
        assert diagnostics._saving.locked() and not task.done()
        with pytest.raises(BlockingIOError):
            await diagnostics.save(miner)
    finally:
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
    assert finished.is_set() and not diagnostics._saving.locked()
    assert len(list((tmp_path / "diagnostics").glob("*.json"))) == 1
    with pytest.raises(BlockingIOError):
        await diagnostics.save(miner)


@pytest.fixture
def dump_client(client, tmp_path, monkeypatch):
    miner = diagnostic_miner(tmp_path)
    monkeypatch.setattr(web, "twitch_client", miner)
    return client, miner


def test_api_saves_to_volume_and_never_returns_payload_or_absolute_path(dump_client):
    browser, miner = dump_client
    response = browser.post("/api/diagnostics", json={"browser": {"browser": "Chrome", "platform": "Windows", "width": 1280}})
    assert response.status_code == 200
    data = response.json()
    assert data["success"] is True and data["file"].startswith("diagnostics/diagnosis-")
    assert str(miner.diagnostics.data_dir) not in response.text and CANARY not in response.text
    assert (miner.diagnostics.data_dir / data["file"]).exists()
    assert browser.get("/api/" + data["file"]).status_code == 404
    assert browser.post("/api/diagnostics", json={}).status_code == 429


@pytest.mark.parametrize("body", [{"path": CANARY}, {"browser": {"user_agent": CANARY}},
                                  {"browser": {"browser": CANARY}}, {"browser": {"width": CANARY}},
                                  {"browser": {"online": CANARY}}, {"browser": {"width": -1}},
                                  {"browser": {"height": 32001}}])
def test_api_invalid_browser_input_is_rejected_without_echoing_values(dump_client, body):
    browser, miner = dump_client
    response = browser.post("/api/diagnostics", json=body)
    assert response.status_code == 422
    assert CANARY not in response.text
    assert response.json() == {"detail": "invalid_request"}
    assert not (miner.diagnostics.data_dir / "diagnostics").exists()


def test_api_limits_body_and_checks_write_header_and_origin(dump_client):
    browser, miner = dump_client
    assert browser.post("/api/diagnostics", json={"private": CANARY * 400}).status_code == 413
    assert browser.post("/api/diagnostics", json={}, headers={"Origin": "https://foreign.test"}).status_code == 403
    browser.headers.pop("X-TDM-Request")
    assert browser.post("/api/diagnostics", json={}).status_code == 403
    assert not (miner.diagnostics.data_dir / "diagnostics").exists()


def test_api_requires_dashboard_session_and_rejects_revocation(protected, tmp_path, monkeypatch):
    miner = diagnostic_miner(tmp_path)
    monkeypatch.setattr(web, "twitch_client", miner)
    assert protected.post("/api/diagnostics", json={}).status_code == 401
    assert login(protected).status_code == 200
    assert protected.post("/api/diagnostics", json={}).status_code == 200
    assert protected.post("/api/auth/logout").status_code == 200
    assert protected.post("/api/diagnostics", json={}).status_code == 401


def test_api_storage_error_and_unavailable_are_fixed_redacted_codes(dump_client, monkeypatch):
    browser, miner = dump_client
    monkeypatch.setattr(miner.diagnostics, "_write", MagicMock(side_effect=OSError("private path " + CANARY)))
    response = browser.post("/api/diagnostics", json={})
    assert response.status_code == 500 and response.json() == {"detail": "diagnostics_failed"}
    assert CANARY not in response.text
    monkeypatch.setattr(web, "twitch_client", None)
    assert browser.post("/api/diagnostics", json={}).status_code == 503


@asynccontextmanager
async def response_context(response):
    yield response


def http_response(payload, status=200):
    body = json.dumps(payload).encode()
    stream = io.BytesIO(body)
    return SimpleNamespace(status=status, read=AsyncMock(return_value=body), json=AsyncMock(return_value=payload),
                           text=AsyncMock(return_value=body.decode()), release=MagicMock(),
                           content=SimpleNamespace(read=AsyncMock(side_effect=lambda size: stream.read(size))))


@pytest.mark.asyncio
@pytest.mark.parametrize("url,category,method", [
    ("https://www.twitch.tv/" + CANARY, "twitch_page", "GET"),
    ("https://usher.ttvnw.net/api/channel/hls/secret.m3u8?sig=" + CANARY, "watch_playlist", "GET"),
    ("https://video.ttvnw.net/" + CANARY, "watch_segment", "HEAD"),
    ("https://spade.twitch.tv/" + CANARY, "twitch_telemetry", "POST"),
])
async def test_main_http_transport_records_response_without_additional_body_reads(tmp_path, url, category, method):
    miner = diagnostic_miner(tmp_path)
    transport = HTTPClient(miner.settings, miner.gui, miner, ClientType.WEB, diagnostics=miner.diagnostics)
    response = http_response({"id": CANARY, "ok": True})
    session = SimpleNamespace(timeout=aiohttp.ClientTimeout(total=1), request=AsyncMock(return_value=response))
    transport.get_session = AsyncMock(return_value=session)
    async with transport.request(method, url) as returned:
        assert returned is response
    response.read.assert_awaited_once()
    assert miner.diagnostics.apis[category]["samples"][0]["response"]["ok"] is True
    assert CANARY not in json.dumps(miner.diagnostics.apis)


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", [TimeoutError(CANARY), aiohttp.ClientConnectionError(CANARY)])
async def test_main_http_transport_retry_records_fixed_failure_and_success(tmp_path, monkeypatch, failure):
    miner = diagnostic_miner(tmp_path)
    transport = HTTPClient(miner.settings, miner.gui, miner, ClientType.WEB, diagnostics=miner.diagnostics)
    response = http_response({"ok": True})
    transport.get_session = AsyncMock(return_value=SimpleNamespace(timeout=aiohttp.ClientTimeout(total=1),
                                                                 request=AsyncMock(side_effect=[failure, response])))
    backoff = MagicMock(steps=1)
    backoff.__iter__.return_value = iter([0, 0])
    monkeypatch.setattr("src.api.http_client.ExponentialBackoff", lambda **kwargs: backoff)
    monkeypatch.setattr("src.api.http_client.asyncio.sleep", AsyncMock())
    async with transport.request("GET", BrowserSession.VALIDATE_URL):
        pass
    samples = miner.diagnostics.apis["twitch_oauth"]["samples"]
    assert samples[0]["outcome"] == ("timeout" if isinstance(failure, TimeoutError) else "connection")
    assert samples[1]["status"] == 200
    assert CANARY not in json.dumps(samples)


@pytest.mark.asyncio
@pytest.mark.parametrize("url,method,operations,key", [
    (BrowserSession.VALIDATE_URL, "GET", None, "twitch_oauth"),
    (BrowserSession.GQL_URL, "POST", {"query": CHANNEL_BAN_QUERY}, "twitch_gql:ChannelBanStatus"),
])
async def test_authenticated_session_transport_records_all_api_json_without_headers(tmp_path, url, method, operations, key):
    diagnostics = Diagnostics(tmp_path)
    transport = SessionTransport(diagnostics=diagnostics)
    response = http_response({"data": {"user": {"id": "1"}}, "access_token": CANARY})
    transport._http = SimpleNamespace(closed=False, request=MagicMock(return_value=response_context(response)))
    returned = await transport.request(method, url, headers={"Authorization": CANARY, "User-Agent": CANARY}, body=operations)
    assert returned["access_token"] == CANARY  # The actual API result is unchanged.
    assert diagnostics.apis[key]["samples"][0]["response"]["access_token"] == "<redacted>"
    assert CANARY not in json.dumps(diagnostics.apis)


@pytest.mark.asyncio
async def test_session_auth_errors_record_status_and_never_read_rejected_body(tmp_path):
    diagnostics = Diagnostics(tmp_path)
    transport = SessionTransport(diagnostics=diagnostics)
    response = http_response({"message": CANARY}, status=401)
    transport._http = SimpleNamespace(closed=False, request=MagicMock(return_value=response_context(response)))
    with pytest.raises(SessionError):
        await transport.request("GET", BrowserSession.VALIDATE_URL, headers={"Authorization": CANARY})
    response.json.assert_not_awaited()
    assert diagnostics.apis["twitch_oauth"]["samples"][0]["status"] == 401


@pytest.mark.asyncio
async def test_sdk_integrity_response_is_captured_without_sdk_or_auth_values(tmp_path):
    diagnostics = Diagnostics(tmp_path)
    source = ServerSeed.from_dict(seed_data(), now=1000)
    async with sdk_peer() as (browser, _commands, _closed, _state):
        await SDKIssuer(browser, clock=lambda: 1000, diagnostics=diagnostics).issue(source)
    sample = diagnostics.apis["twitch_integrity"]["samples"][0]
    assert sample["status"] == 200
    assert sample["response"]["token"] == "<redacted>"
    assert "private" not in json.dumps(diagnostics.apis)


@pytest.mark.asyncio
@pytest.mark.parametrize("fault", ["cache", "cache_event", "worker", "http_error"])
async def test_sdk_rejected_integrity_responses_still_record_status(tmp_path, fault):
    diagnostics = Diagnostics(tmp_path)
    source = ServerSeed.from_dict(seed_data(), now=1000)
    async with sdk_peer(fault=fault) as (browser, _commands, _closed, _state):
        with pytest.raises(SessionError):
            await SDKIssuer(browser, clock=lambda: 1000, diagnostics=diagnostics).issue(source)
    entry = diagnostics.apis["twitch_integrity"]
    assert entry["count"] == 1
    assert entry["samples"][0]["status"] == (403 if fault == "http_error" else 200)
    assert "private" not in json.dumps(diagnostics.apis)


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [200, 400])
async def test_telegram_captures_sanitized_response_and_preserves_success(tmp_path, monkeypatch, status):
    diagnostics = Diagnostics(tmp_path)
    response = http_response({"ok": status == 200, "result": {"chat": {"id": 123456789}, "text": CANARY}}, status=status)
    session = MagicMock()
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock(return_value=False)
    session.post.return_value = response_context(response)
    monkeypatch.setattr("src.services.telegram_service.aiohttp.ClientSession", lambda: session)
    assert await TelegramNotifier(CANARY, "123456789", diagnostics=diagnostics)._send_message(CANARY) is (status == 200)
    sample = diagnostics.apis["telegram"]["samples"][0]
    assert sample["status"] == status and sample["response"]["ok"] is (status == 200)
    assert CANARY not in json.dumps(diagnostics.apis) and "123456789" not in json.dumps(diagnostics.apis)


@pytest.mark.asyncio
async def test_telegram_new_capture_read_is_bounded_and_large_success_stays_successful(tmp_path, monkeypatch):
    diagnostics = Diagnostics(tmp_path)
    response = http_response({"data": CANARY * 30000})
    session = MagicMock()
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock(return_value=False)
    session.post.return_value = response_context(response)
    monkeypatch.setattr("src.services.telegram_service.aiohttp.ClientSession", lambda: session)
    assert await TelegramNotifier(CANARY, "1", diagnostics=diagnostics)._send_message(CANARY)
    sample = diagnostics.apis["telegram"]["samples"][0]
    assert sample["outcome"] == "truncated" and sample["body_bytes"] == diagnostics.MAX_BODY_BYTES + 1
    assert sum(call.args[0] for call in response.content.read.await_args_list) == diagnostics.MAX_BODY_BYTES + 1
    response.read.assert_not_awaited()


@pytest.mark.asyncio
async def test_websocket_captures_nested_progress_json_and_keeps_raw_routing_unchanged(tmp_path):
    diagnostics = Diagnostics(tmp_path)
    payload = {"type": "MESSAGE", "data": {"topic": "user." + CANARY, "message": json.dumps({
        "type": "drop-progress", "data": {"drop_id": CANARY, "current_progress_min": 8, "required_progress_min": 30}})}}
    receiver = SimpleNamespace(_twitch=SimpleNamespace(diagnostics=diagnostics), _idx=0,
                               _ws=AwaitableValue())
    receiver._ws.set(SimpleNamespace(receive=AsyncMock(side_effect=[
        aiohttp.WSMessage(aiohttp.WSMsgType.TEXT, json.dumps(payload), ""), TimeoutError(),
    ])))
    messages = []
    with pytest.raises(TimeoutError):
        await Websocket._gather_recv(receiver, messages)
    assert messages == [payload]
    retained = diagnostics.apis["twitch_websocket"]["samples"][0]["response"]
    assert retained["data"]["message"]["data"]["current_progress_min"] == 8
    assert CANARY not in json.dumps(diagnostics.apis)


@pytest.mark.asyncio
@pytest.mark.parametrize("api", ["version", "proxy"])
async def test_dashboard_outbound_apis_capture_responses_and_failures(tmp_path, monkeypatch, api):
    miner = diagnostic_miner(tmp_path)
    monkeypatch.setattr(web, "twitch_client", miner)
    response = http_response({"tag_name": "v1.2.3", "html_url": "https://github.com/" + CANARY})
    session = MagicMock()
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock(return_value=False)
    session.get.return_value = response_context(response)
    monkeypatch.setattr("aiohttp.ClientSession", lambda: session)
    category = "github_version" if api == "version" else "twitch_page"
    if api == "version":
        await web.get_version()
    else:
        await web.verify_proxy(web.ProxyVerifyRequest(proxy="http://" + CANARY))
    assert miner.diagnostics.apis[category]["samples"][0]["status"] == 200
    session.get.side_effect = TimeoutError(CANARY)
    if api == "version":
        await web.get_version()
    else:
        await web.verify_proxy(web.ProxyVerifyRequest(proxy="http://" + CANARY))
    assert miner.diagnostics.apis[category]["samples"][-1]["outcome"] == "timeout"
    assert CANARY not in json.dumps(miner.diagnostics.apis)
