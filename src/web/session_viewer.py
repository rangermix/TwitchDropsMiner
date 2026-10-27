"""Serve the bundled noVNC UI with a fixed dashboard-only connection policy."""

import re
from pathlib import Path

from fastapi import HTTPException
from fastapi.responses import HTMLResponse


class SessionViewer:
    ASSET_PATH = "/static/novnc-login-v2"

    def __init__(self, directory: Path):
        self.directory = directory

    def page(self) -> HTMLResponse:
        try:
            with (self.directory / "vnc.html").open("r", encoding="utf-8") as source:
                template = source.read(262145)
        except OSError:
            raise HTTPException(503, "session_viewer_unavailable") from None
        if len(template) > 262144 or template.count('<script type="module">') != 1:
            raise HTTPException(503, "session_viewer_unavailable")
        template = template.replace("<head>", f'<head><base href="{self.ASSET_PATH}/">', 1)
        template = re.sub(r'<script type="module">.*?</script>',
                          '<script type="module" src="/static/session-viewer.js?v=2"></script>',
                          template, count=1, flags=re.DOTALL)
        return HTMLResponse(template, headers={"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"})
