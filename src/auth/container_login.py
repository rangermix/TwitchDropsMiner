"""Temporary container desktop for dashboard sign-in; credentials stay on the server."""

from __future__ import annotations

import asyncio
import os
import shutil
import socket
import tempfile
from collections.abc import Callable
from contextlib import suppress
from pathlib import Path
from typing import Any

import aiohttp

from src.auth.server_renewal import OwnedChromium
from src.auth.session_bundle import SessionError
from src.auth.session_controller import SessionController
from src.auth.session_helper import BrowserExporter, DevToolsConnection
from src.config.paths import DATA_DIR


def loopback_port() -> int:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


async def finish_cleanup(cleanup: asyncio.Task[None]) -> None:
    """Drain all owned processes and private files through repeated cancellation."""
    interrupted = False
    while not cleanup.done():
        try:
            await asyncio.shield(cleanup)
        except asyncio.CancelledError:
            interrupted = True
    cleanup.result()
    if interrupted:
        raise asyncio.CancelledError


class BrowserIsolation:
    """Run the interactive desktop as a different UID with no miner-file access."""

    def __init__(self):
        if os.name != "posix" or os.geteuid() != 0:
            raise SessionError("BROWSER_UNAVAILABLE")
        import pwd

        try:
            account = pwd.getpwnam("tdm-browser")
        except KeyError:
            raise SessionError("BROWSER_UNAVAILABLE") from None
        self.uid, self.gid = account.pw_uid, account.pw_gid
        if self.uid == 0 or self.gid == 0:
            raise SessionError("BROWSER_ISOLATION")
        self.options: dict[str, Any] = {"user": self.uid, "group": self.gid, "extra_groups": []}

    def protect(self, *directories: Path) -> None:
        try:
            for directory in directories:
                directory.mkdir(parents=True, exist_ok=True)
                directory.chmod(0o700)
                info = directory.stat()
                # Some host bind mounts ignore chmod. Never open VNC in that case.
                if info.st_mode & 0o077 or info.st_uid == self.uid:
                    raise SessionError("BROWSER_ISOLATION")
        except OSError:
            raise SessionError("BROWSER_ISOLATION") from None

    def own(self, directory: Path) -> None:
        os.chown(directory, self.uid, self.gid)

    def directory(self, prefix: str) -> Path:
        directory = Path(tempfile.mkdtemp(prefix=prefix))
        try:
            directory.chmod(0o700)
            self.own(directory)
            return directory
        except BaseException:
            shutil.rmtree(directory)
            raise

    @staticmethod
    def environment() -> dict[str, str]:
        # Do not pass miner secrets or privileged browser configuration to the viewer.
        return {name: value for name, value in os.environ.items()
                if name in ("PATH", "TZ", "LANG", "LC_ALL") or name.startswith("LC_")}


class ContainerDesktop:
    """Own Xvfb, its window manager and a loopback-only VNC listener."""

    def __init__(self):
        self.processes: list[asyncio.subprocess.Process] = []
        self.environment: dict[str, str] = {}
        self.port = loopback_port()
        self.root: Path | None = None
        self._cleanup: asyncio.Task[None] | None = None
        self.isolation: BrowserIsolation | None = None

    async def launch(self, program: str, *args: str, **options: Any) -> asyncio.subprocess.Process:
        process = await asyncio.create_subprocess_exec(
            program, *args, env=self.environment or None, start_new_session=True,
            stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL,
            **(self.isolation.options if self.isolation else {}), **options,
        )
        self.processes.append(process)
        return process

    async def __aenter__(self):
        if os.name != "posix" or any(not shutil.which(name) for name in ("Xvfb", "openbox", "x11vnc", "xdotool")):
            raise SessionError("BROWSER_UNAVAILABLE")
        self.isolation = BrowserIsolation()
        self.isolation.protect(DATA_DIR, Path("logs"))
        self.root = self.isolation.directory("tdm-display-")
        self.environment = {**self.isolation.environment(),
                            "XDG_CONFIG_HOME": str(self.root / "config"),
                            "XDG_CACHE_HOME": str(self.root / "cache"),
                            **{name: str(self.root) for name in ("TMPDIR", "TMP", "TEMP")}}
        try:
            # Xvfb picks an unused display; no fixed display lock survives a restart.
            read_fd, write_fd = os.pipe()
            try:
                xvfb = await self.launch("Xvfb", "-displayfd", str(write_fd), "-screen", "0", "1280x800x24",
                                         "-nolisten", "tcp", pass_fds=(write_fd,))
                os.close(write_fd)
                write_fd = -1
                os.set_blocking(read_fd, False)
                async with asyncio.timeout(15):  # type: ignore[attr-defined]
                    display = b""
                    while b"\n" not in display:
                        if xvfb.returncode is not None:
                            raise SessionError("BROWSER_START")
                        with suppress(BlockingIOError):
                            display += os.read(read_fd, 32)
                        await asyncio.sleep(.05)
                if not display.strip().isdigit():
                    raise SessionError("BROWSER_START")
                self.environment["DISPLAY"] = ":" + display.strip().decode("ascii")
            finally:
                os.close(read_fd)
                if write_fd != -1:
                    os.close(write_fd)
            await self.launch("openbox")
            vnc = await self.launch("x11vnc", "-display", self.environment["DISPLAY"], "-localhost",
                                    "-rfbport", str(self.port), "-forever", "-shared", "-nopw", "-quiet")
            async with asyncio.timeout(15):  # type: ignore[attr-defined]
                while True:
                    if vnc.returncode is not None:
                        raise SessionError("BROWSER_START")
                    try:
                        reader, writer = await asyncio.open_connection("127.0.0.1", self.port)
                        try:
                            greeting = await asyncio.wait_for(reader.readexactly(12), 2)
                        finally:
                            writer.close()
                            await writer.wait_closed()
                        if greeting != b"RFB 003.008\n":
                            raise SessionError("BROWSER_START")
                        break
                    except OSError:
                        await asyncio.sleep(.1)
            return self
        except BaseException:
            await self.close()
            raise

    async def __aexit__(self, *_args):
        await self.close()

    async def close(self) -> None:
        if self._cleanup is None:
            self._cleanup = asyncio.create_task(self._close())
        await finish_cleanup(self._cleanup)

    async def _close(self) -> None:
        failures = []
        for process in reversed(self.processes):
            try:
                await OwnedChromium.stop(process)
            except Exception as error:
                failures.append(error)
        self.processes.clear()
        if self.root is not None:
            await asyncio.to_thread(shutil.rmtree, self.root)
            self.root = None
        if failures:
            raise SessionError("BROWSER_STOP")


class LoginChromium:
    """Ordinary login, normal close, then owned CDP capture from the flushed profile."""

    def __init__(self, environment: dict[str, str]):
        self.environment = environment
        self.executable = shutil.which("chromium") or shutil.which("chromium-browser")
        self.process: asyncio.subprocess.Process | None = None
        self.profile: Path | None = None
        self.address = ""
        self._cleanup: asyncio.Task[None] | None = None
        self.isolation: BrowserIsolation | None = None

    async def launch(self, *, capture: bool = False) -> None:
        assert self.profile is not None and self.executable is not None
        args = [self.executable, f"--user-data-dir={self.profile}", "--no-first-run",
                "--no-default-browser-check", "--disable-background-mode", "--disable-sync",
                "--disable-dev-shm-usage", "--window-size=1200,760"]
        if os.geteuid() == 0:
            args.insert(1, "--no-sandbox")
        if capture:
            port = loopback_port()
            self.address = f"http://127.0.0.1:{port}"
            args.extend(["--remote-debugging-address=127.0.0.1", f"--remote-debugging-port={port}", "about:blank"])
        else:
            args.append("https://www.twitch.tv/login")
        self.process = await asyncio.create_subprocess_exec(
            *args, env={**self.environment,
                        "XDG_CONFIG_HOME": str(self.profile / "config"),
                        "XDG_CACHE_HOME": str(self.profile / "cache"),
                        **{name: str(self.profile / "tmp") for name in ("TMPDIR", "TMP", "TEMP")}},
            stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL, start_new_session=True,
            **(self.isolation.options if self.isolation else {}),
        )
        if capture:
            await self.check_ready()
        else:
            await asyncio.sleep(.1)
            if self.process.returncode is not None:
                raise SessionError("BROWSER_START")

    async def __aenter__(self):
        if not self.executable:
            raise SessionError("BROWSER_UNAVAILABLE")
        self.isolation = BrowserIsolation()
        self.profile = self.isolation.directory("tdm-login-")
        try:
            (self.profile / "tmp").mkdir(mode=0o700)
            self.isolation.own(self.profile / "tmp")
            await self.launch()
            return self
        except BaseException:
            await self.close()
            raise

    async def __aexit__(self, *_args):
        await self.close()

    def require_owner(self, data: Any) -> None:
        if not isinstance(data, dict) or not isinstance(data.get("processInfo"), list) or self.process is None or not any(
            isinstance(row, dict) and row.get("type") == "browser" and row.get("id") == self.process.pid
            for row in data["processInfo"]
        ):
            raise SessionError("BROWSER_OWNER")

    async def socket_url(self, http: aiohttp.ClientSession):
        async with http.get(self.address + "/json/version", allow_redirects=False) as response:
            if response.status != 200:
                raise SessionError("BROWSER_START")
            data = await response.json()
            return BrowserExporter(self.address).endpoint(data["webSocketDebuggerUrl"])

    async def check_ready(self) -> None:
        async with asyncio.timeout(20), aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=5)) as http:  # type: ignore[attr-defined]
            while self.process is not None and self.process.returncode is None:
                try:
                    endpoint = await self.socket_url(http)
                    async with http.ws_connect(endpoint) as socket:
                        protocol = DevToolsConnection(socket)
                        try:
                            self.require_owner(await protocol.command("SystemInfo.getProcessInfo", timeout=2))
                            return
                        finally:
                            await protocol.close()
                except (aiohttp.ClientError, OSError, KeyError, ValueError, TimeoutError):
                    await asyncio.sleep(.1)
        raise SessionError("BROWSER_START")

    async def finish(self) -> None:
        assert self.process is not None
        output = await self.window_command("search", "--onlyvisible", "--pid", str(self.process.pid))
        windows = output.decode("ascii").splitlines()
        if not windows or any(not window.isdecimal() for window in windows):
            raise SessionError("BROWSER_CLOSE")
        for window in windows:
            # EWMH close asks the WM to perform the ordinary user close, flushing storage.
            owner = await self.window_command("getwindowpid", window)
            if owner.strip() != str(self.process.pid).encode("ascii"):
                raise SessionError("BROWSER_CLOSE")
            await self.window_command("windowquit", window)

    async def window_command(self, *args: str) -> bytes:
        process = await asyncio.create_subprocess_exec("xdotool", *args, env=self.environment,
                    stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL, start_new_session=True,
                    **(self.isolation.options if self.isolation else {}))
        try:
            output, _ = await asyncio.wait_for(process.communicate(), 5)
            if process.returncode != 0:
                raise SessionError("BROWSER_CLOSE")
            return output
        finally:
            if process.returncode is None:
                await OwnedChromium.finish_stop(process)

    async def capture(self):
        assert self.process is not None
        if await self.process.wait() != 0:
            raise SessionError("BROWSER_START")
        await self.launch(capture=True)
        async with (aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=30)) as http,
                    http.ws_connect(await self.socket_url(http)) as socket):
            protocol = DevToolsConnection(socket)
            try:
                self.require_owner(await protocol.command("SystemInfo.getProcessInfo"))
                cookies = await protocol.command("Storage.getCookies")
                if not any(cookie.get("name") == "auth-token" and cookie.get("value")
                           and cookie.get("domain") in (".twitch.tv", "twitch.tv", "www.twitch.tv")
                           for cookie in cookies.get("cookies", [])):
                    raise SessionError("LOGIN_MISSING")
            finally:
                await protocol.close()
        return await BrowserExporter(self.address, timeout=120).capture_seed()

    async def close(self) -> None:
        if self._cleanup is None:
            self._cleanup = asyncio.create_task(self._close())
        await finish_cleanup(self._cleanup)

    async def _close(self) -> None:
        if self.process is not None:
            await OwnedChromium.stop(self.process)
            self.process = None
        if self.profile is not None:
            await asyncio.to_thread(shutil.rmtree, self.profile)
            self.profile = None


class ContainerLogin:
    """One shared temporary login attempt, automatically replaced by verified mining."""

    def __init__(self, controller: SessionController, *, on_change: Callable[[], None] = lambda: None,
                 desktop_factory: Callable = ContainerDesktop, browser_factory: Callable = LoginChromium):
        self.controller, self.on_change = controller, on_change
        self.desktop_factory, self.browser_factory = desktop_factory, browser_factory
        self.state, self.attempt = "idle", 0
        self.error: str | None = None
        self.desktop: ContainerDesktop | None = None
        self.browser: LoginChromium | None = None
        self._task: asyncio.Task | None = None
        self._stopped = False
        self._needed = False
        self._finish_lock = asyncio.Lock()

    def status(self) -> dict[str, Any]:
        return {"state": self.state, "error": self.error, "attempt": self.attempt}

    def change(self, state: str, error: str | None = None) -> None:
        self.state, self.error = state, error
        self.on_change()

    def request_login(self) -> None:
        self._needed = True
        if not self._stopped and self._task is None and self.state == "idle":
            self.attempt += 1
            self.change("starting")
            self._task = asyncio.create_task(self._run(self.attempt))

    def session_changed(self) -> None:
        if self.controller.session.status()["state"] == "ready" and self.state == "sign_in":
            self._needed = False
            if self._task is not None:
                self._task.cancel()

    async def _run(self, attempt: int) -> None:
        error = None
        try:
            async with self.desktop_factory() as desktop:
                self.desktop = desktop
                async with self.browser_factory(desktop.environment) as browser:
                    self.browser = browser
                    self.change("sign_in")
                    assert browser.process is not None
                    await asyncio.wait_for(browser.process.wait(), 900)
                    self.change("verifying")
                    seed = await browser.capture()
                    await self.controller.accept(seed, authorized=lambda: attempt == self.attempt and not self._stopped)
                    self._needed = False
        except asyncio.CancelledError:
            pass
        except SessionError as failure:
            error = failure.code
        except TimeoutError:
            error = "LOGIN_TIMEOUT"
        except Exception:
            # Process/cleanup diagnostics must never expose a credential or exception text.
            error = "BROWSER_FAILED"
        finally:
            self.browser = self.desktop = None
            self._task = None
            self.change("error" if error else "idle", error)

    async def finish(self) -> dict[str, Any]:
        async with self._finish_lock:
            if self.state != "sign_in" or self.browser is None:
                raise SessionError("BROWSER_STATE")
            await self.browser.finish()
            return self.status()

    async def retry(self) -> dict[str, Any]:
        if not self._needed or self.state not in ("idle", "error"):
            raise SessionError("BROWSER_STATE")
        self.change("idle")
        self.request_login()
        return self.status()

    async def cancel(self) -> None:
        self.attempt += 1
        self._needed = False
        if self._task is not None:
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)
        self.change("idle")

    async def stop(self) -> None:
        self._stopped = True
        await self.cancel()
