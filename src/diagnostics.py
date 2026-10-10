"""Bounded, deidentified operational evidence saved only on a dashboard request."""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
import math
import os
import platform
import re
import secrets
import stat
import sys
import tempfile
from collections import OrderedDict, deque
from datetime import datetime, timezone
from itertools import islice
from pathlib import Path
from time import monotonic
from typing import TYPE_CHECKING, Any
from urllib.parse import urlsplit

from dateutil.parser import isoparse

from src.config import GQL_OPERATIONS, State
from src.version import __version__


if TYPE_CHECKING:
    from src.core.client import Twitch


class DiagnosticLogHandler(logging.Handler):
    """Keep warning locations and exception types, never formatted log messages."""

    def __init__(self, diagnostics: Diagnostics):
        super().__init__(logging.WARNING)
        self.diagnostics = diagnostics

    def emit(self, record: logging.LogRecord) -> None:
        self.diagnostics.logs.append({
            "level": record.levelname if record.levelname in ("WARNING", "ERROR", "CRITICAL") else "ERROR",
            "module": record.module if record.module in self.diagnostics.LOG_MODULES else self.diagnostics.reference(record.module),
            "line": record.lineno,
            "exception": self.diagnostics.exception_name(record.exc_info[1])
            if record.exc_info and record.exc_info[1] is not None else None,
        })


class Diagnostics:
    MAX_APIS = 128
    MAX_BODY_BYTES = 262144
    MAX_DUMP_BYTES = 8 * 1024 * 1024
    MAX_FILES = 10
    MAX_SETTINGS_ITEMS = 256
    MAX_PRECONDITIONS = 64
    EXCEPTIONS = frozenset({"ValueError", "TypeError", "KeyError", "IndexError", "RuntimeError", "OSError",
                            "PermissionError", "FileNotFoundError", "TimeoutError", "CancelledError", "ClientError",
                            "ClientConnectionError", "ClientConnectorError", "ClientSSLError", "ClientPayloadError",
                            "HTTPException", "GQLException", "MinerException", "SessionError", "LoginException", "WebsocketClosed"})
    ERROR_CODES = frozenset({"AUTH", "IDENTITY", "ACCOUNT_MISMATCH", "CATALOG", "REQUEST", "FILE", "FORMAT", "EXPIRED",
                             "REPLAY", "SDK_EXPIRED", "SDK_TIMEOUT", "SDK_ISSUANCE", "SDK_COOKIE", "SDK_PAGE", "SDK_SEED",
                             "CAPTURE_TIMEOUT", "STOPPED", "STALE", "UNAUTHENTICATED", "FORBIDDEN", "PERSISTED_QUERY_NOT_FOUND"})
    DATE_FIELDS = frozenset({"startAt", "endAt", "expiresAt", "createdAt", "awardedAt", "lastAwardedAt", "published_at"})
    LOCALES = frozenset({"English", "Dansk", "Deutsch", "Español", "Français", "Magyar", "Indonesian", "Italiano",
                        "Nederlandse", "Polski", "Português", "Română", "Türkçe", "Čeština", "Русский", "Українська",
                        "العربية", "日本語", "简体中文", "繁體中文"})
    LOG_MODULES = frozenset(["client", "gql_client", "http_client", "auth_state", "channel", "watch_service", "inventory_service", "maintenance", "message_handlers", "websocket", "pool", "app", "gui_manager", "server_renewal", "container_login", "session_controller", "imported_session", "telegram_service", "origin", "settings", "login", "drop"])
    # Preserve schema keys, not arbitrary dictionary keys that can contain identities.
    FIELDS = frozenset(["data", "errors", "error", "message", "path", "locations", "extensions", "operationName", "line", "column", "user", "currentUser", "viewer", "self", "id", "login", "name", "displayName", "user_id", "username", "first_name", "last_name", "email", "client_id", "scopes", "expires_in", "access_token", "token", "authorization", "password", "cookies", "headers", "device_id", "session_id", "signature", "banStatus", "isPermanent", "expiresAt", "createdAt", "expiresInMs", "reason", "bannedUser", "moderator", "roomOwner", "game", "stream", "streams", "edges", "node", "cursor", "pageInfo", "hasNextPage", "endCursor", "viewersCount", "title", "broadcastSettings", "broadcaster", "tags", "isPartner", "isAffiliate", "isMature", "dropsEnabled", "hasDropsEnabled", "dropCampaigns", "campaigns", "campaign", "drop", "drops", "dropCampaignsInProgress", "dropCampaignsCompleted", "timeBasedDrops", "claimedBenefits", "gameEvent", "gameEvents", "status", "startAt", "endAt", "allow", "channels", "isEnabled", "isAccountConnected", "accountLinkURL", "benefitEdges", "benefit", "benefitType", "type", "imageURL", "requiredMinutesWatched", "currentMinutesWatched", "requiredSubs", "isClaimed", "canClaim", "preconditionDrops", "preconditionMinutes", "dropInstanceID", "dropID", "campaignID", "claimDropRewards", "claimDropClaimId", "claimDrop", "userID", "channelID", "playbackAccessToken", "streamPlaybackAccessToken", "videoPlaybackAccessToken", "value", "inventory", "currentDrop", "dropCurrentSession", "awardedAt", "lastAwardedAt", "minutesWatched", "availableBadges", "isEligibleForRewards", "isViewerEligibleForRewards", "notifications", "notification", "notificationID", "code", "success", "error_code", "description", "ok", "result", "message_id", "date", "chat", "from", "text", "is_bot", "language_code", "retry_after", "parameters", "tag_name", "html_url", "assets", "size", "browser_download_url", "published_at", "prerelease", "draft", "state", "generation", "renewal_error", "renewal_available", "renewal_requires_login", "session", "attempt", "timeout", "remaining_seconds", "authenticated", "logged_out", "ready", "login_state", "connected", "topic", "type", "nonce", "version", "response", "expiration", "__typename", "drop_id", "campaign_id", "current_progress_min", "required_progress_min", "viewers", "old_game"])
    NUMBERS = frozenset(["line", "column", "viewersCount", "requiredMinutesWatched", "currentMinutesWatched", "requiredSubs", "minutesWatched", "size", "expiresInMs", "expires_in", "error_code", "retry_after", "generation", "remaining_seconds", "timeout", "version", "code", "current_progress_min", "required_progress_min", "viewers"])
    ENUMS = frozenset(["ACTIVE", "UPCOMING", "EXPIRED", "BADGE", "EMOTE", "DIRECT_ENTITLEMENT", "UNKNOWN", "SUCCESS", "ERROR", "CLAIMED", "ELIGIBLE", "INELIGIBLE", "PONG", "RESPONSE", "RECONNECT", "MESSAGE", "ready", "waiting", "expired", "rejected", "logged_out", "verifying", "starting", "cleaning", "idle", "disconnected", "connected", "retrying", "failed", "stopped", "unavailable", "stream-up", "stream-down", "viewcount", "broadcast-settings-update", "drop-progress", "drop-claim"])
    OPERATIONS = frozenset(operation["operationName"] for operation in GQL_OPERATIONS.values()) | {
        "ChannelBanStatus", "SendSpadeEvents", "CurrentUser", "raw_query",
    }
    SECRET = re.compile(r"auth|cookie|token|password|integrity|signature|secret|device|sessionid|"
                        r"clientid|ticket|claimid|dropinstance|email|phone|address|nonce|sdk|verification", re.I)
    REF = re.compile(r"^(?:ref|field)-[0-9a-f]{24}$")

    def __init__(self, data_dir: Path):
        self.data_dir = data_dir
        self._key = secrets.token_bytes(32)
        self.apis: OrderedDict[str, dict[str, Any]] = OrderedDict()
        self.logs: deque[dict[str, Any]] = deque(maxlen=100)
        self._log_handler = DiagnosticLogHandler(self)
        self._saving = asyncio.Lock()
        self._last_save: float | None = None

    def start_logs(self) -> None:
        logging.getLogger("TwitchDrops").addHandler(self._log_handler)

    def stop_logs(self) -> None:
        logging.getLogger("TwitchDrops").removeHandler(self._log_handler)

    def clear(self) -> None:
        self.apis.clear()
        self.logs.clear()
        self._key = secrets.token_bytes(32)

    def reference(self, value: Any, *, field: bool = False) -> str:
        digest = hmac.new(self._key, str(value)[:4096].encode("utf-8", errors="replace"), hashlib.sha256)
        return ("field-" if field else "ref-") + digest.hexdigest()[:24]

    def exception_name(self, error: BaseException) -> str:
        name = type(error).__name__
        return name if name in self.EXCEPTIONS else self.reference(name)

    def sanitize(self, value: Any) -> Any:
        budget = [1500]

        def visit(item: Any, key: str = "", depth: int = 0) -> Any:
            budget[0] -= 1
            if budget[0] < 0 or depth > 10:
                return "<truncated>"
            if self.SECRET.search(re.sub(r"[^a-zA-Z]", "", key[:128])):
                return "<redacted>"
            if item is None:
                return None
            if type(item) is bool:
                return item
            if type(item) in (int, float):
                if key in self.NUMBERS and abs(item) < 1e18 and math.isfinite(item):
                    return item
                return self.reference(item)
            if isinstance(item, str):
                if key in self.DATE_FIELDS and len(item) <= 40:
                    try:
                        parsed = isoparse(item)
                        if parsed.tzinfo is not None:
                            return parsed.astimezone(timezone.utc).isoformat()
                    except (ValueError, OverflowError):
                        pass
                if key == "message" and item.startswith(("{", "[")) and len(item) <= self.MAX_BODY_BYTES:
                    try:
                        return visit(json.loads(item), key, depth + 1)
                    except (ValueError, RecursionError):
                        pass
                if key == "operationName" and item in self.OPERATIONS:
                    return item
                if item in self.ENUMS and key in {"status", "state", "type", "login_state"}:
                    return item
                if item in self.ERROR_CODES and key in {"error", "code", "renewal_error"}:
                    return item
                if key == "tag_name" and len(item) <= 32 and re.fullmatch(r"v?\d+\.\d+\.\d+", item):
                    return item
                return self.reference(item)
            if isinstance(item, dict):
                result: dict[str, Any] = {}
                for index, (name, child) in enumerate(item.items()):
                    if index >= 80 or budget[0] <= 0:
                        result["__truncated__"] = len(item) - index
                        break
                    safe_key = name if isinstance(name, str) and name in self.FIELDS else self.reference(name, field=True)
                    result[safe_key] = visit(child, name if isinstance(name, str) else "", depth + 1)
                return result
            if isinstance(item, (list, tuple)):
                items = [visit(child, key, depth + 1) for child in item[:64] if budget[0] > 0]
                if len(items) < len(item):
                    items.append({"__truncated__": len(item) - len(items)})
                return items
            return "<unsupported>"

        return visit(value)

    @staticmethod
    def endpoint(url: Any, method: str = "GET") -> str:
        try:
            address = urlsplit(str(url)[:4096])
            host = address.hostname or ""
            if host == "gql.twitch.tv":
                return "twitch_integrity" if address.path == "/integrity" else "twitch_gql"
            if host == "id.twitch.tv":
                return "twitch_oauth"
            if host == "api.telegram.org":
                return "telegram"
            if host == "api.github.com":
                return "github_version"
            if host.endswith(".ttvnw.net"):
                return "watch_segment" if method == "HEAD" else "watch_playlist"
            if host in {"spade.twitch.tv", "spade.twitch.com"}:
                return "twitch_telemetry"
            if host in {"www.twitch.tv", "twitch.tv"}:
                return "twitch_page"
        except (ValueError, TypeError):
            pass
        return "other_http"

    def record(self, endpoint: str, response: Any = None, *, status: int | None = None,
               operation: str = "", body_bytes: int | None = None, outcome: str = "response") -> None:
        """Sanitize synchronously; raw payloads are never retained by the recorder."""
        allowed = {"twitch_integrity", "twitch_gql", "twitch_oauth", "telegram", "github_version",
                   "watch_segment", "watch_playlist", "twitch_telemetry", "twitch_page",
                   "other_http", "twitch_websocket"}
        endpoint = endpoint if endpoint in allowed else "other_http"
        operation = operation if operation in self.OPERATIONS else ""
        key = endpoint + (":" + operation if operation else "")
        entry = self.apis.setdefault(key, {"endpoint": endpoint, "operation": operation, "count": 0, "samples": []})
        self.apis.move_to_end(key)
        entry["count"] += 1
        entry["samples"].append({
            "at": datetime.now(timezone.utc).isoformat(),
            "status": status if type(status) is int and 100 <= status <= 599 else None,
            "outcome": outcome if outcome in {"response", "timeout", "connection", "tls", "decode", "truncated"} else "response",
            "body_bytes": body_bytes if type(body_bytes) is int and body_bytes >= 0 else None,
            "response": self.sanitize(response),
        })
        del entry["samples"][:-2]
        while len(self.apis) > self.MAX_APIS:
            self.apis.popitem(last=False)

    def record_http(self, method: str, url: Any, status: int, *, body: bytes | None = None,
                    payload: Any = None, operations: Any = None) -> None:
        endpoint = self.endpoint(url, method)
        if body is not None:
            if len(body) > self.MAX_BODY_BYTES:
                self.record(endpoint, status=status, body_bytes=len(body), outcome="truncated")
                return
            try:
                payload = json.loads(body) if body else None
            except (ValueError, UnicodeError, RecursionError):
                # HTML, playlists, binary responses and malformed JSON: metadata only.
                payload = None
        if endpoint == "twitch_gql" and operations is not None:
            ops = operations if isinstance(operations, list) else [operations]
            rows = payload if isinstance(payload, list) else [payload]
            for index, row in enumerate(rows[:64]):
                operation = ops[index] if index < len(ops) and isinstance(ops[index], dict) else {}
                name = operation.get("operationName", "")
                if not name and isinstance(operation.get("query"), str):
                    match = re.search(r"\b(?:query|mutation)\s+(\w+)", operation["query"])
                    name = match[1] if match else "raw_query"
                self.record(endpoint, row, status=status, operation=name,
                            body_bytes=len(body) if body is not None else None)
        else:
            self.record(endpoint, payload, status=status,
                        body_bytes=len(body) if body is not None else None)

    async def capture_http(self, method: str, url: str, response: Any) -> None:
        """For otherwise unread responses, consume at most one byte over the capture limit."""
        body = bytearray()
        while len(body) <= self.MAX_BODY_BYTES:
            chunk = await response.content.read(min(65536, self.MAX_BODY_BYTES + 1 - len(body)))
            if not chunk:
                break
            body.extend(chunk)
        self.record_http(method, url, response.status, body=bytes(body))

    def snapshot(self, twitch: Twitch, browser: dict[str, Any] | None = None) -> dict[str, Any]:
        settings = twitch.settings
        watched = twitch.watching_channel.get_with_default(None)
        candidates = list(twitch.channels.values()) + twitch._channel_service.banned_channels
        if watched is not None:
            candidates.append(watched)
        channels = {channel.id: channel for channel in candidates}
        campaigns = []
        drops_remaining = 5000
        offset = datetime.now().astimezone().utcoffset()
        for campaign in twitch.inventory[:500]:
            drops = list(islice(campaign.drops, min(500, drops_remaining)))
            drops_remaining -= len(drops)
            campaigns.append({
                "id": self.reference(campaign.id), "name": self.reference(campaign.name),
                "game": self.reference(campaign.game.name), "linked": self.flag(campaign.linked),
                "active": self.flag(campaign.active), "upcoming": self.flag(campaign.upcoming), "expired": self.flag(campaign.expired),
                "starts_at": campaign.starts_at.isoformat(), "ends_at": campaign.ends_at.isoformat(),
                "allowed_channels": [self.reference(channel.id) for channel in campaign.allowed_channels[:500]],
                "allowed_channels_total": len(campaign.allowed_channels),
                "drops_total": len(campaign.timed_drops),
                "drops": [{
                    "id": self.reference(drop.id), "name": self.reference(drop.name),
                    "claimed": self.flag(drop.is_claimed), "mineable": self.flag(drop.is_mineable),
                    "required_minutes": self.number(drop.required_minutes), "reported_minutes": self.number(drop.real_current_minutes),
                    "estimated_minutes": self.number(drop.extra_current_minutes),
                    "preconditions": [self.reference(key) for key in islice(drop.precondition_drops, self.MAX_PRECONDITIONS)],
                    "preconditions_total": len(drop.precondition_drops),
                    "starts_at": drop.starts_at.isoformat(), "ends_at": drop.ends_at.isoformat(),
                } for drop in drops],
            })
        snapshot = {
            "schema_version": 1, "app_version": __version__, "created_at": datetime.now(timezone.utc).isoformat(),
            "runtime": {"python": platform.python_version(),
                        "platform": sys.platform if sys.platform in {"win32", "linux", "darwin"} else "other",
                        "docker": Path("/.dockerenv").exists(),
                        "timezone_offset_seconds": offset.total_seconds() if offset is not None else None},
            "miner": {
                "state": twitch._state.name if isinstance(twitch._state, State) else "unknown",
                "inventory_loaded": self.flag(twitch._inventory_loaded),
                "wanted_games": [self.reference(game.name) for game in twitch.wanted_games[:self.MAX_SETTINGS_ITEMS]],
                "wanted_games_total": len(twitch.wanted_games),
                "watching": self.reference(watched.id) if watched else None,
                "manual_mode": twitch._manual_target_channel is not None,
            },
            "settings": {
                "language": settings.language if settings.language in self.LOCALES else self.reference(settings.language),
                "allow_unlinked_campaigns": self.flag(settings.allow_unlinked_campaigns),
                "dark_mode": self.flag(settings.dark_mode),
                "inventory_list_view": self.flag(settings.inventory_list_view),
                "connection_quality": self.number(settings.connection_quality),
                "refresh_interval_minutes": self.number(settings.minimum_refresh_interval_minutes),
                "inventory_filters": self.sanitize(settings.inventory_filters),
                "proxy_configured": bool(settings.proxy),
                "telegram_configured": bool(settings.telegram_bot_token and settings.telegram_chat_id),
                "games_to_watch": [self.reference(name) for name in settings.games_to_watch[:self.MAX_SETTINGS_ITEMS]],
                "games_to_watch_total": len(settings.games_to_watch),
                "ignored_keywords": [self.reference(word) for word in settings.drop_name_blacklist[:self.MAX_SETTINGS_ITEMS]],
                "ignored_keywords_total": len(settings.drop_name_blacklist),
                "mining_benefits": {key: value for key, value in settings.mining_benefits.items()
                                    if key in {"BADGE", "EMOTE", "DIRECT_ENTITLEMENT", "UNKNOWN"} and type(value) is bool},
            },
            "session": self.sanitize(twitch.session_controller.status()),
            "channels": [{
                "id": self.reference(channel.id), "name": self.reference(channel.name),
                "online": self.flag(channel.online), "acl": self.flag(channel.acl_based),
                "banned": self.flag(channel.banned), "ban_permanent": self.flag(channel.ban_permanent),
                "ban_expires_at": datetime.fromtimestamp(channel.ban_expires_at, timezone.utc).isoformat()
                if channel.ban_expires_at is not None else None,
                "game": self.reference(channel.game.name) if channel.game else None,
            } for channel in list(channels.values())[:1000]],
            "channels_total": len(channels), "campaigns": campaigns, "campaigns_total": len(twitch.inventory),
            "apis": list(self.apis.values()), "log_locations": list(self.logs), "browser": browser or {},
            "limits": {"api_categories": self.MAX_APIS, "samples_per_api": 2, "response_body_bytes": self.MAX_BODY_BYTES,
                       "campaigns": 500, "drops_per_campaign": 500, "channels": 1000,
                       "total_drops": 5000,
                       "response_nodes": 1500, "response_list_items": 64,
                       "settings_items": self.MAX_SETTINGS_ITEMS, "preconditions": self.MAX_PRECONDITIONS,
                       "dump_bytes": self.MAX_DUMP_BYTES, "saved_files": self.MAX_FILES},
        }
        # Different dumps have independent references, with relationships preserved within each dump.
        export_key = secrets.token_bytes(32)

        def export(value: Any) -> Any:
            if isinstance(value, str) and self.REF.fullmatch(value):
                return value.split("-", 1)[0] + "-" + hmac.new(export_key, value.encode(), hashlib.sha256).hexdigest()[:16]
            if isinstance(value, dict):
                return {export(key): export(child) for key, child in value.items()}
            if isinstance(value, list):
                return [export(child) for child in value]
            return value

        return export(snapshot)

    @staticmethod
    def flag(value: Any) -> bool | None:
        return value if type(value) is bool else None

    @staticmethod
    def number(value: Any) -> int | float | None:
        return value if type(value) in (int, float) and abs(value) < 1e18 and math.isfinite(value) else None

    async def save(self, twitch: Twitch, browser: dict[str, Any] | None = None) -> str:
        if self._saving.locked() or self._last_save is not None and monotonic() - self._last_save < 10:
            raise BlockingIOError("diagnostics_busy")
        async with self._saving:
            snapshot = self.snapshot(twitch, browser)
            writer = asyncio.create_task(asyncio.to_thread(self._write, snapshot))
            try:
                return await asyncio.shield(writer)
            except asyncio.CancelledError:
                # A worker thread cannot be cancelled. Hold the admission lock until
                # it finishes, including repeated cancellation during server shutdown.
                while not writer.done():
                    try:
                        await asyncio.shield(writer)
                    except asyncio.CancelledError:
                        continue
                    except Exception:
                        break
                if not writer.cancelled():
                    writer.exception()
                raise
            finally:
                self._last_save = monotonic()

    def _write(self, snapshot: dict[str, Any]) -> str:
        directory = self.data_dir / "diagnostics"
        if directory.is_symlink():
            raise OSError("diagnostics_path")
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        if directory.resolve().parent != self.data_dir.resolve():
            raise OSError("diagnostics_path")
        if os.name == "posix":
            if directory.stat().st_uid != getattr(os, "geteuid", lambda: None)():
                raise OSError("diagnostics_permissions")
            directory.chmod(0o700)
            if stat.S_IMODE(directory.stat().st_mode) != 0o700:
                raise OSError("diagnostics_permissions")
        filename = "diagnosis-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + secrets.token_hex(6) + ".json"
        encoded = self._encode(snapshot)
        fd, temporary = tempfile.mkstemp(prefix=".diagnosis-", dir=directory)
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(encoded)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, directory / filename)
        finally:
            Path(temporary).unlink(missing_ok=True)
        self._prune(directory, filename)
        return "diagnostics/" + filename

    def _encode(self, snapshot: dict[str, Any]) -> bytes:
        """Keep category/status evidence even when a very large snapshot needs trimming."""
        def encode() -> bytes:
            return json.dumps(snapshot, ensure_ascii=True, indent=2, allow_nan=False).encode("ascii")

        encoded = encode()
        if len(encoded) <= self.MAX_DUMP_BYTES:
            return encoded
        truncation = snapshot.setdefault("truncation", {})
        truncation["api_samples"] = 0
        for entry in snapshot["apis"]:
            truncation["api_samples"] += max(0, len(entry["samples"]) - 1)
            del entry["samples"][:-1]
        encoded = encode()
        if len(encoded) > self.MAX_DUMP_BYTES:
            truncation["api_bodies"] = 0
            for entry in snapshot["apis"]:
                for sample in entry["samples"]:
                    sample["response"] = "<truncated:dump-size>"
                    truncation["api_bodies"] += 1
            encoded = encode()
        while len(encoded) > self.MAX_DUMP_BYTES and snapshot["campaigns"]:
            removed = max(1, len(snapshot["campaigns"]) // 2)
            del snapshot["campaigns"][-removed:]
            truncation["campaigns"] = truncation.get("campaigns", 0) + removed
            encoded = encode()
        if len(encoded) > self.MAX_DUMP_BYTES:
            raise OSError("diagnostics_size")
        return encoded

    def _prune(self, directory: Path, latest: str) -> None:
        """Retain only the ten newest files created by this feature; ignore other files."""
        files = [path for path in directory.iterdir()
                 if re.fullmatch(r"diagnosis-\d{8}T\d{6}Z-[0-9a-f]{12}\.json", path.name)
                 and not path.is_symlink() and path.is_file()]
        for path in sorted(files, key=lambda path: (path.name == latest, path.stat().st_mtime_ns),
                           reverse=True)[self.MAX_FILES:]:
            path.unlink(missing_ok=True)
