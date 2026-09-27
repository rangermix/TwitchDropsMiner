"""Private, narrowly scoped bootstrap state for server-side SDK renewal."""

from __future__ import annotations

import json
import math
import re
import time
from dataclasses import dataclass, field
from typing import Any

from src.auth.session_bundle import SessionBundle, SessionError


@dataclass(frozen=True)
class SDKCookie:
    value: str = field(repr=False)
    expires_at: float

    NAME = "KP_UIDz-ssn"
    DOMAIN = "k.twitchcdn.net"
    URL = "https://k.twitchcdn.net/"

    @classmethod
    def from_dict(cls, data: Any) -> SDKCookie:
        try:
            if not isinstance(data, dict) or set(data) != {"value", "expires_at"}:
                raise ValueError
            value, expiry = data["value"], data["expires_at"]
            if (
                not isinstance(value, str)
                or not re.fullmatch(r"[\x21\x23-\x2b\x2d-\x3a\x3c-\x5b\x5d-\x7e]{1,8192}", value)
                or type(expiry) not in (int, float) or not math.isfinite(expiry) or expiry <= 0
            ):
                raise ValueError
            return cls(value, float(expiry))
        except (KeyError, TypeError, ValueError, OverflowError):
            raise SessionError("SDK_COOKIE") from None

    @classmethod
    def from_browser(cls, cookies: Any, *, now: float) -> SDKCookie:
        if not isinstance(cookies, list):
            raise SessionError("SDK_COOKIE")
        candidates = [cookie for cookie in cookies if isinstance(cookie, dict) and (
            cookie.get("name") == cls.NAME and cookie.get("domain") == cls.DOMAIN
            and cookie.get("path") == "/" and cookie.get("secure") is True
            and cookie.get("httpOnly") is True
        )]
        if len(candidates) != 1:
            raise SessionError("SDK_COOKIE")
        cookie = candidates[0]
        result = cls.from_dict({"value": cookie.get("value"), "expires_at": cookie.get("expires")})
        result.require_fresh(now)
        return result

    def require_fresh(self, now: float) -> None:
        if self.expires_at <= now:
            raise SessionError("SDK_EXPIRED")

    def to_dict(self) -> dict[str, Any]:
        return {"value": self.value, "expires_at": self.expires_at}

    def to_browser_cookie(self) -> dict[str, Any]:
        return {
            "name": self.NAME, "value": self.value, "expires": self.expires_at,
            "domain": self.DOMAIN, "path": "/", "secure": True,
            "httpOnly": True, "sameSite": "None",
        }


@dataclass(frozen=True)
class ServerSeed:
    bundle: SessionBundle
    cookie: SDKCookie

    @classmethod
    def from_dict(cls, data: Any, *, now: float | None = None) -> ServerSeed:
        now = time.time() if now is None else now
        try:
            if (
                not isinstance(data, dict) or set(data) != {"version", "bundle", "sdk_cookie"}
                or type(data["version"]) is not int or data["version"] != 1
                or len(json.dumps(data).encode()) > SessionBundle.MAX_BYTES
            ):
                raise ValueError
            return cls(
                SessionBundle.from_dict(data["bundle"], now=now),
                SDKCookie.from_dict(data["sdk_cookie"]),
            )
        except (KeyError, TypeError, ValueError, OverflowError, RecursionError):
            raise SessionError("SDK_SEED") from None

    def to_dict(self) -> dict[str, Any]:
        return {"version": 1, "bundle": self.bundle.to_dict(), "sdk_cookie": self.cookie.to_dict()}
