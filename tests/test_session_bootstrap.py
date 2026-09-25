"""Native seed bootstrap uses empty owned contexts, never another profile's state."""

import asyncio
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from aiohttp import web

from src.auth.session_bundle import SessionError
from src.auth.session_helper import BrowserExporter
from tests.test_server_renewal import sdk_peer
from tests.test_server_seed import seed_data
from tests.test_session_helper import devtools


SDK_EVENTS = frozenset({
    "Fetch.requestPaused", "Page.loadEventFired", "Network.requestServedFromCache", "Network.loadingFailed",
})


@pytest.mark.asyncio
@pytest.mark.parametrize("fault", [None, "cache", "worker", "mismatch", "missing_cookie"])
@pytest.mark.parametrize("source", ["empty", "expired", "long_integrity"])
async def test_missing_native_cookie_bootstraps_fresh_proof_and_disposes_before_validation(monkeypatch, fault, source):
    from src.auth.imported_session import SessionTransport

    validations, lifecycle = [], []

    async def validate(self, bundle, expected):
        validations.append((bundle.headers["client-integrity"], expected))
        if len(validations) == 2:
            assert lifecycle == ["opened", "disposed"]
            assert expected == 42
        return SimpleNamespace(user_id=42)

    monkeypatch.setattr(SessionTransport, "validate", validate)
    async with sdk_peer(fault=fault) as (browser, commands, _sdk_closed, _state):
        @asynccontextmanager
        async def isolated(self, *, extra_events=frozenset()):
            lifecycle.append("opened")
            try:
                async with browser.start() as address, BrowserExporter(address).target(extra_events=extra_events) as protocol:
                    yield protocol
            finally:
                lifecycle.append("disposed")

        monkeypatch.setattr(BrowserExporter, "isolated_target", isolated, raising=False)
        async with devtools(sdk_cookie=source == "expired", sdk_expiry=999, integrity_expiry=8200 if source == "long_integrity" else 4600) as (address, closed, _commands):
            exporter = BrowserExporter(address, clock=lambda: 1000, timeout=1)
            if fault is None:
                seed = await exporter.capture_seed()
                assert seed.cookie.value == "server-sdk-cookie"
                assert seed.bundle.headers["client-integrity"] == "private-new-integrity"
                assert validations == [("new-integrity", None), ("private-new-integrity", 42)]
            else:
                with pytest.raises(SessionError):
                    await exporter.capture_seed()
                assert validations == [("new-integrity", None)]
            assert closed == ["owned-tab"]
        assert lifecycle == ["opened", "disposed"]
        methods = [method for method, _ in commands]
        assert "Network.setCookies" not in methods
        assert "Storage.clearDataForOrigin" not in methods


@pytest.mark.asyncio
async def test_bootstrap_wrong_account_closes_transport_and_returns_no_seed(monkeypatch):
    from src.auth.imported_session import SessionTransport

    validate = AsyncMock(side_effect=[SimpleNamespace(user_id=42), SessionError("ACCOUNT_MISMATCH")])
    close = AsyncMock()
    monkeypatch.setattr(SessionTransport, "validate", validate)
    monkeypatch.setattr(SessionTransport, "close", close)
    async with sdk_peer() as (browser, _commands, sdk_closed, _state):
        @asynccontextmanager
        async def isolated(self, *, extra_events=frozenset()):
            async with browser.start() as address, BrowserExporter(address).target(extra_events=extra_events) as protocol:
                yield protocol

        monkeypatch.setattr(BrowserExporter, "isolated_target", isolated, raising=False)
        async with devtools(sdk_cookie=False) as (address, closed, _commands):
            with pytest.raises(SessionError, match="ACCOUNT_MISMATCH"):
                await BrowserExporter(address, clock=lambda: 1000, timeout=1).capture_seed()
            assert closed == ["owned-tab"]
        assert sdk_closed == ["owned"]
        assert validate.await_args.args[1] == 42
        close.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("phase", ["context_disposal", "validation"])
async def test_bootstrap_expiring_before_export_preserves_existing_files(monkeypatch, tmp_path, phase):
    from argparse import Namespace

    from src.auth.imported_session import SessionTransport
    from src.auth.session_helper import SessionHelper

    clock, validations = [1000], []

    async def validate(self, bundle, expected):
        validations.append(expected)
        if expected is not None:
            clock[0] = bundle.expires_at + 1
        return SimpleNamespace(user_id=42)

    monkeypatch.setattr(SessionTransport, "validate", validate)
    async with sdk_peer() as (browser, _commands, _closed, _state):
        @asynccontextmanager
        async def isolated(self, *, extra_events=frozenset()):
            async with browser.start() as address, BrowserExporter(address).target(extra_events=extra_events) as protocol:
                yield protocol
            if phase == "context_disposal":
                clock[0] = 5601

        monkeypatch.setattr(BrowserExporter, "isolated_target", isolated)
        async with devtools(sdk_cookie=False) as (address, _closed, _commands):
            exporter = BrowserExporter(address, clock=lambda: clock[0], timeout=1)
            monkeypatch.setattr("src.auth.session_helper.BrowserExporter", lambda _address: exporter)
            output, seed = tmp_path / "session.json", tmp_path / "seed.json"
            for path in (output, seed):
                path.write_text("previous export")
            with pytest.raises(SessionError, match="EXPIRED"):
                await SessionHelper.run(Namespace(command="export", browser=address, output=str(output), server_seed=str(seed)))
            assert output.read_text() == seed.read_text() == "previous export"
            assert validations == ([None] if phase == "context_disposal" else [None, 42])


@asynccontextmanager
async def context_peer(*, bad_page_endpoint=False, create_failure=False, dispose_failure=False, pause_method=None, malformed=None):
    address, commands = [], []
    paused, disconnected = asyncio.Event(), asyncio.Event()

    async def version(request):
        return web.json_response({"webSocketDebuggerUrl": address[0].replace("http:", "ws:") + "/devtools/browser/owned"})

    async def pages(request):
        endpoint = "ws://remote.invalid:9222/devtools/page/owned" if bad_page_endpoint else address[0].replace("http:", "ws:") + "/devtools/page/isolated"
        if malformed == "mismatched_endpoint":
            endpoint = address[0].replace("http:", "ws:") + "/devtools/page/existing"
        page = {"id": "isolated", "webSocketDebuggerUrl": endpoint}
        if malformed == "null_target":
            page.pop("id")
        return web.json_response(page if malformed == "pages_object" else [page])

    async def socket(request):
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        async for message in ws:
            data = message.json()
            method, params = data["method"], data.get("params", {})
            commands.append((method, params))
            if method == pause_method:
                paused.set()
                continue
            result = {}
            if method == "Target.createBrowserContext":
                assert params == {"disposeOnDetach": True}
                result = {"browserContextId": "private-context"}
            elif method == "Target.createTarget":
                assert params == {"url": "about:blank", "browserContextId": "private-context"}
                if create_failure:
                    await ws.send_json({"id": data["id"], "error": {"code": -1, "message": "private"}})
                    continue
                result = {"targetId": None if malformed == "null_target" else "isolated"}
            elif method == "Target.disposeBrowserContext":
                assert params == {"browserContextId": "private-context"}
                if dispose_failure:
                    await ws.send_json({"id": data["id"], "error": {"code": -1, "message": "private"}})
                    continue
            await ws.send_json({"id": data["id"], "result": result})
        if request.path == "/devtools/browser/owned":
            disconnected.set()
        return ws

    app = web.Application()
    app.router.add_get("/json/version", version)
    app.router.add_get("/json/list", pages)
    app.router.add_get("/devtools/browser/owned", socket)
    app.router.add_get("/devtools/page/isolated", socket)
    app.router.add_get("/devtools/page/existing", socket)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    address.append(f"http://127.0.0.1:{site._server.sockets[0].getsockname()[1]}")
    try:
        yield address[0], commands, paused, disconnected
    finally:
        await runner.cleanup()


@pytest.mark.asyncio
@pytest.mark.parametrize("malformed", ["null_target", "mismatched_endpoint", "pages_object"])
async def test_unidentified_or_mismatched_page_is_never_used(malformed):
    async with context_peer(malformed=malformed) as (address, commands, _paused, disconnected):
        with pytest.raises(SessionError, match="BROWSER_PROTOCOL"):
            async with BrowserExporter(address).isolated_target() as protocol:
                await protocol.command("Network.enable")
        assert "Network.enable" not in [method for method, _ in commands]
        assert commands[-1][0] == "Target.disposeBrowserContext"
        await asyncio.wait_for(disconnected.wait(), 1)


@pytest.mark.asyncio
@pytest.mark.parametrize("outcome", ["success", "failure", "cancel", "bad_endpoint", "create_failure", "dispose_failure"])
async def test_isolated_native_context_owns_target_and_disposes_on_every_exit(outcome):
    async with context_peer(bad_page_endpoint=outcome == "bad_endpoint", create_failure=outcome == "create_failure", dispose_failure=outcome == "dispose_failure") as (address, commands, _paused, disconnected):
        try:
            async with BrowserExporter(address).isolated_target() as protocol:
                await protocol.command("Network.enable")
                if outcome == "failure":
                    raise SessionError("TEST")
                if outcome == "cancel":
                    raise asyncio.CancelledError
        except (SessionError, asyncio.CancelledError) as error:
            assert outcome != "success"
            assert "private" not in str(error)
        else:
            assert outcome == "success"
        methods = [method for method, _ in commands]
        assert methods[0:2] == ["Target.createBrowserContext", "Target.createTarget"]
        assert methods[-1] == "Target.disposeBrowserContext"
        assert "Browser.close" not in methods
        await asyncio.wait_for(disconnected.wait(), 1)


@pytest.mark.asyncio
@pytest.mark.parametrize("pause_method", ["Target.createBrowserContext", "Target.createTarget", "Network.enable", "Target.disposeBrowserContext"])
async def test_cancel_during_context_lifecycle_disconnects_dispose_on_detach_controller(pause_method):
    async with context_peer(pause_method=pause_method) as (address, commands, paused, disconnected):
        async def use():
            async with BrowserExporter(address).isolated_target() as protocol:
                await protocol.command("Network.enable")

        task = asyncio.create_task(use())
        await asyncio.wait_for(paused.wait(), 1)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        await asyncio.wait_for(disconnected.wait(), 1)
        assert commands[0] == ("Target.createBrowserContext", {"disposeOnDetach": True})
        assert "Browser.close" not in [method for method, _ in commands]


@pytest.mark.asyncio
async def test_cookie_free_acquisition_still_rejects_expired_replacement():
    from src.auth.server_renewal import SDKAcquisition
    from src.auth.session_bundle import SessionBundle

    async with sdk_peer(fault="expired_cookie") as (browser, _commands, closed, _state):
        async with browser.start() as address, BrowserExporter(address).target(extra_events=SDK_EVENTS) as protocol:
            with pytest.raises(SessionError, match="SDK_EXPIRED"):
                await SDKAcquisition(clock=lambda: 1000).run(protocol, SessionBundle.from_dict(seed_data()["bundle"], now=1000))
        assert closed == ["owned"]
