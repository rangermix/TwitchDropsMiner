"""Strict, private representation of an exported Twitch request context."""

from __future__ import annotations

import json
import math
import os
import re
import tempfile
import time
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType
from typing import Any

from src.config import ClientType
from src.exceptions import LoginException
from src.i18n import _


class SessionError(LoginException):
    """Only a fixed diagnostic code may cross a credential-handling boundary."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(_.t["login"]["error_code"].format(error_code=f"SESSION_{code}"))


class PrivateSessionFile:
    """Atomic owner-only JSON storage; callers never log the stored object."""

    def __init__(self, path: Path):
        self.path = path

    def read(self) -> Any:
        try:
            with self.path.open("rb") as stream:
                raw = stream.read(SessionBundle.MAX_BYTES + 4097)
            if len(raw) > SessionBundle.MAX_BYTES + 4096:
                raise ValueError
            return json.loads(raw)
        except (OSError, ValueError, RecursionError):
            raise SessionError("FILE") from None

    def write(self, data: Any) -> None:
        name = None
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            fd, name = tempfile.mkstemp(prefix=".tdm-session-", dir=self.path.parent)
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                json.dump(data, stream, allow_nan=False)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(name, self.path)
        except (OSError, ValueError, TypeError):
            raise SessionError("SAVE") from None
        finally:
            if name is not None:
                Path(name).unlink(missing_ok=True)


@dataclass(frozen=True)
class SessionBundle:
    captured_at: float
    expires_at: float
    user_agent: str
    headers: Mapping[str, str] = field(repr=False)

    MAX_BYTES = 65536
    HEADER_NAMES = frozenset({
        "authorization", "client-id", "client-integrity", "client-version",
        "client-session-id", "x-device-id", "device-id", "accept-language",
    })

    @classmethod
    def from_json(cls, raw: bytes, *, now: float | None = None) -> SessionBundle:
        try:
            if len(raw) > cls.MAX_BYTES:
                raise ValueError
            data = json.loads(raw)
        except (ValueError, UnicodeError, RecursionError):
            raise SessionError("FORMAT") from None
        return cls.from_dict(data, now=now)

    @classmethod
    def from_dict(cls, data: Any, *, now: float | None = None) -> SessionBundle:
        now = time.time() if now is None else now
        try:
            if not isinstance(data, dict) or set(data) != {
                "version", "captured_at", "expires_at", "user_agent", "headers",
            } or type(data["version"]) is not int or data["version"] != 1:
                raise ValueError
            captured, expiry = data["captured_at"], data["expires_at"]
            if any(type(v) not in (int, float) or not math.isfinite(v)
                   for v in (captured, expiry)):
                raise ValueError
            if not 0 < captured <= now + 60 or not 0 < expiry - captured <= 86400:
                raise ValueError
            headers, user_agent = data["headers"], data["user_agent"]
            if not isinstance(headers, dict) or not set(headers) <= cls.HEADER_NAMES:
                raise ValueError
            for value in (user_agent, *headers.values()):
                if not isinstance(value, str) or not re.fullmatch(r"[\x20-\x7e]{1,16384}", value):
                    raise ValueError
            if len(user_agent) > 1024:
                raise ValueError
            if (
                headers.get("client-id") != ClientType.WEB.CLIENT_ID
                or not re.fullmatch(r"OAuth [A-Za-z0-9_-]{1,512}", headers.get("authorization", ""))
                or not headers.get("client-integrity", "").strip()
                or not (headers.get("x-device-id") or headers.get("device-id"))
            ):
                raise ValueError
            if len(json.dumps(data).encode()) > cls.MAX_BYTES:
                raise ValueError
            return cls(float(captured), float(expiry), user_agent, MappingProxyType(dict(headers)))
        except (KeyError, TypeError, ValueError, OverflowError):
            raise SessionError("FORMAT") from None

    @property
    def token(self) -> str:
        return self.headers["authorization"][6:]

    def require_fresh(self, now: float) -> None:
        if self.expires_at <= now:
            raise SessionError("EXPIRED")

    def request_headers(self) -> dict[str, str]:
        return {
            **self.headers, "user-agent": self.user_agent,
            "origin": "https://www.twitch.tv", "referer": "https://www.twitch.tv/",
            "content-type": "application/json",
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": 1, "captured_at": self.captured_at, "expires_at": self.expires_at,
            "user_agent": self.user_agent, "headers": dict(self.headers),
        }
