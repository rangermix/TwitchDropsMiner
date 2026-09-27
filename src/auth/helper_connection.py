"""One-time helper admission and server-owned integrity renewal."""

from __future__ import annotations

import asyncio
import hashlib
import os
import re
import secrets
import shutil
import time
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager, nullcontext
from typing import Any

from src.auth.imported_session import ImportedSession
from src.auth.server_renewal import OwnedChromium, SDKIssuer
from src.auth.server_seed import ServerSeed
from src.auth.session_bundle import SessionError


class HelperConnections:
    """Own admission tickets; the provider owns the atomic durable commit."""

    CONNECTION_SECONDS = 600
    MAX_CONNECTIONS = 8
    RENEW_BEFORE = 300
    RELOGIN_ERRORS = frozenset({"SDK_EXPIRED", "ACCOUNT_MISMATCH", "IDENTITY", "AUTH"})

    def __init__(
        self, session: ImportedSession, settings: Any, *, issuer: SDKIssuer | None = None,
        clock: Callable[[], float] = time.time,
        activation: Callable[[], AbstractAsyncContextManager] = nullcontext,
        on_change: Callable[[], None] = lambda: None,
    ):
        self.session, self.settings, self.clock = session, settings, clock
        self.issuer = issuer or SDKIssuer(OwnedChromium(
            shutil.which("chromium") or shutil.which("chromium-browser") or "chromium",
            no_sandbox=os.name == "posix" and os.geteuid() == 0,
        ))
        self.activation, self.on_change = activation, on_change
        self._connections: dict[str, tuple[float, int]] = {}
        self._lock = asyncio.Lock()
        self._wake = asyncio.Event()
        self._task: asyncio.Task | None = None
        self._operations: set[asyncio.Task] = set()
        self._stopping = False
        self._force_renewal = False
        self._renewal_error: str | None = None
        self.settings.allow_helper_connection = self.allowed
        self.session.on_renewal_needed = self.request_renewal

    @property
    def allowed(self) -> bool:
        return self.session.helper_allowed

    def status(self) -> dict[str, Any]:
        return {"enabled": True, "allow_helper_connection": self.allowed,
                "session": self.session.status(), "renewal_error": self._renewal_error,
                "renewal_available": self.session.seed() is not None,
                "renewal_requires_login": self._renewal_error in self.RELOGIN_ERRORS}

    def set_allowed(self, value: bool) -> None:
        epoch = self.session.helper_epoch
        self.session.set_helper_allowed(value)
        self.settings.allow_helper_connection = self.allowed
        if epoch != self.session.helper_epoch:
            self._connections.clear()
        self.on_change()

    @staticmethod
    def digest(token: str) -> str:
        if not isinstance(token, str) or not re.fullmatch(r"[A-Za-z0-9_-]{43}", token):
            raise SessionError("CONNECTION")
        return hashlib.sha256(token.encode()).hexdigest()

    def connect(self) -> dict[str, Any]:
        if self._stopping:
            raise SessionError("STOPPED")
        if not self.allowed:
            raise SessionError("HELPER_DISABLED")
        now = self.clock()
        self._connections = {key: value for key, value in self._connections.items() if value[0] > now}
        if len(self._connections) >= self.MAX_CONNECTIONS:
            raise SessionError("BUSY")
        token = secrets.token_urlsafe(32)
        expiry = now + self.CONNECTION_SECONDS
        self._connections[self.digest(token)] = (expiry, self.session.helper_epoch)
        return {"version": 1, "connection": token, "expires_at": expiry}

    def _check(self, token: str) -> bool:
        if self._stopping:
            raise SessionError("STOPPED")
        if not self.allowed:
            raise SessionError("HELPER_DISABLED")
        ticket = self._connections.get(self.digest(token))
        if ticket is None or ticket[0] <= self.clock() or ticket[1] != self.session.helper_epoch:
            raise SessionError("CONNECTION")
        return True

    def result(self, token: str) -> dict[str, Any]:
        receipt = self.session.helper_result(self.digest(token))
        if receipt is not None:
            return receipt
        if not self.allowed:
            raise SessionError("CONNECTION")
        self._check(token)
        return {"state": "pending"}

    @asynccontextmanager
    async def _operation(self):
        if self._stopping:
            raise SessionError("STOPPED")
        task = asyncio.current_task()
        assert task is not None
        self._operations.add(task)
        try:
            yield
        finally:
            self._operations.discard(task)

    async def accept(self, token: str, data: Any) -> dict[str, Any]:
        async with self._operation():
            return await self._accept(token, data)

    async def _accept(self, token: str, data: Any) -> dict[str, Any]:
        self._check(token)
        seed = ServerSeed.from_dict(data, now=self.clock())
        seed.bundle.require_fresh(self.clock())
        seed.cookie.require_fresh(self.clock())
        async with self._lock:
            self._check(token)
            identity = await self.session._transport.validate(seed.bundle, None)
            renewed = await self.issuer.issue(seed, initial=True)
            self._check(token)
            # Freeze authenticated miner work only after proving server issuance.
            async with self.activation():
                await self.session.install(
                    renewed.bundle.to_dict(), expected_user_id=identity.user_id,
                    sdk_cookie=renewed.cookie, replace=True,
                    authorized=lambda: self._check(token),
                    helper_receipt=(self.digest(token), self.clock() + self.CONNECTION_SECONDS),
                )
            self._connections.clear()
            self.settings.allow_helper_connection = self.allowed
            self._renewal_error = None
            self._wake.set()
            self.on_change()
            result = self.session.helper_result(self.digest(token))
            assert result is not None
            return result

    async def renew_once(self) -> dict[str, Any]:
        async with self._operation():
            return await self._renew_once()

    async def _renew_once(self) -> dict[str, Any]:
        async with self._lock:
            if self._stopping:
                raise SessionError("STOPPED")
            seed = self.session.seed()
            if seed is None:
                raise SessionError("SDK_SEED")
            generation = self.session.status()["generation"]
            account = self.session.status()["user_id"]
            renewed = await self.issuer.issue(seed)

            def current() -> bool:
                if self._stopping or self.session.status()["generation"] != generation:
                    raise SessionError("STALE")
                return True

            result = await self.session.install(
                renewed.bundle.to_dict(), sdk_cookie=renewed.cookie,
                expected_user_id=account, authorized=current,
            )
            self._renewal_error = None
            self.on_change()
            return result

    def request_renewal(self) -> None:
        if self.session.seed() is not None and not self._force_renewal:
            self._force_renewal = True
            self._wake.set()

    def start(self) -> None:
        if self._task is None and not self._stopping:
            self._task = asyncio.create_task(self._run())

    async def _wait(self, delay: float | None = None) -> bool:
        try:
            if delay is None:
                await self._wake.wait()
            else:
                await asyncio.wait_for(self._wake.wait(), timeout=max(0, delay))
            return True
        except TimeoutError:
            return False
        finally:
            self._wake.clear()

    async def _run(self) -> None:
        retry = 5.0
        while not self._stopping:
            seed = self.session.seed()
            if seed is None:
                await self._wait()
                continue
            delay = 0 if self._force_renewal else max(0, seed.bundle.expires_at - self.clock() - self.RENEW_BEFORE)
            if delay and await self._wait(delay):
                continue
            self._force_renewal = False
            try:
                await self.renew_once()
                retry = 5.0
            except SessionError as error:
                self._renewal_error = error.code
                self.on_change()
                if error.code in self.RELOGIN_ERRORS:
                    await self._wait()
                else:
                    await self._wait(retry)
                    self._force_renewal = True
                    retry = min(300, retry * 2)

    async def stop(self) -> None:
        self._stopping = True
        self._connections.clear()
        self._wake.set()
        tasks = self._operations | ({self._task} if self._task is not None else set())
        tasks.discard(asyncio.current_task())
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        self._task = None
