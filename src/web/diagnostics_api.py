"""Dashboard action for private, deidentified diagnostic files in the data volume."""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field, StrictBool

from src.diagnostics import Diagnostics


if TYPE_CHECKING:
    from src.core.client import Twitch


class DiagnosticBrowser(BaseModel):
    model_config = ConfigDict(extra="forbid")
    width: int | None = Field(default=None, ge=0, le=32000)
    height: int | None = Field(default=None, ge=0, le=32000)
    browser: Literal["Chrome", "Edge", "Firefox", "Safari", "Other"] = "Other"
    platform: Literal["Windows", "macOS", "Linux", "Android", "iOS", "Other"] = "Other"
    tab: Literal["main", "inventory", "history", "settings", "help"] = "settings"
    online: StrictBool | None = None
    socket_connected: StrictBool | None = None


class DiagnosticRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    browser: DiagnosticBrowser | None = None


class DiagnosticsAPI:
    def __init__(self, get_client: Callable[[], Twitch | None]):
        self.get_client = get_client
        self.router = APIRouter()
        self.router.add_api_route("/api/diagnostics", self.save, methods=["POST"])

    async def save(self, request: DiagnosticRequest):
        client = self.get_client()
        if client is None or not isinstance(getattr(client, "diagnostics", None), Diagnostics):
            raise HTTPException(503, "diagnostics_unavailable")
        try:
            filename = await client.diagnostics.save(
                client, request.browser.model_dump() if request.browser is not None else None,
            )
        except BlockingIOError:
            raise HTTPException(429, "diagnostics_busy") from None
        except (OSError, ValueError, TypeError, AttributeError):
            # Storage paths, exception details and source data never enter the web response.
            raise HTTPException(500, "diagnostics_failed") from None
        return {"success": True, "file": filename}
