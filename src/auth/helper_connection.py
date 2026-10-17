"""Temporary desktop admission; SessionController remains the only session writer."""

from __future__ import annotations

import asyncio
import hashlib
import re
import secrets
import time
from collections.abc import Callable
from typing import Any

from src.auth.server_seed import ServerSeed
from src.auth.session_bundle import SessionError
from src.auth.session_controller import SessionController


class HelperConnections:
    CONNECTION_SECONDS = 600

    def __init__(self, controller: SessionController, *, clock: Callable[[], float] = time.time,
                 on_change: Callable[[], None] = lambda: None):
        self.controller, self.clock, self.on_change = controller, clock, on_change
        self.state = "disabled"
        self.error: str | None = None
        self.attempt = 0
        self.expires_at: float | None = None
        self._ticket = ""
        self._generation = 0
        self._authorized: Callable[[], bool] = lambda: False
        self._task: asyncio.Task | None = None
        self._receipt: dict[str, Any] | None = None
        self._stopped = False

    @staticmethod
    def digest(token: str) -> str:
        if not isinstance(token, str) or not re.fullmatch(r"[A-Za-z0-9_-]{43}", token):
            raise SessionError("CONNECTION")
        return hashlib.sha256(token.encode()).hexdigest()

    @property
    def selected(self) -> bool:
        return self.state not in ("disabled", "complete")

    def _live(self) -> bool:
        return (not self._stopped and self.expires_at is not None
                and self.clock() < self.expires_at and self._authorized())

    def _invalidate(self, *, error: str | None = None) -> None:
        self.attempt += 1
        self._ticket = ""
        self._receipt = None
        self.expires_at = None
        self.state, self.error = ("error" if error else "disabled"), error
        if self._task is not None and self._task is not asyncio.current_task():
            self._task.cancel()

    def status(self) -> dict[str, Any]:
        if self.state in ("waiting", "connected", "verifying", "complete") and not self._live():
            # Receipt expiry does not expire the independently persisted Twitch session.
            self._invalidate(error=None if self.state == "complete" else "HELPER_EXPIRED")
        return {"state": self.state, "expires_at": self.expires_at, "error": self.error,
                "attempt": self.attempt}

    def enable(self, authorized: Callable[[], bool]) -> None:
        if self._stopped or not authorized():
            raise SessionError("HELPER_DISABLED")
        if self._task is not None or self.controller.session.status()["state"] == "ready":
            raise SessionError("HELPER_STATE")
        self._invalidate()
        self._authorized = authorized
        self._generation = self.controller.session.status()["generation"]
        self.expires_at = self.clock() + self.CONNECTION_SECONDS
        self.state, self.error = "waiting", None
        self.on_change()

    def _check(self, token: str, *, attempt: int | None = None) -> None:
        expected = self._ticket
        if (not self._live() or not expected or self.digest(token) != expected
                or (attempt is not None and attempt != self.attempt)):
            raise SessionError("CONNECTION")

    def connect(self) -> dict[str, Any]:
        if not self._live():
            raise SessionError("HELPER_DISABLED")
        if self.state != "waiting":
            raise SessionError("BUSY")
        token = secrets.token_urlsafe(32)
        self._ticket = self.digest(token)
        self.state = "connected"
        self.on_change()
        return {"version": 1, "connection": token, "expires_at": self.expires_at}

    def result(self, token: str) -> dict[str, Any]:
        self._check(token)
        if self._receipt is not None:
            return self._receipt
        if self.state == "error":
            raise SessionError(self.error or "HELPER_FAILED")
        return {"state": "pending"}

    async def accept(self, token: str, data: Any) -> dict[str, Any]:
        self._check(token)
        if self.state != "connected" or self._task is not None:
            raise SessionError("BUSY")
        seed = ServerSeed.from_dict(data, now=self.clock())
        self.state = "verifying"
        self._task = asyncio.current_task()
        attempt = self.attempt

        def authorized() -> bool:
            self._check(token, attempt=attempt)
            if self.controller.session.status()["generation"] != self._generation:
                raise SessionError("STALE")
            return True

        try:
            self.on_change()
            result = await self.controller.accept(seed, authorized=authorized)
            # No await separates successful installation and the lost-ack receipt.
            self._receipt = {"success": True, "allow_helper_connection": False, "session": result}
            self.state, self.error = "complete", None
            self.on_change()
            return self._receipt
        except SessionError as error:
            if self.attempt == attempt:
                self.state, self.error = "error", error.code
                self.on_change()
            raise
        except asyncio.CancelledError:
            if self.attempt == attempt:
                self._invalidate(error="HELPER_CANCELLED")
                self.on_change()
            raise
        except Exception:
            if self.attempt == attempt:
                self.state, self.error = "error", "HELPER_FAILED"
                self.on_change()
            raise SessionError("HELPER_FAILED") from None
        finally:
            self._task = None

    def session_changed(self) -> None:
        if (self.state in ("waiting", "connected", "verifying")
                and self.controller.session.status()["generation"] != self._generation
                and not (self.state == "verifying" and self._task is asyncio.current_task())):
            # A completed receipt stays valid across same-account renewals.
            self._invalidate()

    async def cancel(self) -> None:
        task = self._task
        self._invalidate()
        if task is not None and task is not asyncio.current_task():
            await asyncio.gather(task, return_exceptions=True)
        self.on_change()

    async def stop(self) -> None:
        self._stopped = True
        await self.cancel()
