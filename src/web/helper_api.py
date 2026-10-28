"""Dashboard-enabled admission and bearer-authenticated desktop session transfer."""

from __future__ import annotations

import json
from collections import deque
from collections.abc import Callable
from typing import TYPE_CHECKING

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from src.auth.helper_connection import HelperConnections
from src.auth.session_bundle import SessionError
from src.auth.session_diagnostics import SessionDiagnostic


if TYPE_CHECKING:
    from src.core.client import Twitch
    from src.web.auth import WebAuth


class HelperAPI:
    def __init__(self, auth: WebAuth, get_client: Callable[[], Twitch | None]):
        self.auth, self.get_client = auth, get_client
        self.attempts: deque[tuple[float, str]] = deque()
        self.router = APIRouter()
        self.router.add_api_route("/api/helper/connect", self.connect, methods=["POST"])
        self.router.add_api_route("/api/helper/session", self.install, methods=["POST"])
        self.router.add_api_route("/api/helper/result", self.result, methods=["GET"])

    def helper(self) -> HelperConnections:
        helper = getattr(self.get_client(), "helper", None)
        if not isinstance(helper, HelperConnections):
            raise SessionError("UNAVAILABLE", stage="helper_admission")
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
    def failure(error: SessionError) -> JSONResponse:
        status = SessionDiagnostic.HTTP_STATUSES.get(error.code, 400)
        error.annotate(stage="helper_admission", http_status=status)
        return JSONResponse(status_code=status, content={
            "detail": "session_" + error.diagnostic.code.lower(),
            "diagnostic": error.diagnostic.to_dict(),
        })

    async def connect(self, request: Request):
        self.auth.limit(request, self.attempts)
        try:
            helper = self.helper()
            if await request.json() != {}:
                raise ValueError
            return helper.connect()
        except SessionError as error:
            return self.failure(error)
        except (ValueError, UnicodeError, RecursionError):
            return self.failure(SessionError("FORMAT", stage="helper_admission", reason="invalid_json"))

    async def install(self, request: Request):
        try:
            token = self.credential(request)
            helper = self.helper()
            helper._check(token)
            return await helper.accept(token, json.loads(await request.body()))
        except SessionError as error:
            return self.failure(error)
        except (ValueError, UnicodeError, RecursionError):
            return self.failure(SessionError("FORMAT", stage="seed_validation", reason="invalid_json"))

    async def result(self, request: Request):
        try:
            token = self.credential(request)
            return self.helper().result(token)
        except SessionError as error:
            return self.failure(error)
