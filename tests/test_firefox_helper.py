"""Firefox selection, owned lifecycle and the production capture/BiDi adapter."""

import asyncio
import base64
import json
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from src.auth import login_helper
from src.auth.browser_session import BrowserSession
from src.auth.firefox_session import FirefoxExporter, FirefoxPage
from src.auth.imported_session import SessionTransport
from src.auth.server_seed import SDKCookie
from src.auth.session_bundle import SessionError
from src.config import ClientType


class BiDiPeer:
    """Protocol-shaped replies with realistic load/preflight/network ordering."""

    def __init__(self, *, fault=None, cookie=True, request_fragment=False, response_fragment=False,
                 catalog_request_url=None, catalog_response_url=None):
        self.events = asyncio.Queue()
        self.commands = []
        self.contexts = {}
        self.bodies = {}
        self.fault, self.cookie = fault, cookie
        self.request_fragment, self.response_fragment = request_fragment, response_fragment
        self.catalog_request_url, self.catalog_response_url = catalog_request_url, catalog_response_url

    def event(self, method, params):
        self.events.put_nowait({"method": method, "params": params})

    def exchange(self, context, request_id, url, body, *, headers=None, preflight=False):
        request_url = self.catalog_request_url if request_id == "catalog" and self.catalog_request_url else url
        response_url = self.catalog_response_url if request_id == "catalog" and self.catalog_response_url else url
        request = {"request": request_id, "url": request_url + ("#origin=twilight" if self.request_fragment else ""),
                   "method": "OPTIONS" if preflight else "POST",
                   "headers": [{"name": key, "value": {"type": "string", "value": value}}
                               for key, value in (headers or {}).items()]}
        self.event("network.beforeRequestSent", {"context": context, "request": request, "isBlocked": False})
        self.event("network.responseCompleted", {"context": context, "request": request,
            "response": {"url": response_url + ("#origin=twilight" if self.response_fragment else ""),
                         "status": 200, "fromCache": self.fault == "cache"}})
        self.bodies[request_id] = body

    async def command(self, method, params=None, *, timeout=30):
        params = params or {}
        self.commands.append((method, params))
        if method == "browser.createUserContext":
            return {"userContext": "isolated"}
        if method == "browsingContext.create":
            if self.fault == "create":
                raise SessionError("BROWSER_PROTOCOL")
            context = "tab-" + params["userContext"]
            self.contexts[context] = params["userContext"]
            return {"context": context}
        if method == "network.addDataCollector":
            assert params["contexts"] and params["maxEncodedDataSize"] == FirefoxPage.MAX_BODY
            return {"collector": "body-collector"}
        if method == "session.subscribe":
            return {"subscription": "events"}
        if method == "network.addIntercept":
            assert params["urlPatterns"] == [{"type": "string", "pattern": BrowserSession.PAGE}]
            return {"intercept": "page-intercept"}
        if method == "browsingContext.navigate":
            context = params["context"]
            if self.contexts[context] == "isolated":
                self.event("network.beforeRequestSent", {"context": context, "isBlocked": True,
                    "request": {"request": "page", "url": params["url"], "method": "GET", "destination": "document"}})
            else:
                # Regression: normal page loads must not reach CaptureObservation.
                self.event("browsingContext.load", {"context": context})
                self.exchange(context, "preflight", "https://gql.twitch.tv/integrity", None, preflight=True)
                self.exchange("unrelated-tab", "foreign", BrowserSession.GQL_URL, None)
                self.exchange(context, "issued", "https://gql.twitch.tv/integrity",
                              {"token": "issued-token", "expiration": 4600000})
                headers = {"authorization": "OAuth test-auth", "client-id": ClientType.WEB.CLIENT_ID,
                    "client-integrity": "mismatch" if self.fault == "mismatch" else "issued-token",
                    "x-device-id": "test-device", "cookie": "must-not-export"}
                if self.fault == "anonymous":
                    headers.pop("authorization")
                body = ([{"errors": [{"message": "rejected"}]}] if self.fault == "catalog" else
                        [{"data": {"currentUser": {"dropCampaigns": []}}}])
                self.exchange(context, "catalog", BrowserSession.GQL_URL, body, headers=headers)
            return {}
        if method == "network.provideResponse":
            assert params["statusCode"] == 200
            assert b"<html>" in base64.b64decode(params["body"]["value"])
            self.event("browsingContext.load", {"context": "tab-isolated"})
            return {}
        if method == "script.evaluate":
            return {"type": "success", "result": {"type": "string", "value": "Test Firefox"}}
        if method == "script.callFunction":
            headers = json.loads(params["arguments"][0]["value"])
            assert headers["authorization"] == "OAuth test-auth"
            assert "client-integrity" not in headers
            data = {"token": "renewed-token", "expiration": 5600000}
            self.exchange("tab-isolated", "renewed", "https://gql.twitch.tv/integrity", data)
            if self.fault == "sdk_mismatch":
                data = {**data, "token": "not-network-token"}
            return {"type": "success", "result": {"type": "string", "value": json.dumps({"status": 200, "data": data})}}
        if method == "network.getData":
            assert params["request"] not in ("preflight", "foreign")
            assert params["disown"] is True
            return {"bytes": {"type": "base64", "value": base64.b64encode(json.dumps(self.bodies[params["request"]]).encode()).decode()}}
        if method == "storage.getCookies":
            assert params["filter"] == {"name": SDKCookie.NAME, "domain": SDKCookie.DOMAIN}
            isolated = params["partition"]["userContext"] == "isolated"
            if (not self.cookie and not isolated) or self.fault == "sdk_cookie":
                return {"cookies": []}
            return {"cookies": [{"name": SDKCookie.NAME, "domain": SDKCookie.DOMAIN,
                "path": "/", "secure": True, "httpOnly": True, "expiry": 9000,
                "value": {"type": "string", "value": "synthetic-sdk-cookie"}}]}
        return {}


@pytest.mark.asyncio
@pytest.mark.parametrize("request_fragment,response_fragment", [(False, False), (True, False), (False, True), (True, True)])
async def test_firefox_capture_filters_load_preflight_and_other_context_and_reuses_validation(request_fragment, response_fragment):
    remote = BiDiPeer(request_fragment=request_fragment, response_fragment=response_fragment)
    seed = await FirefoxExporter("http://127.0.0.1:9222", remote, clock=lambda: 1000, timeout=1).capture_seed()
    assert seed.bundle.user_agent == "Test Firefox"
    assert seed.bundle.headers["client-integrity"] == "issued-token"
    assert seed.bundle.headers["authorization"] == "OAuth test-auth"
    assert "cookie" not in seed.bundle.headers
    assert seed.cookie.value == "synthetic-sdk-cookie"
    assert remote.commands[-1] == ("browsingContext.close", {"context": "tab-default"})
    assert ("network.removeDataCollector", {"collector": "body-collector"}) in remote.commands


@pytest.mark.asyncio
@pytest.mark.parametrize("side", ["request", "response"])
@pytest.mark.parametrize("url", [
    "http://gql.twitch.tv/gql", "https://gql.twitch.tv.evil.invalid/gql",
    "https://gql.twitch.tv:444/gql", "https://user@gql.twitch.tv/gql",
    "https://gql.twitch.tv/gql/", "https://gql.twitch.tv/gql?unrelated=1",
    "https://gql.twitch.tv/gql%23origin=twilight", "https://foreign.invalid/#https://gql.twitch.tv/gql",
])
async def test_firefox_fragment_normalization_preserves_exact_endpoint_allowlist(side, url):
    remote = BiDiPeer(request_fragment=True, response_fragment=True, **{f"catalog_{side}_url": url})
    with pytest.raises(SessionError, match="CAPTURE_TIMEOUT" if side == "request" else "BROWSER_PROTOCOL"):
        await FirefoxExporter("http://127.0.0.1:9222", remote, clock=lambda: 1000, timeout=.02).capture_seed()
    assert remote.commands[-1] == ("browsingContext.close", {"context": "tab-default"})
    assert not any(method == "network.getData" and params["request"] == "catalog" for method, params in remote.commands)


@pytest.mark.asyncio
@pytest.mark.parametrize("fault", ["mismatch", "anonymous", "catalog"])
@pytest.mark.parametrize("fragment", [False, True])
async def test_firefox_rejects_unverified_capture_and_closes_only_owned_tab(fault, fragment):
    remote = BiDiPeer(fault=fault, request_fragment=fragment, response_fragment=fragment)
    with pytest.raises(SessionError, match="CAPTURE_TIMEOUT"):
        await FirefoxExporter("http://127.0.0.1:9222", remote, clock=lambda: 1000, timeout=.02).capture_seed()
    assert remote.commands[-1] == ("browsingContext.close", {"context": "tab-default"})
    assert not any(method == "browser.close" for method, _ in remote.commands)


@pytest.mark.asyncio
@pytest.mark.parametrize("fault", [None, "cache", "sdk_mismatch", "sdk_cookie", "account"])
async def test_firefox_bootstrap_uses_shared_sdk_proof_and_same_account_validation(monkeypatch, fault):
    remote = BiDiPeer(fault=fault, cookie=False)
    validations = []

    async def validate(_self, bundle, expected):
        validations.append((bundle.headers["client-integrity"], expected))
        if expected is not None:
            assert remote.commands[-1] == ("browser.removeUserContext", {"userContext": "isolated"})
            if fault == "account":
                raise SessionError("ACCOUNT_MISMATCH")
        return SimpleNamespace(user_id=42)

    monkeypatch.setattr(SessionTransport, "validate", validate)
    exporter = FirefoxExporter("http://127.0.0.1:9222", remote, clock=lambda: 1000, timeout=1)
    if fault is None:
        seed = await exporter.capture_seed()
        assert seed.bundle.headers["client-integrity"] == "renewed-token"
        assert validations == [("issued-token", None), ("renewed-token", 42)]
    else:
        with pytest.raises(SessionError):
            await exporter.capture_seed()
    assert remote.commands[-1] == ("browser.removeUserContext", {"userContext": "isolated"})
    assert not any(method in ("storage.deleteCookies", "storage.setCookie") for method, _ in remote.commands)


@pytest.mark.asyncio
@pytest.mark.parametrize("outcome", ["create", "cancel", "error"])
async def test_firefox_isolated_target_disposes_on_failures(outcome):
    remote = BiDiPeer(fault=outcome)
    with pytest.raises((SessionError, asyncio.CancelledError)):
        async with FirefoxExporter("http://127.0.0.1:9222", remote).isolated_target() as page:
            await page.command("Network.enable")
            if outcome == "cancel":
                raise asyncio.CancelledError
            raise SessionError("BROWSER_PROTOCOL")
    assert remote.commands[-1] == ("browser.removeUserContext", {"userContext": "isolated"})


def test_auto_selection_prefers_chrome_and_falls_back_only_when_missing(monkeypatch):
    chrome, chromium, firefox = Path("chrome"), Path("chromium"), Path("firefox")
    monkeypatch.setattr(login_helper.NativeChrome, "find_chrome", Mock(return_value=chrome))
    find_chromium = Mock(return_value=chromium)
    monkeypatch.setattr(login_helper.NativeChromium, "find_chromium", find_chromium)
    find_firefox = Mock(return_value=firefox)
    monkeypatch.setattr(login_helper.NativeFirefox, "find_firefox", find_firefox)
    assert type(login_helper.BrowserSelection.create()) is login_helper.NativeChrome
    find_chromium.assert_not_called()
    find_firefox.assert_not_called()
    monkeypatch.setattr(login_helper.NativeChrome, "find_chrome", Mock(side_effect=SessionError("HELPER_CHROME_MISSING")))
    assert type(login_helper.BrowserSelection.create()) is login_helper.NativeChromium
    find_firefox.assert_not_called()
    find_chromium.side_effect = SessionError("HELPER_CHROMIUM_MISSING")
    assert type(login_helper.BrowserSelection.create()) is login_helper.NativeFirefox
    monkeypatch.setattr(login_helper.NativeChrome, "find_chrome", Mock(side_effect=SessionError("HELPER_BROWSER_OWNER")))
    with pytest.raises(SessionError, match="HELPER_BROWSER_OWNER"):
        login_helper.BrowserSelection.create()


@pytest.mark.parametrize("choice,chrome,firefox,expected", [
    ("firefox", None, None, login_helper.NativeFirefox),
    ("chrome", None, None, login_helper.NativeChrome),
    ("auto", Path("custom-chrome"), None, login_helper.NativeChrome),
    ("auto", None, Path("custom-firefox"), login_helper.NativeFirefox),
])
def test_explicit_browser_selection_never_falls_back(choice, chrome, firefox, expected):
    browser = login_helper.BrowserSelection.create(choice, chrome=chrome, firefox=firefox)
    assert type(browser) is expected
    assert browser.executable == (chrome or firefox)


def test_missing_browsers_report_actionable_code(monkeypatch):
    monkeypatch.setattr(login_helper.NativeChrome, "find_chrome", Mock(side_effect=SessionError("HELPER_CHROME_MISSING")))
    monkeypatch.setattr(login_helper.NativeChromium, "find_chromium", Mock(side_effect=SessionError("HELPER_CHROMIUM_MISSING")))
    monkeypatch.setattr(login_helper.NativeFirefox, "find_firefox", Mock(side_effect=SessionError("HELPER_FIREFOX_MISSING")))
    with pytest.raises(SessionError, match="HELPER_BROWSER_MISSING"):
        login_helper.BrowserSelection.create()


@pytest.mark.parametrize("fault,code", [("pid", "HELPER_BROWSER_OWNER"), ("profile", "HELPER_BROWSER_OWNER"),
                                       ("old", "HELPER_FIREFOX_VERSION"), (None, None)])
def test_firefox_checks_owned_pid_profile_and_minimum_version(monkeypatch, tmp_path, fault, code):
    browser = login_helper.NativeFirefox()
    browser.profile = tmp_path
    browser.process = Mock(pid=100, poll=Mock(return_value=None))
    monkeypatch.setattr(browser, "_owns_pid", lambda pid: pid == 100)
    result = {"capabilities": {"moz:processID": 101 if fault == "pid" else 100,
        "moz:profile": str(tmp_path / "foreign" if fault == "profile" else tmp_path),
        "browserVersion": "142.0" if fault == "old" else "143.0"}}
    if code:
        with pytest.raises(SessionError, match=code):
            browser._require_firefox_owner(result)
    else:
        browser._require_firefox_owner(result)
        assert browser._verified


@pytest.mark.asyncio
async def test_firefox_launch_failure_removes_owned_profile_and_scopes_temporary_files(monkeypatch, tmp_path):
    executable = tmp_path / "firefox"
    executable.touch()
    parent_temp = dict(login_helper.os.environ)
    monkeypatch.setattr(login_helper.tempfile, "tempdir", str(tmp_path))
    calls = []

    def launch(args, **kwargs):
        calls.append(args)
        profile = Path(args[args.index("--profile") + 1])
        for name in ("TMP", "TEMP", "TMPDIR"):
            folder = Path(kwargs["env"][name])
            assert folder.is_relative_to(profile)
            (folder / name).write_text("temporary")
        raise OSError("private-launch-details")

    monkeypatch.setattr(login_helper.subprocess, "Popen", launch)
    browser = login_helper.NativeFirefox(executable)
    with pytest.raises(SessionError, match="HELPER_BROWSER"):
        async with browser:
            pass
    assert browser.profile is None
    assert list(tmp_path.iterdir()) == [executable]
    assert dict(login_helper.os.environ) == parent_temp
    assert "--remote-debugging-port" not in calls[0]


@pytest.mark.asyncio
async def test_firefox_shutdown_never_closes_unverified_remote_and_drains_resources():
    browser = login_helper.NativeFirefox()
    remote, http = Mock(command=AsyncMock(), close=AsyncMock()), Mock(close=AsyncMock())
    browser.remote, browser.http = remote, http
    await browser.close()
    remote.command.assert_not_called()
    remote.close.assert_awaited_once()
    http.close.assert_awaited_once()


@pytest.mark.parametrize("domain", [".twitch.tv", "twitch.tv", "www.twitch.tv"])
@pytest.mark.asyncio
async def test_firefox_waits_for_scoped_login_cookie(domain):
    browser = login_helper.NativeFirefox()
    browser._capturing = True
    browser.process = Mock(poll=Mock(return_value=None))
    browser.remote = Mock(command=AsyncMock(return_value={"cookies": [{"name": "auth-token",
        "domain": domain, "value": {"type": "string", "value": "synthetic-secret"}}]}))
    await browser.wait_authenticated(timeout=1)
    assert browser.remote.command.await_args.args == ("storage.getCookies", {
        "filter": {"name": "auth-token"}, "partition": {"type": "storageKey", "userContext": "default"}})


@pytest.mark.parametrize("platform,relative", [("win32", "Mozilla Firefox/firefox.exe"),
                                                ("darwin", "Applications/Firefox.app/Contents/MacOS/firefox")])
def test_firefox_desktop_discovery(monkeypatch, tmp_path, platform, relative):
    executable = tmp_path / relative
    executable.parent.mkdir(parents=True)
    executable.touch()
    monkeypatch.setattr(sys, "platform", platform)
    monkeypatch.setenv("PROGRAMFILES", str(tmp_path))
    monkeypatch.setenv("PROGRAMFILES(X86)", str(tmp_path / "missing"))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "missing"))
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    is_file = Path.is_file
    monkeypatch.setattr(Path, "is_file", lambda path: path == executable and is_file(path))
    assert login_helper.NativeFirefox.find_firefox() == executable


def test_firefox_linux_discovery_includes_esr(monkeypatch, tmp_path):
    executable = tmp_path / "firefox-esr"
    executable.touch()
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(login_helper.shutil, "which", lambda name: str(executable) if name == "firefox-esr" else None)
    assert login_helper.NativeFirefox.find_firefox() == executable
