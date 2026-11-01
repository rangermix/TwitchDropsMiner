"""Verified local login installation and autonomous server renewal."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
import time
from collections.abc import Awaitable, Callable, Iterator
from contextlib import AbstractAsyncContextManager, asynccontextmanager, contextmanager, nullcontext
from dataclasses import replace
from typing import Any

from src.auth.imported_session import ImportedSession
from src.auth.server_renewal import OwnedChromium, SDKIssuer
from src.auth.server_seed import ServerSeed
from src.auth.session_bundle import SessionError
from src.auth.session_diagnostics import SessionDiagnostic


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
        self._renewal_diagnostic: SessionDiagnostic | None = None
        self.session.on_renewal_needed = self.request_renewal

    def status(self) -> dict[str, Any]:
        return {"session": self.session.status(), "renewal_error": self._renewal_error,
                "renewal_diagnostic": self._renewal_diagnostic.to_dict() if self._renewal_diagnostic else None,
                "renewal_available": self.session.seed() is not None,
                "renewal_requires_login": self._renewal_error in self.RELOGIN_ERRORS}

    @contextmanager
    def _phase(self, stage: str) -> Iterator[None]:
        try:
            yield
        except SessionError as error:
            error.annotate(stage=stage)
            if (stage in ("issued_validation", "renewal_validation") and error.code == "CATALOG"
                    and error.diagnostic.reason == "integrity" and getattr(self.issuer, "headless_fallback", False)):
                # The rejected proof came from the headless fallback (no xvfb-run).
                error.diagnostic = replace(error.diagnostic, reason="headless_fallback")
            logging.getLogger(__name__).warning("SESSION_DIAGNOSTIC %s", json.dumps(error.diagnostic.to_dict(), sort_keys=True))
            raise
        except Exception:
            failure = SessionError("HELPER_FAILED", stage=stage, reason="unexpected_exception")
            logging.getLogger(__name__).warning("SESSION_DIAGNOSTIC %s", json.dumps(failure.diagnostic.to_dict(), sort_keys=True))
            raise failure from None

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
            with self._phase("seed_validation"):
                seed.bundle.require_fresh(self.clock())
                seed.cookie.require_fresh(self.clock())
            with self._phase("captured_validation"):
                identity = await self.session._transport.validate(seed.bundle, None)
            with self._phase("server_issuance"):
                renewed = await self.issuer.issue(seed, initial=True)
            current()
            with self._phase("issued_validation"):
                async with self.activation():
                    result = await self.session.install(
                        renewed.bundle.to_dict(), expected_user_id=identity.user_id,
                        sdk_cookie=renewed.cookie, replace=True, authorized=current,
                    )
            self._renewal_error = None
            self._renewal_diagnostic = None
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
            with self._phase("renewal_issuance"):
                if seed.cookie.expires_at <= self.clock():
                    await self.session._transport.validate_token(seed.bundle.token, account,
                                                                user_agent=seed.bundle.user_agent)
                    renewed = await self.issuer.bootstrap(seed.bundle.token)
                else:
                    renewed = await self.issuer.issue(seed)

            def current() -> bool:
                self._current(epoch)
                if self.session.status()["generation"] != generation:
                    raise SessionError("STALE")
                return True

            with self._phase("renewal_validation"):
                result = await self.session.install(renewed.bundle.to_dict(), sdk_cookie=renewed.cookie,
                                                    expected_user_id=account, authorized=current)
            self._renewal_error = None
            self._renewal_diagnostic = None
            self.on_change()
            return result

    async def accept_token(self, token: str, *, authorized: Callable[[], bool]) -> dict[str, Any]:
        """Bootstrap proof from an explicitly supplied WEB OAuth token, never trust OAuth alone."""
        epoch = self._epoch

        def current() -> bool:
            self._current(epoch)
            if not authorized():
                raise SessionError("STALE")
            return True

        async with self._operation(), self._lock:
            current()
            with self._phase("captured_validation"):
                account = await self.session._transport.validate_token(token)
            current()
            with self._phase("server_issuance"):
                renewed = await self.issuer.bootstrap(token)
            current()
            with self._phase("issued_validation"):
                async with self.activation():
                    result = await self.session.install(
                        renewed.bundle.to_dict(), expected_user_id=account,
                        sdk_cookie=renewed.cookie, replace=True, authorized=current,
                    )
            self._renewal_error = self._renewal_diagnostic = None
            self._wake.set()
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
        self._renewal_diagnostic = None
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
                self._renewal_error = error.diagnostic.code
                self._renewal_diagnostic = error.diagnostic
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
