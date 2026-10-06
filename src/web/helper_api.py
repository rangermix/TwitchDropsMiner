"""Dashboard-enabled admission and bearer-authenticated desktop session transfer."""

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


class HelperAPI:
    def __init__(self, auth: WebAuth, get_client: Callable[[], Twitch | None]):
        self.auth, self.get_client = auth, get_client
        self.router = APIRouter()
        self.router.add_api_route("/api/helper/connect", self.connect, methods=["POST"])
        self.router.add_api_route("/api/helper/session", self.install, methods=["POST"])
        self.router.add_api_route("/api/helper/result", self.result, methods=["GET"])

    def helper(self) -> HelperConnections:
        helper = getattr(self.get_client(), "helper", None)
        if not isinstance(helper, HelperConnections):
            raise HTTPException(503, "session_unavailable")
        return helper

    @staticmethod
    def credential(request: Request) -> str:
        header = request.headers.get("authorization", "")
        if not header.startswith("Bearer "):
            raise SessionError("CONNECTION")
        token = header[7:]
        HelperConnections.digest(token)
        return token

    @staticmethod
    def failure(error: SessionError) -> HTTPException:
        status = {"CONNECTION": 401, "HELPER_DISABLED": 403, "BUSY": 429,
                  "STOPPED": 503, "BROWSER_START": 503, "HELPER_FAILED": 503}.get(error.code, 400)
        return HTTPException(status, "session_" + error.code.lower())

    async def connect(self, request: Request):
        self.auth.limit(request)
        try:
            helper = self.helper()
            if await request.json() != {}:
                raise ValueError
            return helper.connect()
        except SessionError as error:
            raise self.failure(error) from None
        except (ValueError, UnicodeError, RecursionError):
            raise HTTPException(400, "session_format") from None

    async def install(self, request: Request):
        try:
            token = self.credential(request)
            helper = self.helper()
            helper._check(token)
            return await helper.accept(token, json.loads(await request.body()))
        except SessionError as error:
            raise self.failure(error) from None
        except (ValueError, UnicodeError, RecursionError):
            raise HTTPException(400, "session_format") from None

    async def result(self, request: Request):
        try:
            token = self.credential(request)
            return self.helper().result(token)
        except SessionError as error:
            raise self.failure(error) from None
