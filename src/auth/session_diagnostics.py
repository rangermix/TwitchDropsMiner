"""Bounded fixed labels for authentication failures, without remote text or secrets."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, ClassVar


@dataclass(frozen=True)
class SessionDiagnostic:
    """A safe projection; HTTP fields distinguish dashboard and Twitch responses."""

    HTTP_STATUSES: ClassVar[dict[str, int]] = {
        "CONNECTION": 401, "HELPER_DISABLED": 403, "BUSY": 429,
        "STOPPED": 503, "UNAVAILABLE": 503, "BROWSER_START": 503, "HELPER_FAILED": 503,
    }

    CODES: ClassVar[frozenset[str]] = frozenset({
        "ACCOUNT_MISMATCH", "AUTH", "BROWSER_ADDRESS", "BROWSER_CLOSE",
        "BROWSER_FAILED", "BROWSER_ISOLATION", "BROWSER_OWNER", "BROWSER_PROTOCOL",
        "BROWSER_START", "BROWSER_STATE", "BROWSER_STOP", "BROWSER_UNAVAILABLE", "BUSY",
        "CAPTURE_LIMIT", "CAPTURE_TIMEOUT", "CATALOG", "CONFIG", "CONNECTION", "DELIVERY",
        "DESTINATION", "EXPIRED", "FILE",
        "FORMAT", "HELPER_BROWSER", "HELPER_BROWSER_CLEANUP", "HELPER_BROWSER_MISSING",
        "HELPER_BROWSER_OWNER", "HELPER_BUSY", "HELPER_CA_FILE", "HELPER_INSECURE_TLS", "HELPER_CANCELLED", "HELPER_CHROME_MISSING",
        "HELPER_CHROMIUM_MISSING", "HELPER_DESTINATION", "HELPER_DISABLED", "HELPER_EXPIRED",
        "HELPER_FAILED", "HELPER_FIREFOX_LOGIN", "HELPER_FIREFOX_MISSING",
        "HELPER_FIREFOX_VERSION", "HELPER_LOGIN_MISSING", "HELPER_LOGIN_TIMEOUT",
        "HELPER_NETWORK", "HELPER_PROFILE_CLEANUP", "HELPER_REDIRECT", "HELPER_REJECTED",
        "HELPER_RESPONSE", "HELPER_RESULT_UNKNOWN", "HELPER_SERVER_BROWSER", "HELPER_STATE",
        "IDENTITY", "LOGIN_MISSING", "LOGIN_TIMEOUT", "PAIRING", "REPLAY", "REQUEST",
        "RESPONSE", "SAVE", "UNAVAILABLE", "SDK_COOKIE", "SDK_EXPIRED", "SDK_ISSUANCE", "SDK_PAGE",
        "SDK_SEED", "SDK_TIMEOUT", "STALE", "STOPPED",
    })
    STAGES: ClassVar[frozenset[str]] = frozenset({
        "unknown", "helper_configuration", "helper_connect", "browser_start", "browser_login",
        "browser_capture", "browser_cleanup", "session_upload", "helper_receipt",
        "helper_admission", "seed_validation", "captured_validation", "server_issuance",
        "issued_validation", "persistence", "renewal_issuance", "renewal_validation",
    })
    REASONS: ClassVar[frozenset[str]] = frozenset({
        "unknown", "unexpected_exception", "timeout", "tls", "dns", "connection",
        "http_error", "invalid_json", "response_limit", "redirect", "invalid_response",
        "integrity", "persisted_query_not_found", "service", "catalog_shape",
        "identity_shape", "account_mismatch",
    })
    OPERATIONS: ClassVar[frozenset[str]] = frozenset({
        "unknown", "identity", "Inventory", "Campaigns", "catalog",
    })

    code: str = "HELPER_FAILED"
    stage: str = "unknown"
    reason: str = "unknown"
    operation: str = "unknown"
    http_status: int | None = None
    upstream_status: int | None = None

    def __post_init__(self) -> None:
        # Sanitize direct construction too, so to_dict and repr are always safe.
        for name, allowed, fallback in (
            ("code", self.CODES, "HELPER_FAILED"),
            ("stage", self.STAGES, "unknown"),
            ("reason", self.REASONS, "unknown"),
            ("operation", self.OPERATIONS, "unknown"),
        ):
            value = getattr(self, name)
            if type(value) is not str or value not in allowed:
                object.__setattr__(self, name, fallback)
        for name in ("http_status", "upstream_status"):
            value = getattr(self, name)
            if type(value) is not int or not 100 <= value <= 599:
                object.__setattr__(self, name, None)

    def to_dict(self) -> dict[str, Any]:
        return {"code": self.code, "stage": self.stage, "reason": self.reason,
                "operation": self.operation, "http_status": self.http_status,
                "upstream_status": self.upstream_status}

    @classmethod
    def from_dict(cls, value: Any) -> SessionDiagnostic:
        """Project only known keys and labels from a decoded remote response."""
        if type(value) is not dict:
            return cls()
        return cls(code=value.get("code", "HELPER_FAILED"), stage=value.get("stage", "unknown"),
                   reason=value.get("reason", "unknown"), operation=value.get("operation", "unknown"),
                   http_status=value.get("http_status"), upstream_status=value.get("upstream_status"))
