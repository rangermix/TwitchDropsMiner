"""
Client-integrity tokens via Streamlink's Chromium automation.

Twitch gated `currentUser.dropCampaigns` behind a `Client-Integrity` header
around 2026-09-18. A token minted by POSTing to `/integrity` from plain HTTP is
rejected; only one produced by a real browser passes. Streamlink already solves
this for its own Twitch plugin, so we reuse `TwitchClientIntegrity.acquire`
rather than driving CDP ourselves.

Streamlink's browser automation runs on trio, which cannot share a thread with
our asyncio loop, so acquisition happens in a subprocess that prints
`TOKEN:` and `EXPIRES_MS:` lines on stdout.

Chromium must run headful: Twitch issues tokens to headless Chromium but then
rejects them with "failed integrity check". In a container that means a virtual
display, so the subprocess is wrapped in `xvfb-run` when there is no DISPLAY.

Ported from Donistr's patch for DevilXD/TwitchDropsMiner, posted in
https://github.com/DevilXD/TwitchDropsMiner/issues/1165#issuecomment-5828653655
"""

from __future__ import annotations

import asyncio
import logging
import os
import shutil
import sys
from contextlib import suppress
from datetime import datetime, timedelta, timezone


logger = logging.getLogger("TwitchDrops")

# The token is bound to the client id it was minted under, so this has to stay
# in step with ClientType.WEB.
CLIENT_ID = "kimne78kx3ncx6brgo4mv6wki5h1ko"
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/138.0.0.0 Safari/537.36"
)

# Any existing channel; the page just has to load far enough for Twitch's SDK
# to run.
INTEGRITY_CHANNEL = "twitch"

SUBPROCESS_TIMEOUT = 180
# Renew early so a request never races the expiry.
RENEW_MARGIN = timedelta(minutes=15)
# Don't hammer Chromium if acquisition is failing.
FAILURE_COOLDOWN = timedelta(minutes=5)


def chromium_path() -> str:
    return os.environ.get("TDM_CHROMIUM_PATH", "/usr/bin/chromium")


def headless() -> bool:
    # Off by default: Twitch issues tokens to headless Chromium but then rejects
    # them on the gated operations. Headful under a virtual display passes.
    return os.environ.get("TDM_INTEGRITY_HEADLESS", "0") not in ("0", "false", "False")


def _command(access_token: str, device_id: str) -> list[str]:
    # `python -m src.auth.integrity` would re-execute a module the package has
    # already imported (RuntimeWarning), so call the entry point directly.
    cmd = [
        sys.executable, "-c",
        "import sys; from src.auth.integrity import _main; "
        "sys.exit(_main(sys.argv[1], sys.argv[2]))",
        access_token, device_id,
    ]
    if not headless() and not os.environ.get("DISPLAY") and shutil.which("xvfb-run"):
        # Headful needs a display; containers don't have one.
        cmd = ["xvfb-run", "-a", *cmd]
    return cmd


async def acquire(access_token: str, device_id: str) -> tuple[str, datetime] | None:
    """
    Mint a client-integrity token. Returns (token, expiry) or None on failure.
    """
    try:
        proc = await asyncio.create_subprocess_exec(
            *_command(access_token, device_id),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except OSError as exc:
        logger.error(f"Could not start the integrity subprocess: {exc}")
        return None

    try:
        stdout, stderr = await asyncio.wait_for(
            proc.communicate(), timeout=SUBPROCESS_TIMEOUT
        )
    except asyncio.TimeoutError:
        logger.error("Integrity subprocess timed out; killing it")
        proc.kill()
        await proc.wait()
        return None

    for line in stderr.decode("utf-8", errors="replace").splitlines():
        if line.strip():
            logger.debug(f"[integrity] {line}")

    token: str | None = None
    expires_ms: int | None = None
    for line in stdout.decode("utf-8", errors="replace").splitlines():
        if line.startswith("TOKEN:"):
            token = line[len("TOKEN:"):].strip()
        elif line.startswith("EXPIRES_MS:"):
            with suppress(ValueError):
                expires_ms = int(line[len("EXPIRES_MS:"):].strip())

    if not token or expires_ms is None:
        logger.error(
            f"Integrity subprocess exited with code {proc.returncode} "
            "without returning a token"
        )
        return None

    expires_at = datetime.fromtimestamp(expires_ms / 1000, timezone.utc)
    logger.info(f"Acquired a client-integrity token, valid until {expires_at}")
    return token, expires_at


def _main(access_token: str, device_id: str) -> int:
    # Imported here so the parent process never pulls in streamlink/trio.
    from streamlink.plugins.twitch import TwitchClientIntegrity
    from streamlink.session import Streamlink  # type: ignore[attr-defined]

    session = Streamlink()
    session.set_option("webbrowser", True)
    session.set_option("webbrowser-headless", headless())
    session.set_option("webbrowser-timeout", 60)
    session.set_option("webbrowser-executable", chromium_path())

    try:
        result = TwitchClientIntegrity.acquire(
            session=session,
            channel=INTEGRITY_CHANNEL,
            headers={
                "Client-Id": CLIENT_ID,
                "Authorization": f"OAuth {access_token}",
                "User-Agent": USER_AGENT,
            },
            device_id=device_id,
        )
    except Exception as exc:  # streamlink raises a wide range of browser errors
        print(f"acquire failed: {exc!r}", file=sys.stderr)
        return 1

    if result is None:
        print("acquire returned None", file=sys.stderr)
        return 1

    token, expiration = result
    print(f"TOKEN:{token}")
    print(f"EXPIRES_MS:{int(expiration * 1000)}")
    return 0


if __name__ == "__main__":
    if len(sys.argv) < 3:
        print("usage: python -m src.auth.integrity <access_token> <device_id>",
              file=sys.stderr)
        sys.exit(2)
    sys.exit(_main(sys.argv[1], sys.argv[2]))
