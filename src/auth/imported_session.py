"""Validated browser context imported into a browser-free TDM process."""

from __future__ import annotations

import asyncio
import hashlib
import re
import secrets
import time
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any

import aiohttp

from src.auth.browser_session import BrowserIdentity, BrowserSession
from src.auth.session_bundle import PrivateSessionFile, SessionBundle, SessionError
from src.config import GQL_OPERATIONS, ClientType
from src.exceptions import ExitRequest, LoginException


if TYPE_CHECKING:
    from src.web.managers.login import LoginFormManager


class SessionTransport:
    """Send credentials only to fixed Twitch endpoints, without a cookie jar."""

    def __init__(self, proxy: str | None | Callable[[], str | None] = None):
        self.proxy = proxy
        self._http: aiohttp.ClientSession | None = None

    async def request(self, method: str, url: str, *, headers: dict[str, str], body: Any = None) -> Any:
        if (method, url) not in {
            ("GET", BrowserSession.VALIDATE_URL), ("POST", BrowserSession.GQL_URL),
        }:
            raise SessionError("REQUEST")
        if self._http is None or self._http.closed:
            self._http = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=30), cookie_jar=aiohttp.DummyCookieJar(),
            )
        try:
            async with self._http.request(
                method, url, headers=headers, json=body, proxy=self.proxy() if callable(self.proxy) else self.proxy, allow_redirects=False,
            ) as response:
                if response.status != 200:
                    raise SessionError("AUTH" if response.status in (401, 403) else "REQUEST")
                return await response.json()
        except (aiohttp.ClientError, TimeoutError, ValueError):
            raise SessionError("REQUEST") from None

    async def validate(self, bundle: SessionBundle, expected_user_id: int | None) -> BrowserIdentity:
        identity = await self.request("GET", BrowserSession.VALIDATE_URL, headers={
            "Authorization": bundle.headers["authorization"], "User-Agent": bundle.user_agent,
        })
        try:
            if not isinstance(identity, dict) or identity.get("client_id") != ClientType.WEB.CLIENT_ID:
                raise ValueError
            raw_id = identity["user_id"]
            if not isinstance(raw_id, str) or not raw_id.isdecimal():
                raise ValueError
            user_id = int(raw_id)
            if user_id <= 0:
                raise ValueError
        except (KeyError, TypeError, ValueError):
            raise SessionError("IDENTITY") from None
        if expected_user_id is not None and user_id != expected_user_id:
            raise SessionError("ACCOUNT_MISMATCH")
        results = await self.request(
            "POST", BrowserSession.GQL_URL, headers=bundle.request_headers(),
            body=[GQL_OPERATIONS["Inventory"], GQL_OPERATIONS["Campaigns"]],
        )
        try:
            BrowserSession.campaign_count(results)
        except LoginException:
            raise SessionError("CATALOG") from None
        return BrowserIdentity(
            user_id, bundle.token, bundle.headers.get("x-device-id") or bundle.headers["device-id"],
            bundle.user_agent,
        )

    async def close(self) -> None:
        if self._http is not None:
            await self._http.close()
            self._http = None


class ImportedSession:
    """Replace complete validated contexts atomically and wait at expiry."""

    def __init__(
        self, path: Path, *, transport: SessionTransport | None = None,
        clock: Callable[[], float] = time.time,
        bound_user_id: Callable[[], int | None] = lambda: None,
        on_identity: Callable[[BrowserIdentity], None] = lambda identity: None,
    ):
        self.path, self._clock = path, clock
        self._file = PrivateSessionFile(path)
        self._transport = transport or SessionTransport()
        self.bound_user_id = bound_user_id
        self._on_identity = on_identity
        self._bundle: SessionBundle | None = None
        self._identity: BrowserIdentity | None = None
        self._user_id: int | None = None
        self._expected_user_id: int | None = None
        self._generation = 0
        self._revision = 0
        self._renewal_digest = ""
        self._lock = asyncio.Lock()
        self._updated = asyncio.Event()
        self._stopping = False
        self._login: LoginFormManager | None = None
        self._restore_pending = False
        self._rejected = False
        if path.exists():
            state = self._file.read()
            try:
                if (
                    not isinstance(state, dict) or state.get("version") != 1
                    or type(state["user_id"]) is not int or state["user_id"] <= 0
                    or type(state["generation"]) is not int or state["generation"] < 1
                    or not isinstance(state.get("renewal_digest", ""), str)
                    or not re.fullmatch(r"(?:[0-9a-f]{64})?", state.get("renewal_digest", ""))
                ):
                    raise ValueError
                self._bundle = SessionBundle.from_dict(state["bundle"], now=clock())
                self._user_id, self._generation = state["user_id"], state["generation"]
                self._renewal_digest = state.get("renewal_digest", "")
                self._restore_pending = True
            except (KeyError, TypeError, ValueError, SessionError):
                raise SessionError("FILE") from None

    def bind_account(self, user_id: int | None) -> None:
        if user_id is not None:
            if self._user_id not in (None, user_id) or self._expected_user_id not in (None, user_id):
                raise SessionError("ACCOUNT_MISMATCH")
            self._expected_user_id = user_id

    def _bound_account(self) -> int | None:
        account = self.bound_user_id()
        if account is not None and self._expected_user_id not in (None, account):
            raise SessionError("ACCOUNT_MISMATCH")
        return account or self._expected_user_id

    def status(self) -> dict[str, Any]:
        return {
            "state": (
                "waiting" if self._bundle is None else
                "expired" if self._bundle.expires_at <= self._clock() else
                "waiting" if self._identity is None or self._rejected else "ready"
            ),
            "user_id": self._user_id, "generation": self._generation,
            "expires_at": self._bundle.expires_at if self._bundle else None,
            "paired": bool(self._renewal_digest),
        }

    def _save(self, bundle: SessionBundle, user_id: int, generation: int, digest: str) -> None:
        self._file.write({
            "version": 1, "bundle": bundle.to_dict(), "user_id": user_id,
            "generation": generation, "renewal_digest": digest,
        })

    def _check_renewal(self, token: str) -> None:
        if (not re.fullmatch(r"[A-Za-z0-9_-]{43}", token) or not self._renewal_digest
                or not secrets.compare_digest(hashlib.sha256(token.encode()).hexdigest(), self._renewal_digest)):
            raise SessionError("PAIRING")

    async def pair(self, *, authorized: Callable[[], bool] = lambda: True) -> str:
        async with self._lock:
            if not authorized():
                raise SessionError("AUTH")
            if self._stopping or self.status()["state"] != "ready":
                raise SessionError("AUTH")
            assert self._bundle is not None and self._user_id is not None
            token = secrets.token_urlsafe(32)
            digest = hashlib.sha256(token.encode()).hexdigest()
            self._save(self._bundle, self._user_id, self._generation, digest)
            self._renewal_digest = digest
            self._revision += 1
            return token

    async def revoke(self, *, authorized: Callable[[], bool] = lambda: True) -> None:
        async with self._lock:
            if not authorized():
                raise SessionError("AUTH")
            if self._bundle is not None and self._user_id is not None:
                self._save(self._bundle, self._user_id, self._generation, "")
            self._renewal_digest = ""
            self._revision += 1

    def _check_candidate(self, bundle: SessionBundle) -> None:
        bundle.require_fresh(self._clock())
        previous = self._bundle
        if previous is not None and (
            bundle.captured_at <= previous.captured_at
            or bundle.expires_at <= previous.expires_at
            or bundle.headers["client-integrity"] == previous.headers["client-integrity"]
        ):
            raise SessionError("REPLAY")

    async def install(
        self, data: Any, *, expected_user_id: int | None = None,
        renewal_token: str | None = None, authorized: Callable[[], bool] = lambda: True,
    ) -> dict[str, Any]:
        bundle = SessionBundle.from_dict(data, now=self._clock())
        async with self._lock:
            if self._stopping:
                raise SessionError("STOPPED")
            if not authorized():
                raise SessionError("AUTH")
            if renewal_token is not None:
                self._check_renewal(renewal_token)
            existing_account = self._bound_account()
            if existing_account is not None:
                if expected_user_id not in (None, existing_account):
                    raise SessionError("ACCOUNT_MISMATCH")
                expected_user_id = existing_account
            if self._user_id is not None and expected_user_id not in (None, self._user_id):
                raise SessionError("ACCOUNT_MISMATCH")
            self._check_candidate(bundle)
            account, revision = self._user_id or expected_user_id, self._revision
        # Do not hold the state lock while contacting Twitch: revoke/rotate must win.
        identity = await self._transport.validate(bundle, account)
        async with self._lock:
            if self._stopping:
                raise SessionError("STOPPED")
            if not authorized():
                raise SessionError("AUTH")
            if renewal_token is not None:
                self._check_renewal(renewal_token)
            if revision != self._revision:
                raise SessionError("STALE")
            self._check_candidate(bundle)
            if self._bound_account() not in (None, identity.user_id):
                raise SessionError("ACCOUNT_MISMATCH")
            generation = self._generation + 1
            self._save(bundle, identity.user_id, generation, self._renewal_digest)
            self._bundle, self._identity = bundle, identity
            self._user_id, self._generation = identity.user_id, generation
            self._revision += 1
            self._restore_pending = self._rejected = False
            self._on_identity(identity)
            self._updated.set()
            return self.status()

    async def authenticate(self, login: LoginFormManager) -> BrowserIdentity:
        self._login = login
        pending = False
        try:
            while not self._stopping:
                async with self._lock:
                    if self._bundle is not None and self._bundle.expires_at > self._clock():
                        if self._restore_pending:
                            self._restore_pending = False
                            try:
                                self._identity = await self._transport.validate(self._bundle, self._user_id)
                            except SessionError:
                                self._rejected = True
                        if self._stopping:
                            raise ExitRequest()
                        if (self._identity is not None and not self._rejected
                                and self._bundle.expires_at > self._clock()):
                            if self._bound_account() not in (None, self._identity.user_id):
                                raise SessionError("ACCOUNT_MISMATCH")
                            return self._identity
                    if self._stopping:
                        raise ExitRequest()
                    self._updated.clear()
                if not pending:
                    await login.import_pending(True)
                    pending = True
                await self._updated.wait()
            raise ExitRequest()
        finally:
            if pending:
                await login.import_pending(False)

    @staticmethod
    def _auth_rejection(row: Any) -> bool:
        # Retry only a definitive rejection before execution, never a partial result.
        return (
            isinstance(row, dict) and "data" not in row
            and isinstance(row.get("errors"), list) and bool(row["errors"])
            and all(isinstance(error, dict) and error.get("message") in {
                "failed integrity check", "invalid oauth token",
            } and "path" not in error for error in row["errors"])
        )

    async def gql(self, operations: Any) -> Any:
        if self._login is None:
            raise SessionError("AUTH")
        batch = isinstance(operations, list)
        pending = list(range(len(operations))) if batch else [0]
        results: list[Any] = [None] * len(pending)
        while pending:
            await self.authenticate(self._login)
            async with self._lock:
                if self._stopping:
                    raise ExitRequest()
                assert self._bundle is not None
                if self._bundle.expires_at <= self._clock() or self._rejected:
                    continue
                body = [operations[index] for index in pending] if batch else operations
                try:
                    response = await self._transport.request(
                        "POST", BrowserSession.GQL_URL, headers=self._bundle.request_headers(), body=body,
                    )
                except SessionError as error:
                    if error.code != "AUTH":
                        raise
                    self._rejected = True
                    continue
                rows = response if batch else [response]
                if (not isinstance(rows, list) or len(rows) != len(pending)
                        or any(not isinstance(row, dict) for row in rows)):
                    raise SessionError("RESPONSE")
                rejected = []
                for index, row in zip(pending, rows, strict=True):
                    if self._auth_rejection(row):
                        rejected.append(index)
                    else:
                        results[index] = row
                if rejected:
                    self._rejected = True
                pending = rejected
        return results if batch else results[0]

    def request_stop(self) -> None:
        self._stopping = True
        self._updated.set()

    async def close(self) -> None:
        self.request_stop()
        await self._transport.close()
