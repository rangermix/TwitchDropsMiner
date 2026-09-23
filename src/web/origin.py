"""Dashboard public origin, independent of forwarded-header and client-IP trust."""

from __future__ import annotations

import re
from ipaddress import IPv4Address
from urllib.parse import urlsplit

from pydantic import AnyHttpUrl
from starlette.requests import HTTPConnection


class DashboardOrigin:
    def __init__(self, public_base_url: str = ""):
        self.public_origin = self._parse(public_base_url) if public_base_url else None

    @staticmethod
    def _parse(value: str) -> str:
        try:
            # Reject characters URL parsers may silently discard or reinterpret.
            if any(char.isspace() or ord(char) < 32 or char in "\\%?#" for char in value):
                raise ValueError
            parts = urlsplit(value)
            # Use browser-compatible serialization, including mapped IPv6 and IDNA.
            url = AnyHttpUrl(value)
            if (
                parts.scheme not in ("http", "https")
                or not parts.netloc
                or parts.path not in ("", "/")
                or parts.username is not None
                or parts.password is not None
                or not re.fullmatch(r"(?:\[[0-9a-fA-F:.]+\]|[^:\[\]]+)(?::[0-9]+)?", parts.netloc)
                or not url.host
                or parts.netloc.endswith(":")
                or url.port is None
                or not 1 <= url.port <= 65535
                or (":" not in url.host and not re.fullmatch(r"[a-z0-9_.-]+", url.host))
            ):
                raise ValueError
            host = parts.hostname or ""
            if ":" not in host and re.fullmatch(r"[0-9]+|0x[0-9a-f]*", host.removesuffix(".").rsplit(".", 1)[-1]):
                # Require dotted decimal instead of legacy short/octal/hex IPv4 forms.
                IPv4Address(host.removesuffix("."))
            return str(url).removesuffix("/")
        except (ValueError, UnicodeError):
            # The supplied URL could contain accidentally pasted credentials.
            raise ValueError(
                "PUBLIC_BASE_URL must be one absolute http(s) root URL without "
                "credentials, a path, query, or fragment"
            ) from None

    def expected(self, connection: HTTPConnection) -> str:
        if self.public_origin is not None:
            return self.public_origin
        scheme = "https" if connection.scope["scheme"] in ("https", "wss") else "http"
        return f"{scheme}://{connection.headers.get('host', '')}"

    def secure_cookie(self, connection: HTTPConnection) -> bool:
        if self.public_origin is not None:
            return self.public_origin.startswith("https://")
        return connection.scope["scheme"] in ("https", "wss")
