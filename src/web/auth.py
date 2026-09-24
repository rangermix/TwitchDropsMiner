"""Optional password-only dashboard authentication, independent of Twitch OAuth."""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import os
import re
import secrets
import tempfile
import time
from collections import deque
from pathlib import Path
from typing import Literal

import socketio
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from pydantic import BaseModel, SecretStr
from starlette.requests import HTTPConnection
from starlette.types import ASGIApp, Receive, Scope, Send

from src.i18n import _
from src.version import __version__
from src.web.origin import DashboardOrigin


class PasswordRequest(BaseModel):
    password: SecretStr
    remember: bool = False


class AuthSettingsRequest(BaseModel):
    action: Literal["enable", "change", "disable"]
    current_password: SecretStr = SecretStr("")
    password: SecretStr = SecretStr("")
    confirm_password: SecretStr = SecretStr("")


class WebAuth:
    COOKIE = "tdm_session"
    SESSION_SECONDS = 30 * 24 * 60 * 60
    MAX_SESSIONS = 128

    def __init__(self, path: Path, public_base_url: str = ""):
        self.origin = DashboardOrigin(public_base_url)
        self.path = path
        self.password_hash = ""
        self.sessions: dict[str, float] = {}
        self.lock = asyncio.Lock()
        self.attempts: deque[tuple[float, str]] = deque()
        if path.exists():
            # A damaged file must stop startup, never silently disable protection.
            data = json.loads(path.read_text(encoding="utf-8"))
            self.password_hash = data["password_hash"]
            self.sessions = data["sessions"]
            if (
                data.get("version") != 1
                or not isinstance(self.password_hash, str)
                or (self.password_hash and not re.fullmatch(
                    r"scrypt\$[0-9a-f]{32}\$[0-9a-f]{64}", self.password_hash
                ))
                or not isinstance(self.sessions, dict)
                or len(self.sessions) > self.MAX_SESSIONS
                or any(not re.fullmatch(r"[0-9a-f]{64}", key)
                       or type(expiry) not in (int, float)
                       or not 0 < expiry < float("inf")
                       for key, expiry in self.sessions.items())
                or (not self.password_hash and self.sessions)
            ):
                raise ValueError("Invalid dashboard authentication file")

    @property
    def enabled(self) -> bool:
        return bool(self.password_hash)

    @staticmethod
    def digest(token: str) -> str:
        return hashlib.sha256(token.encode()).hexdigest()

    @staticmethod
    def hash_password(password: str, salt: str | None = None) -> str:
        salt = salt or secrets.token_hex(16)
        # OWASP's 32 MiB / parallelization 3 scrypt configuration.
        key = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt),
                             n=2**15, r=8, p=3, dklen=32, maxmem=64 * 1024 * 1024)
        return f"scrypt${salt}${key.hex()}"

    def authenticated(self, token: str) -> bool:
        return bool(token) and self.sessions.get(self.digest(token), 0) > time.time()

    def allowed(self, token: str) -> bool:
        return not self.enabled or self.authenticated(token)

    def token(self, scope: Scope) -> str:
        return HTTPConnection(scope).cookies.get(self.COOKIE, "")

    def save(self, password_hash: str, sessions: dict[str, float]) -> None:
        """Commit to disk atomically before changing the running policy."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, name = tempfile.mkstemp(prefix=".web-auth-", dir=self.path.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                json.dump({"version": 1, "password_hash": password_hash,
                           "sessions": sessions}, stream)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(name, self.path)
        finally:
            Path(name).unlink(missing_ok=True)
        self.password_hash, self.sessions = password_hash, sessions

    def limit(self, request: Request) -> None:
        now = time.monotonic()
        peer = request.client.host if request.client else "unknown"
        while self.attempts and self.attempts[0][0] <= now - 60:
            self.attempts.popleft()
        # A global ceiling also bounds memory and concurrent hashing under IP rotation.
        if len(self.attempts) >= 30 or sum(ip == peer for _, ip in self.attempts) >= 5:
            raise HTTPException(429, "rate_limited", headers={"Retry-After": "60"})
        self.attempts.append((now, peer))

    async def verify(self, password: str) -> None:
        if not 1 <= len(password) <= 1024:
            raise HTTPException(401, "invalid_password")
        candidate = await asyncio.to_thread(
            self.hash_password, password, self.password_hash.split("$")[1]
        )
        if not hmac.compare_digest(candidate, self.password_hash):
            raise HTTPException(401, "invalid_password")

    def new_session(self) -> tuple[str, dict[str, float]]:
        token = secrets.token_urlsafe(32)
        sessions = {key: expiry for key, expiry in self.sessions.items() if expiry > time.time()}
        while len(sessions) >= self.MAX_SESSIONS:
            del sessions[min(sessions, key=lambda key: sessions[key])]
        sessions[self.digest(token)] = time.time() + self.SESSION_SECONDS
        return token, sessions

    def response(self, request: Request, token: str = "", remember: bool = False):
        response = JSONResponse({"success": True, "enabled": self.enabled},
                                headers={"Cache-Control": "no-store"})
        if token:
            response.set_cookie(self.COOKIE, token, httponly=True, samesite="strict",
                                secure=self.origin.secure_cookie(request), path="/",
                                max_age=self.SESSION_SECONDS if remember else None)
        else:
            response.delete_cookie(self.COOKIE, httponly=True, samesite="strict",
                                   secure=self.origin.secure_cookie(request), path="/")
        return response


class AuthMiddleware:
    """Guard HTTP and Engine.IO before either application handles the request."""

    PUBLIC = {"/login", "/healthz", "/api/auth/status", "/api/auth/login",
              "/static/auth.js", "/static/auth.css", "/static/styles.css", "/static/favicon.png"}

    def __init__(self, app: ASGIApp, auth: WebAuth):
        self.app, self.auth = app, auth

    async def __call__(self, scope: Scope, receive: Receive, send: Send):
        if scope["type"] not in ("http", "websocket") or scope.get("tdm_auth_checked"):
            return await self.app(scope, receive, send)
        scope["tdm_auth_checked"] = True
        connection = HTTPConnection(scope)
        path = scope["path"]
        socket = path.startswith("/socket.io")
        mutation = scope.get("method", "GET") not in ("GET", "HEAD", "OPTIONS")
        origin = connection.headers.get("origin")
        unsafe_origin = origin is not None and origin != self.auth.origin.expected(connection)
        forbidden = (mutation or socket) and (
            unsafe_origin or connection.headers.get("sec-fetch-site") == "cross-site"
            or (mutation and not socket and connection.headers.get("x-tdm-request") != "1")
        )
        if forbidden:
            return await self.reject(scope, receive, send, 403, "forbidden")
        if path not in self.PUBLIC and not self.auth.allowed(self.auth.token(scope)):
            if path == "/" and scope["type"] == "http":
                return await RedirectResponse("/login", status_code=303,
                    headers={"Cache-Control": "no-store"})(scope, receive, send)
            return await self.reject(scope, receive, send, 401, "authentication_required")
        # Bound auth payloads before Pydantic parses them; do not echo submitted secrets.
        if mutation and path.startswith(("/api/auth/", "/api/session/")):
            limit = 65536 if path.startswith("/api/session/") else 16384
            body = b""
            while True:
                message = await receive()
                if message["type"] == "http.disconnect":
                    return
                body += message.get("body", b"")
                if len(body) > limit:
                    return await self.reject(scope, receive, send, 413, "invalid_request")
                if not message.get("more_body"):
                    break

            async def buffered_receive():
                return {"type": "http.request", "body": body, "more_body": False}

            receive = buffered_receive

        async def private_send(message):
            if message["type"] == "http.response.start" and not path.startswith("/static/"):
                headers = [(k, v) for k, v in message.get("headers", [])
                           if k.lower() != b"cache-control"]
                headers.extend([(b"cache-control", b"no-store"),
                                (b"x-frame-options", b"DENY"),
                                (b"referrer-policy", b"same-origin")])
                message["headers"] = headers
            await send(message)

        await self.app(scope, receive, private_send)

    @staticmethod
    async def reject(scope, receive, send, status, detail):
        if scope["type"] == "websocket":
            await send({"type": "websocket.close", "code": 1008})
        else:
            await JSONResponse({"detail": detail}, status_code=status,
                               headers={"Cache-Control": "no-store"})(scope, receive, send)


class AuthSocketServer(socketio.AsyncServer):
    """Recheck sessions for broadcasts and events, including already open sockets."""

    def __init__(self, auth: WebAuth):
        super().__init__(async_mode="asgi", cors_allowed_origins=[],
                         logger=False, engineio_logger=False)
        self.auth = auth
        self.tokens: dict[str, str] = {}
        self.expirations: dict[str, asyncio.TimerHandle] = {}

    def register(self, sid: str, scope: Scope) -> bool:
        token = self.auth.token(scope)
        if not self.auth.allowed(token):
            return False
        self.tokens[sid] = token
        if self.auth.enabled:
            delay = max(0, self.auth.sessions[self.auth.digest(token)] - time.time())
            self.expirations[sid] = asyncio.get_running_loop().call_later(
                delay, lambda: asyncio.create_task(self.disconnect(sid))
            )
        return True

    def forget(self, sid: str) -> None:
        self.tokens.pop(sid, None)
        timer = self.expirations.pop(sid, None)
        if timer:
            timer.cancel()

    async def authorize(self, sid: str) -> bool:
        if sid in self.tokens and self.auth.allowed(self.tokens[sid]):
            return True
        await self.disconnect(sid)
        return False

    async def prune(self) -> None:
        # Socket.IO creates a provisional room before running its connect handler.
        # Include those participants so a rejected handshake cannot receive a broadcast.
        participants = {sid for sid, _ in self.manager.get_participants("/", None)}
        for sid in participants | self.tokens.keys():
            await self.authorize(sid)

    async def emit(self, event, data=None, *args, **kwargs):
        await self.prune()
        return await super().emit(event, data, *args, **kwargs)


class AuthAPI:
    def __init__(self, auth: WebAuth, sio: AuthSocketServer):
        self.auth, self.sio = auth, sio
        self.router = APIRouter()
        self.router.add_api_route("/login", self.page, methods=["GET"])
        self.router.add_api_route("/healthz", self.health, methods=["GET"])
        self.router.add_api_route("/api/auth/status", self.status, methods=["GET"])
        self.router.add_api_route("/api/auth/login", self.login, methods=["POST"])
        self.router.add_api_route("/api/auth/logout", self.logout, methods=["POST"])
        self.router.add_api_route("/api/auth/settings", self.configure, methods=["POST"])

    async def health(self):
        return {"status": "ok"}

    async def page(self, request: Request):
        if self.auth.allowed(self.auth.token(request.scope)):
            return RedirectResponse("/", status_code=303)
        path = Path(__file__).resolve().parents[2] / "web" / "login.html"
        return HTMLResponse(path.read_text(encoding="utf-8").replace("__APP_VERSION__", __version__))

    async def status(self, request: Request):
        return {"enabled": self.auth.enabled,
                "authenticated": self.auth.allowed(self.auth.token(request.scope)),
                "translations": _.t["gui"]["auth"]}

    async def login(self, data: PasswordRequest, request: Request):
        self.auth.limit(request)
        async with self.auth.lock:
            if not self.auth.enabled:
                raise HTTPException(409, "auth_changed")
            await self.auth.verify(data.password.get_secret_value())
            token, sessions = self.auth.new_session()
            # A successful login rotates this browser's previous session.
            sessions.pop(self.auth.digest(self.auth.token(request.scope)), None)
            self.auth.save(self.auth.password_hash, sessions)
            await self.sio.prune()
            return self.auth.response(request, token, data.remember)

    async def logout(self, request: Request):
        async with self.auth.lock:
            sessions = self.auth.sessions.copy()
            sessions.pop(self.auth.digest(self.auth.token(request.scope)), None)
            self.auth.save(self.auth.password_hash, sessions)
            await self.sio.prune()
            return self.auth.response(request)

    async def configure(self, data: AuthSettingsRequest, request: Request):
        self.auth.limit(request)
        async with self.auth.lock:
            if (data.action == "enable") == self.auth.enabled:
                raise HTTPException(409, "auth_changed")
            # Recheck after acquiring the lock: another request may have revoked us.
            if not self.auth.allowed(self.auth.token(request.scope)):
                raise HTTPException(401, "authentication_required")
            if self.auth.enabled:
                await self.auth.verify(data.current_password.get_secret_value())
            if data.action == "disable":
                self.auth.save("", {})
                # Drop old connections even though the app is now public.
                for sid in list(self.sio.tokens):
                    await self.sio.disconnect(sid)
                return self.auth.response(request)
            password = data.password.get_secret_value()
            if not 8 <= len(password) <= 1024:
                raise HTTPException(400, "password_length")
            if password != data.confirm_password.get_secret_value():
                raise HTTPException(400, "password_mismatch")
            hashed = await asyncio.to_thread(self.auth.hash_password, password)
            token = secrets.token_urlsafe(32)
            self.auth.save(hashed, {self.auth.digest(token): time.time() + self.auth.SESSION_SECONDS})
            await self.sio.prune()
            return self.auth.response(request, token)
