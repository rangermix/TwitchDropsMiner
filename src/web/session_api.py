"""Helper-only authentication routes; responses never contain Twitch credentials."""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import TYPE_CHECKING

from fastapi import APIRouter, HTTPException, Request

from src.auth.helper_connection import HelperConnections
from src.auth.session_bundle import SessionError


if TYPE_CHECKING:
    from src.core.client import Twitch
    from src.web.auth import WebAuth


class SessionAPI:
    def __init__(self, auth: WebAuth, get_client: Callable[[], Twitch | None]):
        self.auth, self.get_client = auth, get_client
        self.router = APIRouter()
        self.router.add_api_route("/api/session", self.status, methods=["GET"])
        self.router.add_api_route("/api/helper/connect", self.connect, methods=["POST"])
        self.router.add_api_route("/api/helper/session", self.install, methods=["POST"])
        self.router.add_api_route("/api/helper/result", self.result, methods=["GET"])

    def helper(self) -> HelperConnections:
        client = self.get_client()
        helper = getattr(client, "helper", None)
        if not isinstance(helper, HelperConnections):
            raise HTTPException(503, "session_unavailable")
        return helper

    @staticmethod
    def credential(request: Request) -> str:
        header = request.headers.get("authorization", "")
        if not header.startswith("Bearer "):
            raise HTTPException(401, "session_connection")
        return header[7:]

    @staticmethod
    def failure(error: SessionError) -> HTTPException:
        status = {
            "HELPER_DISABLED": 403, "CONNECTION": 401, "BUSY": 429,
            "STOPPED": 503, "BROWSER_START": 503,
        }.get(error.code, 400)
        return HTTPException(status, "session_" + error.code.lower())

    async def status(self):
        return self.helper().status()

    async def connect(self, request: Request):
        try:
            if await request.json() != {}:
                raise ValueError
            return self.helper().connect()
        except SessionError as error:
            raise self.failure(error) from None
        except (ValueError, UnicodeError, RecursionError):
            raise HTTPException(400, "session_format") from None

    async def install(self, request: Request):
        token = self.credential(request)
        try:
            data = json.loads(await request.body())
            return await self.helper().accept(token, data)
        except SessionError as error:
            raise self.failure(error) from None
        except (ValueError, UnicodeError, RecursionError):
            raise HTTPException(400, "session_format") from None

    async def result(self, request: Request):
        token = self.credential(request)
        try:
            return self.helper().result(token)
        except SessionError as error:
            raise self.failure(error) from None
