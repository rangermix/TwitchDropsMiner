"""
HTTP client for Twitch API requests.

Handles HTTP session management, request retries, and connection quality settings.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING
from collections import abc

import aiohttp
from yarl import URL

from src.i18n import _
from src.exceptions import ExitRequest, RequestInvalid
from src.utils import ExponentialBackoff
from src.config import COOKIES_PATH

if TYPE_CHECKING:
    from src.config.settings import Settings
    from src.web.gui_manager import WebGUIManager
    from src.config import ClientInfo


logger = logging.getLogger("TwitchDrops")


class HTTPClient:
    """
    Manages HTTP session and handles request retries with exponential backoff.

    This client provides:
    - Session management with cookie persistence
    - Automatic request retries on connection errors
    - Connection quality-based timeout configuration
    - Proxy support
    """

    def __init__(
        self,
        settings: Settings,
        gui: WebGUIManager,
        client_type: ClientInfo,
    ):
        """
        Initialize the HTTP client.

        Parameters
        ----------
        settings : Settings
            Application settings for connection quality and proxy configuration
        gui : WebGUIManager
            GUI manager for user notifications and close detection
        client_type : ClientInfo
            Client type information (User-Agent, Client-ID, etc.)
        """
        self.settings = settings
        self.gui = gui
        self._client_type = client_type
        self._session: aiohttp.ClientSession | None = None

    async def get_session(self) -> aiohttp.ClientSession:
        """
        Get or create the HTTP session.

        Returns
        -------
        aiohttp.ClientSession
            The active HTTP session

        Raises
        ------
        RuntimeError
            If the session is closed
        """
        if (session := self._session) is not None:
            if session.closed:
                raise RuntimeError("Session is closed")
            return session

        # Load cookies
        cookie_jar = aiohttp.CookieJar()
        try:
            if COOKIES_PATH.exists():
                cookie_jar.load(COOKIES_PATH)
        except Exception:
            # If loading cookies fails, clear the jar and continue
            cookie_jar.clear()

        # Create timeouts based on connection quality
        connection_quality = self.settings.connection_quality
        if connection_quality < 1:
            connection_quality = self.settings.connection_quality = 1
        elif connection_quality > 6:
            connection_quality = self.settings.connection_quality = 6

        timeout = aiohttp.ClientTimeout(
            sock_connect=5 * connection_quality,
            total=10 * connection_quality,
        )

        # Create session with connection pooling
        connector = aiohttp.TCPConnector(limit=50)
        self._session = aiohttp.ClientSession(
            timeout=timeout,
            connector=connector,
            cookie_jar=cookie_jar,
            headers={"User-Agent": self._client_type.USER_AGENT},
        )
        return self._session

    @asynccontextmanager
    async def request(
        self,
        method: str,
        url: URL | str,
        *,
        invalidate_after: datetime | None = None,
        **kwargs,
    ) -> abc.AsyncIterator[aiohttp.ClientResponse]:
        """
        Make an HTTP request with automatic retries.

        Parameters
        ----------
        method : str
            HTTP method (GET, POST, etc.)
        url : URL | str
            Request URL
        invalidate_after : datetime | None, optional
            Datetime after which the request should not be retried
        **kwargs
            Additional arguments passed to aiohttp.ClientSession.request

        Yields
        ------
        aiohttp.ClientResponse
            The HTTP response

        Raises
        ------
        ExitRequest
            If the application is closing
        RequestInvalid
            If the request expires during retry loop
        aiohttp.ClientConnectorCertificateError
            If SSL verification fails
        """
        session = await self.get_session()
        method = method.upper()

        if self.settings.proxy and "proxy" not in kwargs:
            kwargs["proxy"] = self.settings.proxy

        logger.debug(f"Request: ({method=}, {url=}, {kwargs=})")
        session_timeout = timedelta(seconds=session.timeout.total or 0)
        backoff = ExponentialBackoff(maximum=3 * 60)

        for delay in backoff:
            if self.gui.close_requested:
                raise ExitRequest()
            elif (
                invalidate_after is not None
                # Account for expiration landing during the request
                and datetime.now(timezone.utc) >= (invalidate_after - session_timeout)
            ):
                raise RequestInvalid()

            try:
                response: aiohttp.ClientResponse | None = None
                response = await self.gui.coro_unless_closed(
                    session.request(method, url, **kwargs)
                )
                assert response is not None
                logger.debug(f"Response: {response.status}: {response}")

                if response.status < 500:
                    # Pre-read the response to avoid getting errors outside the context manager
                    raw_response = await response.read()  # noqa: F841
                    yield response
                    return

                self.gui.print(_("error", "site_down").format(seconds=round(delay)))
            except aiohttp.ClientConnectorCertificateError:
                # SSL verification failures should not be retried
                raise
            except (
                aiohttp.ClientConnectionError,
                asyncio.TimeoutError,
                aiohttp.ClientPayloadError,
            ):
                # Connection problems, retry with backoff
                if backoff.steps > 1:
                    # Don't show quick retries to the user
                    self.gui.print(_("error", "no_connection").format(seconds=round(delay)))
            finally:
                if response is not None:
                    response.release()

            # Wait for the backoff delay or until the GUI closes
            with asyncio.suppress(asyncio.TimeoutError):
                await asyncio.wait_for(self.gui.wait_until_closed(), timeout=delay)

    async def close(self) -> None:
        """
        Close the HTTP session and save cookies.

        This should be called during application shutdown.
        """
        if self._session is not None:
            cookie_jar = self._session.cookie_jar
            assert isinstance(cookie_jar, aiohttp.CookieJar)

            # Clear empty cookie entries before saving
            # NOTE: Unfortunately, aiohttp provides no easy way of clearing empty cookies,
            # so we need to access the private '_cookies' attribute
            for cookie_key, cookie in list(cookie_jar._cookies.items()):
                if not cookie:
                    del cookie_jar._cookies[cookie_key]

            cookie_jar.save(COOKIES_PATH)
            await self._session.close()
            self._session = None
