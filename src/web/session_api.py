"""Dashboard-protected Twitch login actions and a same-origin VNC bridge."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from contextlib import suppress
from typing import TYPE_CHECKING

from fastapi import APIRouter, HTTPException, Request, WebSocket
from starlette.websockets import WebSocketDisconnect

from src.auth.session_bundle import SessionError


if TYPE_CHECKING:
    from src.core.client import Twitch
    from src.web.auth import WebAuth


class SessionAPI:
    MAX_VIEWERS = 4
    MAX_INPUT_BYTES = 65536

    def __init__(self, auth: WebAuth, get_client: Callable[[], Twitch | None]):
        self.auth, self.get_client = auth, get_client
        self._viewers = 0
        self.router = APIRouter()
        self.router.add_api_route("/api/session", self.status, methods=["GET"])
        self.router.add_api_route("/api/session/finish", self.finish, methods=["POST"])
        self.router.add_api_route("/api/session/retry", self.retry, methods=["POST"])
        self.router.add_api_route("/api/session/logout", self.logout, methods=["POST"])
        self.router.add_api_route("/api/session/helper/enable", self.enable_helper, methods=["POST"])
        self.router.add_api_route("/api/session/helper/cancel", self.cancel_helper, methods=["POST"])
        self.router.add_api_websocket_route("/api/session/vnc", self.viewer)

    def client(self) -> Twitch:
        client = self.get_client()
        if client is None:
            raise HTTPException(503, "session_unavailable")
        return client

    @staticmethod
    def failure(error: SessionError) -> HTTPException:
        return HTTPException(409 if error.code in ("BROWSER_STATE", "HELPER_STATE") else 503,
                             "session_" + error.code.lower())

    async def status(self):
        client = self.client()
        return {**client.session_controller.status(), "browser": client.login_browser.status(),
                "helper": client.helper.status(),
                "logged_in": client._auth_state._logged_in.is_set()}

    async def enable_helper(self, request: Request):
        try:
            token = self.auth.token(request.scope)
            await self.client().enable_helper(lambda: self.auth.allowed(token))
            return await self.status()
        except SessionError as error:
            raise self.failure(error) from None

    async def cancel_helper(self):
        try:
            await self.client().cancel_helper()
            return await self.status()
        except SessionError as error:
            raise self.failure(error) from None

    async def finish(self):
        try:
            await self.client().login_browser.finish()
            return await self.status()
        except SessionError as error:
            raise self.failure(error) from None

    async def retry(self):
        try:
            if self.client().helper.selected:
                raise SessionError("HELPER_STATE")
            await self.client().login_browser.retry()
            return await self.status()
        except SessionError as error:
            raise self.failure(error) from None

    async def logout(self):
        try:
            await self.client().logout()
            return await self.status()
        except (OSError, SessionError):
            raise HTTPException(503, "session_logout_failed") from None

    async def viewer(self, websocket: WebSocket):
        token = self.auth.token(websocket.scope)
        if (websocket.headers.get("origin") != self.auth.origin.expected(websocket)
                or websocket.headers.get("sec-fetch-site") == "cross-site"
                or not self.auth.allowed(token) or self._viewers >= self.MAX_VIEWERS):
            await websocket.close(code=1008)
            return
        client = self.get_client()
        login = getattr(client, "login_browser", None)
        if login is None or login.state != "sign_in" or login.desktop is None:
            await websocket.close(code=1008)
            return
        attempt, port = login.attempt, login.desktop.port
        self._viewers += 1
        writer = None
        tasks: list[asyncio.Task] = []

        def allowed() -> bool:
            return (self.auth.allowed(token) and login.attempt == attempt
                    and login.state == "sign_in" and login.desktop is not None)

        try:
            reader, writer = await asyncio.open_connection("127.0.0.1", port)
            await websocket.accept()

            async def browser_output():
                while allowed():
                    data = await reader.read(65536)
                    if not data or not allowed():
                        return
                    await websocket.send_bytes(data)

            async def browser_input():
                while allowed():
                    message = await websocket.receive()
                    if message["type"] == "websocket.disconnect":
                        return
                    data = message.get("bytes")
                    if not isinstance(data, bytes) or len(data) > self.MAX_INPUT_BYTES or not allowed():
                        return
                    writer.write(data)
                    await writer.drain()

            async def authorization():
                while allowed():
                    await asyncio.sleep(.25)

            tasks = [asyncio.create_task(worker()) for worker in (browser_output, browser_input, authorization)]
            await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        except (OSError, RuntimeError, WebSocketDisconnect):
            pass
        finally:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            if writer is not None:
                writer.close()
                with suppress(OSError):
                    await writer.wait_closed()
            self._viewers -= 1
            with suppress(RuntimeError, OSError):
                await websocket.close(code=1008)
