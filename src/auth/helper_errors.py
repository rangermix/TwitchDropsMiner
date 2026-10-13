"""Fixed, translated helper diagnostics without remote response text."""

from __future__ import annotations

from src.i18n import _


class HelperDiagnostics:
    KEYS = {
        "HELPER_DESTINATION": "destination", "HELPER_DISABLED": "disabled",
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
    }

    @classmethod
    def describe(cls, code: str) -> tuple[str, str]:
        # Unknown/internal exception text cannot become a displayed error code.
        safe_code = code if code in cls.KEYS else "HELPER_FAILED"
        words = _.t["helper"]["errors"]
        return "SESSION_" + safe_code, words[cls.KEYS[safe_code]]  # type: ignore[literal-required]
