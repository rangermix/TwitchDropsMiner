"""Portable session contracts: no live credentials or Twitch requests."""

import asyncio
import json
import stat
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.auth.imported_session import ImportedSession, SessionTransport
from src.auth.session_bundle import SessionBundle, SessionError
from src.config import ClientType
from src.exceptions import ExitRequest


def bundle_data(now=1000, integrity="test-integrity", token="test-token"):
    return {
        "version": 1,
        "captured_at": now,
        "expires_at": now + 3600,
        "user_agent": "Test Chrome",
        "headers": {
            "authorization": f"OAuth {token}",
            "client-id": ClientType.WEB.CLIENT_ID,
            "client-integrity": integrity,
            "x-device-id": "test-device",
            "client-session-id": "test-session",
        },
    }


def catalog():
    return [
        {"data": {"currentUser": {"inventory": {
            "gameEventDrops": [], "dropCampaignsInProgress": [],
        }}}},
        {"data": {"currentUser": {"dropCampaigns": [{"id": "campaign"}]}}},
    ]


def transport(user_id="42"):
    service = SessionTransport()
    service.request = AsyncMock(side_effect=[
        {"client_id": ClientType.WEB.CLIENT_ID, "user_id": user_id}, catalog(),
    ])
    return service


def imported(tmp_path, clock, service=None):
    return ImportedSession(tmp_path / "imported-session.json", transport=service or transport(), clock=lambda: clock[0])


def login():
    return SimpleNamespace(import_pending=AsyncMock())


def test_bundle_redaction_and_strict_format():
    bundle = SessionBundle.from_dict(bundle_data(), now=1000)
    assert bundle.to_dict() == bundle_data()
    assert "test-token" not in repr(bundle)
    assert "test-integrity" not in repr(bundle)
    for mutate in (
        lambda d: d.update(version=True),
        lambda d: d.update(expires_at=float("nan")),
        lambda d: d.update(captured_at=2000),
        lambda d: d["headers"].update(cookie="secret"),
        lambda d: d["headers"].update(authorization="OAuth secret\r\nCookie: bad"),
        lambda d: d["headers"].update({"client-integrity": ""}),
        lambda d: d["headers"].update({"client-id": ClientType.ANDROID_APP.CLIENT_ID}),
        lambda d: d.update(user_agent="Chrome\nInjected: header"),
        lambda d: d.update(destination="https://evil.test"),
    ):
        data = bundle_data()
        mutate(data)
        with pytest.raises(SessionError) as error:
            SessionBundle.from_dict(data, now=1000)
        assert "secret" not in str(error.value)
    with pytest.raises(SessionError):
        SessionBundle.from_json(b" " * 65537, now=1000)


@pytest.mark.asyncio
async def test_import_validates_before_private_save_and_preserves_android(tmp_path):
    clock = [1000]
    service = transport()
    session = imported(tmp_path, clock, service)
    cookie = tmp_path / "cookies.jar"
    cookie.write_bytes(b"existing android credentials")
    result = await session.install(bundle_data())
    assert result["state"] == "ready" and result["user_id"] == 42
    assert result["generation"] == 1
    assert "test-token" not in json.dumps(result)
    assert "test-integrity" not in json.dumps(result)
    assert cookie.read_bytes() == b"existing android credentials"
    assert stat.S_IMODE(session.path.stat().st_mode) == 0o600
    identity = await session.authenticate(login())
    assert identity.user_id == 42 and identity.token == "test-token"
    assert service.request.await_count == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["identity", "catalog", "expiry", "account"])
async def test_invalid_replacement_keeps_previous_context_and_disk(tmp_path, failure):
    clock = [1000]
    service = transport()
    session = imported(tmp_path, clock, service)
    await session.install(bundle_data())
    original = session.path.read_bytes()
    clock[0] = 1100
    candidate = bundle_data(1100, "new-integrity", "new-token")
    identity = {"client_id": ClientType.WEB.CLIENT_ID, "user_id": "42"}
    response = catalog()
    if failure == "identity":
        identity["client_id"] = ClientType.SMARTBOX.CLIENT_ID
    elif failure == "account":
        identity["user_id"] = "43"
    elif failure == "catalog":
        response[1] = {"errors": [{"message": "failed integrity check secret-response"}]}
    else:
        candidate["expires_at"] = 1099
    service.request.side_effect = [identity, response]
    with pytest.raises(SessionError) as error:
        await session.install(candidate)
    assert "secret-response" not in str(error.value)
    assert session.path.read_bytes() == original
    assert (await session.authenticate(login())).token == "test-token"


@pytest.mark.asyncio
async def test_fresh_same_account_context_replaces_as_a_unit_and_rejects_replay(tmp_path):
    clock = [1000]
    service = transport()
    session = imported(tmp_path, clock, service)
    old = bundle_data()
    await session.install(old)
    clock[0] = 1100
    fresh = bundle_data(1100, "new-integrity", "new-token")
    fresh["headers"]["x-device-id"] = "new-device"
    service.request.side_effect = [
        {"client_id": ClientType.WEB.CLIENT_ID, "user_id": "42"}, catalog(), catalog()[0],
    ]
    await session.install(fresh)
    assert session.status()["generation"] == 2
    identity = await session.authenticate(login())
    assert identity.token == "new-token" and identity.device_id == "new-device"
    await session.gql({"operationName": "Inventory"})
    headers = service.request.await_args.kwargs["headers"]
    assert headers["authorization"] == "OAuth new-token"
    assert headers["client-integrity"] == "new-integrity"
    assert "cookie" not in headers
    for data in (old, fresh):
        with pytest.raises(SessionError, match="REPLAY"):
            await session.install(data)


@pytest.mark.asyncio
async def test_expired_session_waits_for_new_import_and_stop_interrupts(tmp_path):
    clock = [1000]
    session = imported(tmp_path, clock)
    await session.install(bundle_data())
    clock[0] = 4601
    form = login()
    waiter = asyncio.create_task(session.authenticate(form))
    await asyncio.sleep(0)
    assert not waiter.done()
    assert session.status()["state"] == "expired"
    session.request_stop()
    with pytest.raises(ExitRequest):
        await asyncio.wait_for(waiter, 1)


@pytest.mark.asyncio
async def test_restart_revalidates_saved_identity_and_catalog(tmp_path):
    clock = [1000]
    first = imported(tmp_path, clock)
    await first.install(bundle_data())
    server = transport()
    restarted = imported(tmp_path, clock, server)
    assert (await restarted.authenticate(login())).user_id == 42
    assert server.request.await_count == 2
    assert restarted.status()["generation"] == 1


@pytest.mark.asyncio
async def test_expiry_wait_resumes_only_after_valid_new_context(tmp_path):
    clock = [1000]
    service = transport()
    session = imported(tmp_path, clock, service)
    await session.install(bundle_data())
    clock[0] = 4601
    form = login()
    waiter = asyncio.create_task(session.authenticate(form))
    await asyncio.sleep(0)
    service.request.side_effect = [
        {"client_id": ClientType.WEB.CLIENT_ID, "user_id": "42"}, catalog(),
    ]
    await session.install(bundle_data(4601, "new-integrity"))
    assert (await asyncio.wait_for(waiter, 1)).user_id == 42
    assert [call.args for call in form.import_pending.await_args_list] == [(True,), (False,)]


@pytest.mark.asyncio
async def test_disk_failure_does_not_activate_unpersisted_context(tmp_path):
    clock = [1000]
    session = imported(tmp_path, clock)
    session._file.write = lambda _: (_ for _ in ()).throw(SessionError("SAVE"))
    with pytest.raises(SessionError, match="SAVE"):
        await session.install(bundle_data())
    assert session.status()["generation"] == 0
    assert not session.path.exists()


@pytest.mark.asyncio
async def test_real_transport_never_follows_redirect_or_keeps_cookies(monkeypatch):
    from aiohttp import web

    seen = []

    async def receive(request):
        seen.append(dict(request.headers))
        response = web.json_response({"ok": True})
        response.set_cookie("unexpected", "must-not-send")
        return response

    async def redirect(request):
        return web.Response(status=302, headers={"Location": "/sink?secret=must-not-echo"})

    app = web.Application()
    app.router.add_get("/validate", receive)
    app.router.add_post("/gql", redirect)
    app.router.add_post("/sink", receive)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]
    from src.auth.browser_session import BrowserSession

    monkeypatch.setattr(BrowserSession, "VALIDATE_URL", f"http://127.0.0.1:{port}/validate")
    monkeypatch.setattr(BrowserSession, "GQL_URL", f"http://127.0.0.1:{port}/gql")
    service = SessionTransport()
    try:
        for _ in range(2):
            await service.request("GET", BrowserSession.VALIDATE_URL, headers={"Authorization": "OAuth secret"})
        with pytest.raises(SessionError) as error:
            await service.request("POST", BrowserSession.GQL_URL, headers={"Authorization": "OAuth secret"})
        assert "secret" not in str(error.value)
        assert len(seen) == 2
        assert all("Cookie" not in headers for headers in seen)
    finally:
        await service.close()
        await runner.cleanup()


@pytest.mark.asyncio
async def test_expiry_between_auth_check_and_request_waits_without_sending(tmp_path):
    clock = [1000]
    service = transport()
    session = imported(tmp_path, clock, service)
    await session.install(bundle_data())
    form = login()
    await session.authenticate(form)
    original = session.authenticate

    async def expire_after_check(login):
        identity = await original(login)
        if clock[0] == 1000:
            clock[0] = 4601
        return identity

    session.authenticate = expire_after_check
    waiter = asyncio.create_task(session.gql({"operationName": "Inventory"}))
    await asyncio.sleep(0)
    assert not waiter.done()
    assert service.request.await_count == 2
    service.request.side_effect = [
        {"client_id": ClientType.WEB.CLIENT_ID, "user_id": "42"}, catalog(), catalog()[0],
    ]
    await session.install(bundle_data(4601, "new-integrity"))
    assert await asyncio.wait_for(waiter, 1) == catalog()[0]


@pytest.mark.asyncio
@pytest.mark.parametrize("stop", [False, True])
async def test_restore_validation_expiry_or_stop_waits_safely(tmp_path, stop):
    clock = [1000]
    first = imported(tmp_path, clock)
    await first.install(bundle_data())
    service = transport()
    restored = imported(tmp_path, clock, service)
    original = service.validate

    async def validate(*args):
        result = await original(*args)
        if stop:
            restored.request_stop()
            raise SessionError("REQUEST")
        clock[0] = 4601
        return result

    service.validate = validate
    task = asyncio.create_task(restored.authenticate(login()))
    await asyncio.sleep(0)
    if not stop:
        assert not task.done()
        restored.request_stop()
    with pytest.raises(ExitRequest):
        await asyncio.wait_for(task, 1)


@pytest.mark.asyncio
@pytest.mark.parametrize("response", [SessionError("AUTH"), {"errors": [{"message": "failed integrity check"}]}])
async def test_rejected_context_waits_and_only_retries_after_replacement(tmp_path, response):
    clock = [1000]
    service = transport()
    session = imported(tmp_path, clock, service)
    await session.install(bundle_data())
    await session.authenticate(login())
    service.request.side_effect = [response]
    task = asyncio.create_task(session.gql({"operationName": "Inventory"}))
    await asyncio.sleep(0)
    assert not task.done()
    assert session.status()["state"] == "waiting"
    assert service.request.await_count == 3
    clock[0] = 1100
    service.request.side_effect = [
        {"client_id": ClientType.WEB.CLIENT_ID, "user_id": "42"}, catalog(), catalog()[0],
    ]
    await session.install(bundle_data(1100, "new-integrity"))
    assert await asyncio.wait_for(task, 1) == catalog()[0]


@pytest.mark.asyncio
async def test_partial_batch_retries_only_definitively_rejected_rows(tmp_path):
    clock = [1000]
    service = transport()
    session = imported(tmp_path, clock, service)
    await session.install(bundle_data())
    await session.authenticate(login())
    success = {"data": {"claim": "done"}}
    service.request.side_effect = [[success, {"errors": [{"message": "failed integrity check"}]}]]
    task = asyncio.create_task(session.gql([{"operationName": "ClaimDrop"}, {"operationName": "Inventory"}]))
    await asyncio.sleep(0)
    assert not task.done()
    clock[0] = 1100
    service.request.side_effect = [
        {"client_id": ClientType.WEB.CLIENT_ID, "user_id": "42"}, catalog(), [{"data": {"inventory": "ok"}}],
    ]
    await session.install(bundle_data(1100, "new-integrity"))
    assert await asyncio.wait_for(task, 1) == [success, {"data": {"inventory": "ok"}}]
    assert service.request.await_args.kwargs["body"] == [{"operationName": "Inventory"}]


def test_deep_malformed_json_has_stable_error(tmp_path):
    raw = b"[" * 20000 + b"]" * 20000
    with pytest.raises(SessionError, match="FORMAT"):
        SessionBundle.from_json(raw)
    from src.auth.session_bundle import PrivateSessionFile

    path = tmp_path / "bad.json"
    path.write_bytes(raw)
    with pytest.raises(SessionError, match="FILE"):
        PrivateSessionFile(path).read()


def test_resolver_execution_errors_are_never_replayed():
    for row in (
        {"data": None, "errors": [{"message": "failed integrity check"}]},
        {"errors": [{"message": "failed integrity check", "path": ["claimDrop"]}]},
        {"errors": [{"message": "Unauthorized"}]},
    ):
        assert not ImportedSession._auth_rejection(row)


@pytest.mark.asyncio
async def test_malformed_batch_row_is_a_stable_error(tmp_path):
    session = imported(tmp_path, [1000])
    await session.install(bundle_data())
    await session.authenticate(login())
    session._transport.request.side_effect = [[None]]
    with pytest.raises(SessionError, match="RESPONSE"):
        await session.gql([{"operationName": "Inventory"}])
