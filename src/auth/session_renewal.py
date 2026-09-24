"""Account-bound delivery and scheduling for the local browser helper."""

from __future__ import annotations

import asyncio
import json
import math
import re
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

import aiohttp
from yarl import URL

from src.auth.session_bundle import SessionBundle, SessionError
from src.auth.session_helper import BrowserExporter


@dataclass(frozen=True)
class RenewalConnection:
    endpoint: str
    user_id: int
    credential: str = field(repr=False)

    @classmethod
    def from_dict(cls, data: Any) -> RenewalConnection:
        try:
            if (not isinstance(data, dict) or set(data) != {'version', 'endpoint', 'user_id', 'credential'}
                    or type(data['version']) is not int or data['version'] != 1
                    or type(data['user_id']) is not int or data['user_id'] <= 0
                    or not isinstance(data['credential'], str)
                    or not re.fullmatch(r'[A-Za-z0-9_-]{43}', data['credential'])
                    or not isinstance(data['endpoint'], str)
                    or any(c.isspace() or ord(c) < 32 or ord(c) == 127 or c in '\\%?#' for c in data['endpoint'])):
                raise ValueError
            url = URL(data['endpoint'])
            if (not url.host or url.user is not None or url.password is not None or url.query or url.fragment
                    or url.raw_path != '/api/session/renew'
                    or not (url.scheme == 'https' or (url.scheme == 'http' and url.host in ('localhost', '127.0.0.1', '::1')))):
                raise ValueError
            return cls(str(url), data['user_id'], data['credential'])
        except (KeyError, TypeError, ValueError):
            raise SessionError('CONNECTION') from None


class RenewalSender:
    def __init__(self, connection: RenewalConnection):
        self.connection = connection

    async def send(self, bundle: SessionBundle) -> dict[str, Any]:
        try:
            async with aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=90), cookie_jar=aiohttp.DummyCookieJar(),
            ) as http, http.post(
                self.connection.endpoint, allow_redirects=False, json=bundle.to_dict(),
                headers={'Authorization': 'Bearer ' + self.connection.credential, 'X-TDM-Request': '1'},
            ) as response:
                if response.status in (401, 403):
                    raise SessionError('PAIRING')
                if 300 <= response.status < 400:
                    raise SessionError('DESTINATION')
                raw = bytearray()
                async for chunk in response.content.iter_chunked(8192):
                    raw.extend(chunk)
                    if len(raw) > 65536:
                        raise ValueError
                data = json.loads(raw)
                if response.status != 200:
                    if isinstance(data, dict) and data.get('detail') == 'session_account_mismatch':
                        raise SessionError('ACCOUNT_MISMATCH')
                    raise SessionError('DELIVERY')
                status = data['session']
                if status['user_id'] != self.connection.user_id:
                    raise SessionError('ACCOUNT_MISMATCH')
                expiry = status['expires_at']
                if (data.get('success') is not True or status['state'] != 'ready'
                        or type(expiry) not in (float, int) or not math.isfinite(expiry)
                        or expiry != bundle.expires_at
                        or type(status['generation']) is not int or status['generation'] < 1):
                    raise ValueError
                return {'expires_at': expiry, 'generation': status['generation']}
        except (aiohttp.ClientError, TimeoutError, KeyError, TypeError, ValueError, RecursionError):
            raise SessionError('DELIVERY') from None


class RenewalLoop:
    def __init__(
        self, exporter: BrowserExporter, sender: RenewalSender, *,
        clock: Callable[[], float] = time.time,
        sleep: Callable[[float], Awaitable[Any]] = asyncio.sleep,
        report: Callable[[dict[str, Any]], None] = lambda event: print(json.dumps(event), flush=True),
        renew_before: float = 300,
    ):
        if not 30 <= renew_before <= 3600:
            raise SessionError('CONFIG')
        self.exporter, self.sender = exporter, sender
        self.clock, self.sleep, self.report = clock, sleep, report
        self.renew_before = renew_before

    async def run(self) -> None:
        retry = 5.0
        accepted_expiry = 0.0
        while True:
            try:
                bundle = await self.exporter.capture()
                bundle.require_fresh(self.clock())
                result = await self.sender.send(bundle)
                self.report({'event': 'renewed', **result})
                retry = 5.0
                accepted_expiry = result['expires_at']
                remaining = accepted_expiry - self.clock()
                delay = max(0, min(30, remaining / 2), remaining - self.renew_before)
            except SessionError as error:
                if error.code in {'PAIRING', 'ACCOUNT_MISMATCH', 'DESTINATION', 'CONNECTION'}:
                    raise
                remaining = accepted_expiry - self.clock()
                delay = min(retry, max(1, remaining / 2)) if remaining > 0 else retry
                retry = min(300, retry * 2)
                self.report({'event': 'retry', 'code': error.code, 'retry_in': delay})
            await self.sleep(delay)
