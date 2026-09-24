"""Dashboard-facing manual import; raw session data is never returned."""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse

from src.auth.imported_session import ImportedSession
from src.auth.session_bundle import SessionBundle, SessionError


if TYPE_CHECKING:
    from src.core.client import Twitch
    from src.web.auth import WebAuth


class SessionAPI:
    def __init__(self, auth: WebAuth, get_client: Callable[[], Twitch | None]):
        self.auth, self.get_client = auth, get_client
        self.router = APIRouter()
        self.router.add_api_route("/api/session", self.status, methods=["GET"])
        self.router.add_api_route("/api/session/import", self.import_file, methods=["POST"])
        self.router.add_api_route("/api/session/pair", self.pair, methods=["POST"])
        self.router.add_api_route("/api/session/revoke", self.revoke, methods=["POST"])
        self.router.add_api_route("/api/session/renew", self.renew, methods=["POST"])

    def require_dashboard(self, request: Request) -> None:
        if not self.auth.enabled or not self.auth.authenticated(self.auth.token(request.scope)):
            raise HTTPException(401, "dashboard_auth_required")

    def session(self) -> ImportedSession:
        client = self.get_client()
        if client is None or not isinstance(client._browser, ImportedSession):
            raise HTTPException(503, "session_import_disabled")
        return client._browser

    async def status(self, request: Request):
        client = self.get_client()
        enabled = client is not None and isinstance(client._browser, ImportedSession)
        if not self.auth.enabled:
            return {"enabled": enabled, "authentication_required": True}
        self.require_dashboard(request)
        return {
            "enabled": enabled, "authentication_required": False,
            "session": self.session().status() if enabled else None,
        }

    async def import_file(self, request: Request):
        self.require_dashboard(request)
        session = self.session()
        try:
            bundle = SessionBundle.from_json(await request.body(), now=session._clock())
            status = await session.install(bundle.to_dict(), authorized=lambda: (
                self.auth.enabled and self.auth.authenticated(self.auth.token(request.scope))
            ))
        except SessionError as error:
            raise HTTPException(400, f"session_{error.code.lower()}") from None
        return {"success": True, "session": status}

    async def pair(self, request: Request):
        self.require_dashboard(request)
        session = self.session()
        try:
            credential = await session.pair(authorized=lambda: (
                self.auth.enabled and self.auth.authenticated(self.auth.token(request.scope))
            ))
        except SessionError as error:
            raise HTTPException(400, f"session_{error.code.lower()}") from None
        return JSONResponse({
            "version": 1, "endpoint": self.auth.origin.expected(request) + "/api/session/renew",
            "credential": credential, "user_id": session.status()["user_id"],
        }, headers={"Content-Disposition": 'attachment; filename="tdm-connection.json"'})

    async def revoke(self, request: Request):
        self.require_dashboard(request)
        try:
            await self.session().revoke(authorized=lambda: (
                self.auth.enabled and self.auth.authenticated(self.auth.token(request.scope))
            ))
        except SessionError as error:
            raise HTTPException(400, f"session_{error.code.lower()}") from None
        return {"success": True, "session": self.session().status()}

    async def renew(self, request: Request):
        # This is the only cookie-free endpoint. Its credential cannot read the dashboard.
        if not self.auth.enabled:
            raise HTTPException(401, "session_pairing")
        header = request.headers.get("authorization", "")
        if not header.startswith("Bearer "):
            raise HTTPException(401, "session_pairing")
        session = self.session()
        try:
            session._check_renewal(header[7:])
            bundle = SessionBundle.from_json(await request.body(), now=session._clock())
            status = await session.install(
                bundle.to_dict(), renewal_token=header[7:], authorized=lambda: self.auth.enabled,
            )
        except SessionError as error:
            raise HTTPException(401 if error.code in {"PAIRING", "AUTH"} else 400,
                                f"session_{error.code.lower()}") from None
        return {"success": True, "session": status}
