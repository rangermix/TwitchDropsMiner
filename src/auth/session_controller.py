"""Verified local login installation and autonomous server renewal."""

from __future__ import annotations

import asyncio
import os
import shutil
import time
from collections.abc import Awaitable, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager, nullcontext
from typing import Any

from src.auth.imported_session import ImportedSession
from src.auth.server_renewal import OwnedChromium, SDKIssuer
from src.auth.server_seed import ServerSeed
from src.auth.session_bundle import SessionError


class SessionController:
    """Serialize local installation, renewal, and logout without exported tickets."""

    RENEW_BEFORE = 300
    RELOGIN_ERRORS = frozenset({"SDK_EXPIRED", "ACCOUNT_MISMATCH", "IDENTITY", "AUTH"})

    def __init__(self, session: ImportedSession, *, issuer: SDKIssuer | None = None,
                 clock: Callable[[], float] = time.time,
                 activation: Callable[[], AbstractAsyncContextManager] = nullcontext,
                 on_change: Callable[[], None] = lambda: None):
        self.session, self.clock = session, clock
        self.issuer = issuer or SDKIssuer(OwnedChromium(
            shutil.which("chromium") or shutil.which("chromium-browser") or "chromium",
            no_sandbox=os.name == "posix" and os.geteuid() == 0,
        ), diagnostics=getattr(session._transport, "diagnostics", None))
        self.activation, self.on_change = activation, on_change
        self._lock = asyncio.Lock()
        self._wake = asyncio.Event()
        self._task: asyncio.Task | None = None
        self._operations: set[asyncio.Task] = set()
        self._stopping = False
        self._epoch = 0
        self._force_renewal = False
        self._renewal_error: str | None = None
        self.session.on_renewal_needed = self.request_renewal

    def status(self) -> dict[str, Any]:
        return {"session": self.session.status(), "renewal_error": self._renewal_error,
                "renewal_available": self.session.seed() is not None,
                "renewal_requires_login": self._renewal_error in self.RELOGIN_ERRORS}

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

    def _current(self, epoch: int) -> bool:
        if self._stopping or epoch != self._epoch:
            raise SessionError("STALE")
        return True

    async def accept(self, seed: ServerSeed, *, authorized: Callable[[], bool]) -> dict[str, Any]:
        epoch = self._epoch

        def current() -> bool:
            self._current(epoch)
            if not authorized():
                raise SessionError("STALE")
            return True

        async with self._operation(), self._lock:
            current()
            seed.bundle.require_fresh(self.clock())
            seed.cookie.require_fresh(self.clock())
            identity = await self.session._transport.validate(seed.bundle, None)
            renewed = await self.issuer.issue(seed, initial=True)
            current()
            async with self.activation():
                result = await self.session.install(
                    renewed.bundle.to_dict(), expected_user_id=identity.user_id,
                    sdk_cookie=renewed.cookie, replace=True, authorized=current,
                )
            self._renewal_error = None
            self._wake.set()
            self.on_change()
            return result

    async def renew_once(self) -> dict[str, Any]:
        epoch = self._epoch
        async with self._operation(), self._lock:
            self._current(epoch)
            seed = self.session.seed()
            if seed is None:
                raise SessionError("SDK_SEED")
            generation = self.session.status()["generation"]
            account = self.session.status()["user_id"]
            renewed = await self.issuer.issue(seed)

            def current() -> bool:
                self._current(epoch)
                if self.session.status()["generation"] != generation:
                    raise SessionError("STALE")
                return True

            result = await self.session.install(renewed.bundle.to_dict(), sdk_cookie=renewed.cookie,
                                                expected_user_id=account, authorized=current)
            self._renewal_error = None
            self.on_change()
            return result

    async def logout(self, clear_cookies: Callable[[], Awaitable[None]]) -> None:
        # Invalidate before cancellation: even cancellation-resistant issuance cannot commit.
        self._epoch += 1
        tasks = tuple(task for task in self._operations if task is not asyncio.current_task())
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        async with self._lock, self.activation():
            await self.session.logout()
            await clear_cookies()
        self._renewal_error = None
        self._force_renewal = False
        self._wake.set()
        self.on_change()

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
                # Track the operation separately so logout does not cancel the worker itself.
                await asyncio.create_task(self.renew_once())
                retry = 5.0
            except asyncio.CancelledError:
                task = asyncio.current_task()
                if self._stopping or (task is not None and task.cancelling()):  # type: ignore[attr-defined]  # Python 3.12 runtime.
                    raise
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
        self._epoch += 1
        self._wake.set()
        tasks = self._operations | ({self._task} if self._task is not None else set())
        tasks.discard(asyncio.current_task())
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        self._task = None
