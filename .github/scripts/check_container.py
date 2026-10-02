"""Offline runtime smoke check: docker run --rm -i IMAGE python - < this_file."""

import asyncio
import importlib.util
import os
import pwd
import re
import shutil
import sys
from pathlib import Path

from src.auth.container_login import ContainerDesktop, LoginChromium


async def check_desktop():
    desktop = ContainerDesktop()
    async with desktop:
        reader, writer = await asyncio.open_connection("127.0.0.1", desktop.port)
        try:
            greeting = await asyncio.wait_for(reader.readexactly(12), timeout=5)
            assert greeting.startswith(b"RFB "), greeting
        finally:
            writer.close()
            await writer.wait_closed()
        browser = LoginChromium(desktop.environment)
        browser.isolation = desktop.isolation
        browser.profile = browser.isolation.directory("tdm-smoke-")
        profile = browser.profile
        try:
            (profile / "tmp").mkdir(mode=0o700)
            browser.isolation.own(profile / "tmp")
            # The capture path opens about:blank and checks the real browser PID via CDP.
            await browser.launch(capture=True)
            process = browser.process
            assert process is not None
            status = Path(f"/proc/{process.pid}/status").read_text()
            assert re.search(r"^Uid:\s+10001\s+10001\s+10001\s+10001$", status, re.M)
            denied = await asyncio.create_subprocess_exec(
                sys.executable, "-c", "import os,sys; sys.exit(os.access('/app/data', os.R_OK))",
                **browser.isolation.options,
            )
            assert await denied.wait() == 0, "browser user can read private miner data"
        finally:
            await browser.close()
        assert not profile.exists(), "browser profile was not cleaned"
        assert process.returncode is not None
        root = desktop.root
        processes = tuple(desktop.processes)
    assert root is not None and not root.exists(), "desktop directory was not cleaned"
    assert all(process.returncode is not None for process in processes)


def check_assets():
    root = Path("/usr/share/novnc")
    assert (root / "core/rfb.js").is_file()
    copyright_text = (root / "copyright").read_text()
    assert "vendor/pako/*" in copyright_text and "License: Zlib" in copyright_text
    assert Path("/usr/share/common-licenses/MPL-2.0").is_file()
    # Catch omitted modules even when the entry point itself exists.
    for script in root.rglob("*.js"):
        for dependency in re.findall(r"(?:from\s*|import\s*)['\"]([^'\"]+)['\"]", script.read_text()):
            if dependency.startswith("."):
                target = (script.parent / dependency).resolve()
                assert target.is_relative_to(root) and target.is_file(), (script, dependency)


if __name__ == "__main__":
    assert os.geteuid() == 0, "run with the image's default root user"
    assert sys.version_info >= (3, 12)
    account = pwd.getpwnam("tdm-browser")
    assert (account.pw_uid, account.pw_gid) == (10001, 10001)
    for name in ("chromium", "Xvfb", "openbox", "x11vnc", "xdotool"):
        assert shutil.which(name), f"missing executable: {name}"
    for name in ("websockify", "redis"):
        assert importlib.util.find_spec(name) is None, f"unused server dependency: {name}"
    check_assets()
    asyncio.run(check_desktop())
    print("Container assets, browser isolation, VNC startup and cleanup passed.")
