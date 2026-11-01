"""Fixed, translated helper diagnostics without remote response text."""

from __future__ import annotations

from src.auth.session_diagnostics import SessionDiagnostic
from src.i18n import _


class HelperDiagnostics:
    KEYS = {
        "HELPER_DESTINATION": "destination", "HELPER_CA_FILE": "ca_file", "HELPER_INSECURE_TLS": "insecure_tls", "HELPER_DISABLED": "disabled",
        "HELPER_EXPIRED": "expired", "HELPER_BUSY": "busy",
        "HELPER_CHROME_MISSING": "chrome_missing", "HELPER_FIREFOX_MISSING": "firefox_missing",
        "HELPER_CHROMIUM_MISSING": "chromium_missing", "HELPER_LOGIN_MISSING": "login_missing",
        "HELPER_BROWSER_MISSING": "browser_missing", "HELPER_FIREFOX_VERSION": "firefox_version",
        "HELPER_FIREFOX_LOGIN": "firefox_login",
        "HELPER_BROWSER": "browser", "HELPER_BROWSER_OWNER": "browser_owner",
        "HELPER_LOGIN_TIMEOUT": "login_timeout", "HELPER_NETWORK": "network",
        "HELPER_REDIRECT": "redirect", "HELPER_RESPONSE": "response",
        "HELPER_REJECTED": "rejected", "HELPER_SERVER_BROWSER": "server_browser",
        "HELPER_RESULT_UNKNOWN": "result_unknown", "HELPER_BROWSER_CLEANUP": "browser_cleanup",
        "HELPER_PROFILE_CLEANUP": "profile_cleanup", "BROWSER_ADDRESS": "browser_protocol",
        "BROWSER_PROTOCOL": "browser_protocol", "CAPTURE_TIMEOUT": "capture_timeout",
        "CAPTURE_LIMIT": "capture_limit", "EXPIRED": "session_expired", "SDK_EXPIRED": "session_expired",
        "REPLAY": "session_expired", "FORMAT": "session_invalid", "SDK_SEED": "session_invalid",
        "SDK_COOKIE": "sdk", "SDK_PAGE": "sdk", "SDK_TIMEOUT": "sdk", "SDK_ISSUANCE": "sdk",
        "AUTH": "validation", "IDENTITY": "validation", "CATALOG": "validation",
        "ACCOUNT_MISMATCH": "account", "REQUEST": "twitch_network", "HELPER_FAILED": "failed",
        "BROWSER_START": "server_browser", "BROWSER_STOP": "failed",
        "BROWSER_CLOSE": "failed",
        "BROWSER_FAILED": "browser", "BROWSER_ISOLATION": "server_browser",
        "BROWSER_OWNER": "browser_owner", "BROWSER_STATE": "failed",
        "BROWSER_UNAVAILABLE": "server_browser", "BUSY": "busy", "CONNECTION": "expired",
        "FILE": "failed", "SAVE": "failed", "STALE": "session_expired",
        "STOPPED": "failed", "HELPER_STATE": "failed", "HELPER_CANCELLED": "failed",
        "LOGIN_MISSING": "login_missing", "LOGIN_TIMEOUT": "login_timeout",
        "PAIRING": "session_invalid", "RESPONSE": "response",
        "CONFIG": "failed", "DESTINATION": "destination", "DELIVERY": "failed", "UNAVAILABLE": "failed",
    }

    # Fixed errors emitted by the server protocol, separate from local helper failures.
    SERVER_CODES = frozenset({
        "ACCOUNT_MISMATCH", "AUTH", "BROWSER_ADDRESS", "BROWSER_CLOSE", "BROWSER_FAILED",
        "BROWSER_ISOLATION", "BROWSER_OWNER", "BROWSER_PROTOCOL", "BROWSER_START",
        "BROWSER_STATE", "BROWSER_STOP", "BROWSER_UNAVAILABLE", "BUSY", "CAPTURE_LIMIT",
        "CAPTURE_TIMEOUT", "CATALOG", "CONNECTION", "EXPIRED", "FILE", "FORMAT",
        "HELPER_CANCELLED", "HELPER_DISABLED", "HELPER_EXPIRED", "HELPER_FAILED",
        "HELPER_STATE", "IDENTITY", "LOGIN_MISSING", "LOGIN_TIMEOUT", "PAIRING", "REPLAY",
        "REQUEST", "RESPONSE", "SAVE", "SDK_COOKIE", "SDK_EXPIRED", "SDK_ISSUANCE",
        "SDK_PAGE", "SDK_SEED", "SDK_TIMEOUT", "STALE", "STOPPED",
        "CONFIG", "DESTINATION", "DELIVERY", "UNAVAILABLE",
    })

    # Loss of receipt access is not evidence that the credential upload failed.
    RECEIPT_FAILURES = SERVER_CODES - {
        "CONNECTION", "HELPER_EXPIRED", "HELPER_DISABLED", "BUSY", "HELPER_STATE", "UNAVAILABLE",
        "HELPER_FAILED", "DELIVERY",
    }

    CONNECT_CODES = frozenset({
        "FORMAT", "CONNECTION", "HELPER_DISABLED", "BUSY", "HELPER_STATE", "HELPER_EXPIRED",
        "STOPPED", "UNAVAILABLE", "HELPER_FAILED",
    })

    @staticmethod
    def expected_status(code: str) -> int:
        return SessionDiagnostic.HTTP_STATUSES.get(code, 400)

    @classmethod
    def describe(cls, code: str) -> tuple[str, str]:
        # Unknown/internal exception text cannot become a displayed error code.
        safe_code = code if code in cls.KEYS else "HELPER_FAILED"
        words = _.t["helper"]["errors"]
        return "SESSION_" + safe_code, words[cls.KEYS[safe_code]]  # type: ignore[literal-required]
