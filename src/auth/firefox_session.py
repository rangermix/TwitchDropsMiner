"""Firefox BiDi adapter for the shared, verified session capture flow."""

from __future__ import annotations

import asyncio
import base64
import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress
from typing import Any

from src.auth.browser_session import BrowserSession
from src.auth.server_seed import SDKCookie
from src.auth.session_bundle import SessionError
from src.auth.session_helper import BrowserExporter, CaptureProtocol, DevToolsConnection


class FirefoxPage:
    """Translate only the operations used by BrowserExporter and SDKAcquisition."""

    EVENTS = frozenset({
        "network.beforeRequestSent", "network.responseCompleted", "network.fetchError",
        "browsingContext.load",
    })
    MAX_BODY = 8 * 1024 * 1024

    def __init__(self, remote: DevToolsConnection, context: str, user_context: str, *, isolated: bool,
                 extra_events: frozenset[str] = frozenset()):
        self.remote, self.context, self.user_context = remote, context, user_context
        self.isolated = isolated
        self.extra_events = extra_events
        self.events: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue(maxsize=256)
        self.collector: str | None = None
        self.subscription: str | None = None
        self.intercept: str | None = None
        self._forwarder: asyncio.Task[None] | None = None

    @staticmethod
    def string(value: Any) -> str:
        if not isinstance(value, dict) or not isinstance(value.get("value"), str):
            raise SessionError("BROWSER_PROTOCOL")
        if value.get("type") == "string":
            return value["value"]
        if value.get("type") == "base64":
            try:
                return base64.b64decode(value["value"], validate=True).decode("utf-8")
            except (ValueError, UnicodeError):
                pass
        raise SessionError("BROWSER_PROTOCOL")

    @staticmethod
    def identifier(result: Any, key: str) -> str:
        value = result.get(key) if isinstance(result, dict) else None
        if not isinstance(value, str) or not value:
            raise SessionError("BROWSER_PROTOCOL")
        return value

    @staticmethod
    def http_url(value: Any) -> str:
        if not isinstance(value, str):
            raise SessionError("BROWSER_PROTOCOL")
        # Firefox BiDi retains fragments such as Twitch's #origin=twilight.
        # Fragments are never sent in HTTP; preserve every other URL component.
        return value.partition("#")[0]

    def emit(self, method: str, params: dict[str, Any]) -> None:
        self.events.put_nowait({"method": method, "params": params})

    async def _forward(self) -> None:
        try:
            while True:
                event = await self.remote.events.get()
                if event is None:
                    return
                method, params = event["method"], event["params"]
                if params.get("context") != self.context:
                    continue
                if method == "browsingContext.load":
                    if "Page.loadEventFired" in self.extra_events:
                        self.emit("Page.loadEventFired", {})
                    continue
                request = {**params["request"], "url": self.http_url(params["request"]["url"])}
                request_id = request["request"]
                if method == "network.beforeRequestSent" and params.get("isBlocked"):
                    self.emit("Fetch.requestPaused", {
                        "requestId": request_id, "request": request,
                        "resourceType": "Document" if request.get("destination") == "document" else "Other",
                    })
                    continue
                if (request.get("url") not in (BrowserSession.GQL_URL, "https://gql.twitch.tv/integrity")
                        or request.get("method") == "OPTIONS"):
                    continue
                if method == "network.beforeRequestSent":
                    headers = {header["name"]: self.string(header["value"]) for header in request["headers"]}
                    self.emit("Network.requestWillBeSent", {"requestId": request_id,
                        "request": {**request, "headers": headers}})
                elif method == "network.responseCompleted":
                    response = params["response"]
                    response_url = self.http_url(response["url"])
                    if response_url != request["url"]:
                        raise SessionError("BROWSER_PROTOCOL")
                    self.emit("Network.responseReceived", {"requestId": request_id, "response": {
                        **response, "url": response_url,
                        "fromDiskCache": response.get("fromCache", False),
                    }})
                    self.emit("Network.loadingFinished", {"requestId": request_id})
                elif method == "network.fetchError":
                    self.emit("Network.loadingFailed", {"requestId": request_id})
        except (SessionError, KeyError, TypeError, ValueError, asyncio.QueueFull):
            pass
        finally:
            if self.events.full():
                self.events.get_nowait()
            self.events.put_nowait(None)

    async def body(self, request_id: str) -> Any:
        result = await self.remote.command("network.getData", {
            "request": request_id, "dataType": "response", "collector": self.collector, "disown": True,
        })
        try:
            raw = self.string(result["bytes"])
            if len(raw.encode("utf-8")) > self.MAX_BODY:
                raise SessionError("CAPTURE_LIMIT")
            return json.loads(raw)
        except (KeyError, TypeError, ValueError, RecursionError):
            raise SessionError("BROWSER_PROTOCOL") from None

    async def command(self, method: str, params: dict[str, Any] | None = None, *, timeout: float = 30) -> Any:
        params = params or {}
        call = self.remote.command
        if method == "Network.enable":
            self.collector = self.identifier(await call("network.addDataCollector", {
                "dataTypes": ["response"], "maxEncodedDataSize": self.MAX_BODY, "contexts": [self.context],
            }), "collector")
            self.subscription = self.identifier(await call("session.subscribe", {
                "events": sorted(self.EVENTS), "contexts": [self.context],
            }), "subscription")
            self._forwarder = asyncio.create_task(self._forward())
        elif method == "Network.setCacheDisabled":
            await call("network.setCacheBehavior", {"cacheBehavior": "bypass", "contexts": [self.context]})
        elif method == "Network.setBypassServiceWorker":
            # SDK bootstrap uses a brand-new user context: it has no service workers.
            if not self.isolated:
                raise SessionError("BROWSER_PROTOCOL")
        elif method == "Page.enable":
            pass  # The scoped subscription above already includes load events.
        elif method == "Fetch.enable":
            if not self.isolated:
                raise SessionError("BROWSER_PROTOCOL")
            self.intercept = self.identifier(await call("network.addIntercept", {
                "phases": ["beforeRequestSent"], "contexts": [self.context],
                "urlPatterns": [{"type": "string", "pattern": BrowserSession.PAGE}],
            }), "intercept")
        elif method == "Fetch.fulfillRequest":
            await call("network.provideResponse", {
                "request": params["requestId"], "statusCode": params["responseCode"],
                "headers": [{"name": h["name"], "value": {"type": "string", "value": h["value"]}}
                            for h in params["responseHeaders"]],
                "body": {"type": "base64", "value": params["body"]},
            })
        elif method == "Page.navigate":
            await call("browsingContext.navigate", {"context": self.context, "url": params["url"], "wait": "none"})
        elif method == "Runtime.evaluate":
            if params["expression"] == "globalThis":
                return {"result": {"objectId": self.context}}
            result = await call("script.evaluate", {"expression": params["expression"],
                "target": {"context": self.context}, "awaitPromise": False}, timeout=timeout)
            if result.get("type") != "success":
                raise SessionError("BROWSER_PROTOCOL")
            return {"result": {"value": self.string(result["result"])}}
        elif method == "Runtime.callFunctionOn":
            # JSON arguments/results avoid browser-specific remote object formats.
            result = await call("script.callFunction", {
                "functionDeclaration": "async (...args) => JSON.stringify(await ("
                    + params["functionDeclaration"] + ")(...args.map(JSON.parse)))",
                "target": {"context": self.context}, "awaitPromise": True,
                "arguments": [{"type": "string", "value": json.dumps(arg["value"])} for arg in params["arguments"]],
            }, timeout=timeout)
            if result.get("type") != "success":
                raise SessionError("SDK_ISSUANCE")
            return {"result": {"value": json.loads(self.string(result["result"]))}}
        elif method == "Network.getCookies":
            if params != {"urls": [SDKCookie.URL]}:
                raise SessionError("BROWSER_PROTOCOL")
            result = await call("storage.getCookies", {
                "filter": {"name": SDKCookie.NAME, "domain": SDKCookie.DOMAIN},
                "partition": {"type": "storageKey", "userContext": self.user_context},
            })
            return {"cookies": [{**c, "value": self.string(c["value"]), "expires": c.get("expiry")}
                                for c in result["cookies"]]}
        else:
            raise SessionError("BROWSER_PROTOCOL")
        return {}

    async def close(self) -> None:
        if self._forwarder is not None:
            self._forwarder.cancel()
            await asyncio.gather(self._forwarder, return_exceptions=True)
        for method, key, value in (
            ("network.removeIntercept", "intercept", self.intercept),
            ("session.unsubscribe", "subscriptions", [self.subscription] if self.subscription else None),
            ("network.removeDataCollector", "collector", self.collector),
        ):
            if value is not None:
                with suppress(SessionError, TimeoutError):
                    await self.remote.command(method, {key: value}, timeout=2)
        # Events left by a closed tab must not enter the next isolated capture.
        while not self.remote.events.empty():
            self.remote.events.get_nowait()


class FirefoxExporter(BrowserExporter):
    """Reuse capture and SDK proof checks through a browser-owned BiDi session."""

    def __init__(self, address: str, remote: DevToolsConnection, **kwargs: Any):
        super().__init__(address, **kwargs)
        self.remote = remote

    @asynccontextmanager
    async def _target(self, *, isolated: bool, extra_events: frozenset[str]) -> AsyncIterator[CaptureProtocol]:
        user_context = "default"
        context = None
        page = None
        try:
            if isolated:
                user_context = FirefoxPage.identifier(await self.remote.command("browser.createUserContext"), "userContext")
            context = FirefoxPage.identifier(await self.remote.command("browsingContext.create", {
                "type": "tab", "userContext": user_context,
            }), "context")
            page = FirefoxPage(self.remote, context, user_context, isolated=isolated, extra_events=extra_events)
            yield page
        except (KeyError, TypeError, ValueError):
            raise SessionError("BROWSER_PROTOCOL") from None
        finally:
            if page is not None:
                await page.close()
            if context is not None:
                with suppress(SessionError, TimeoutError):
                    await self.remote.command("browsingContext.close", {"context": context}, timeout=2)
            if isolated and user_context != "default":
                with suppress(SessionError, TimeoutError):
                    await self.remote.command("browser.removeUserContext", {"userContext": user_context}, timeout=2)

    def target(self, *, extra_events: frozenset[str] = frozenset()) -> Any:
        return self._target(isolated=False, extra_events=extra_events)

    def isolated_target(self, *, extra_events: frozenset[str] = frozenset()) -> Any:
        return self._target(isolated=True, extra_events=extra_events)
