import asyncio
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from src.web.app import ProxyVerifyRequest, verify_proxy


class MockResponseContext:
    def __init__(self, response_or_exc):
        self.response_or_exc = response_or_exc

    async def __aenter__(self):
        if isinstance(self.response_or_exc, Exception):
            raise self.response_or_exc
        return self.response_or_exc

    async def __aexit__(self, exc_type, exc, tb):
        pass


class TestVerifyProxy(unittest.TestCase):
    def setUp(self):
        # Patch aiohttp.ClientSession
        self.session_patcher = patch("aiohttp.ClientSession")
        self.mock_session_cls = self.session_patcher.start()
        # session object itself is not async, it has async methods/CMs
        self.mock_session = MagicMock()
        # Ensure the session context manager returns our mock session
        # ClientSession() -> CM -> __aenter__ -> session
        self.mock_session_cls.return_value.__aenter__.return_value = self.mock_session

    def tearDown(self):
        self.session_patcher.stop()

    def test_verify_proxy_success(self):
        # Mock successful response
        mock_response = AsyncMock()
        mock_response.status = 200

        # Configure get to return our custom context manager
        self.mock_session.get.side_effect = lambda *args, **kwargs: MockResponseContext(
            mock_response
        )

        request = ProxyVerifyRequest(proxy="http://valid-proxy:8080")

        # Run async function
        result = asyncio.run(verify_proxy(request))

        self.assertTrue(result["success"])
        self.assertIn("Connected!", result["message"])
        self.assertIn("latency", result)

    def test_verify_proxy_failure_status(self):
        # Mock error status response
        mock_response = AsyncMock()
        mock_response.status = 503

        self.mock_session.get.side_effect = lambda *args, **kwargs: MockResponseContext(
            mock_response
        )

        request = ProxyVerifyRequest(proxy="http://bad-proxy:8080")

        result = asyncio.run(verify_proxy(request))

        self.assertFalse(result["success"])

        # The expected message in app.py is: f"Proxy reachable but returned {response.status}"
        self.assertIn("Proxy reachable but returned 503", result["message"])

    def test_verify_proxy_connection_error(self):
        # Mock connection error
        error = Exception("Connection refused")
        self.mock_session.get.side_effect = lambda *args, **kwargs: MockResponseContext(error)

        request = ProxyVerifyRequest(proxy="http://down-proxy:8080")

        result = asyncio.run(verify_proxy(request))

        self.assertFalse(result["success"])
        self.assertIn("Connection failed", result["message"])

    def test_verify_proxy_empty(self):
        request = ProxyVerifyRequest(proxy="")
        result = asyncio.run(verify_proxy(request))
        self.assertFalse(result["success"])
        self.assertEqual(result["message"], "Proxy URL is empty")


if __name__ == "__main__":
    unittest.main()
