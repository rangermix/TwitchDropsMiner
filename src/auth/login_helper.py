"""One-time native Chrome login, sent directly to the selected TDM instance."""

from __future__ import annotations

import argparse
import asyncio
import ctypes
import json
import math
import os
import re
import shutil
import signal
import socket
import ssl
import subprocess
import sys
import tempfile
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager, suppress
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import aiohttp
import truststore
from yarl import URL

from src.auth.server_seed import ServerSeed
from src.auth.session_bundle import SessionBundle, SessionError
from src.auth.session_helper import BrowserExporter, DevToolsConnection
from src.i18n import _


class HelperDestination:
    """One explicitly selected origin; server replies may never change it."""

    def __init__(self, value: str):
        try:
            if not isinstance(value, str) or any(c.isspace() or ord(c) < 32 for c in value):
                raise ValueError
            if any(c in value for c in ("?", "#", "\\")):
                raise ValueError
            url = URL(value)
            if (url.scheme not in ("http", "https") or not url.host or url.user is not None
                    or url.password is not None or url.raw_path != "/" or not url.port):
                raise ValueError
            self.url = str(url).rstrip("/")
        except (TypeError, ValueError, UnicodeError):
            raise SessionError("HELPER_DESTINATION") from None


@dataclass(frozen=True)
class HelperTicket:
    connection: str = field(repr=False)
    expires_at: float


class HelperHTTP:
    """Bounded direct protocol, without redirects, proxies or dashboard cookies."""

    def __init__(self, destination: HelperDestination, *, clock: Callable[[], float] = time.time,
                 receipt_attempts: int = 20, receipt_interval: float = 3):
        self.destination, self.clock = destination, clock
        self.receipt_attempts, self.receipt_interval = receipt_attempts, receipt_interval
        self.http: aiohttp.ClientSession

    async def __aenter__(self) -> HelperHTTP:
        self.http = aiohttp.ClientSession(cookie_jar=aiohttp.DummyCookieJar(), trust_env=False,
            timeout=aiohttp.ClientTimeout(total=300, connect=15),
            connector=aiohttp.TCPConnector(ssl=truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)),
            headers={"X-TDM-Request": "1", "Accept": "application/json"})
        return self

    async def __aexit__(self, *_args: Any) -> None:
        await self.http.close()

    async def _request(self, method: str, path: str, *, ticket: HelperTicket | None = None,
                       payload: dict[str, Any] | None = None) -> dict[str, Any]:
        headers = {"Authorization": f"Bearer {ticket.connection}"} if ticket else {}
        try:
            async with self.http.request(method, self.destination.url + path, json=payload,
                    headers=headers, allow_redirects=False,
                    timeout=aiohttp.ClientTimeout(total=300 if path.endswith("/session") else 10, connect=10)) as response:
                if 300 <= response.status < 400:
                    raise SessionError("HELPER_REDIRECT")
                if response.status >= 500:
                    # A gateway can lose the reply after TDM has committed.
                    raise SessionError("HELPER_NETWORK")
                raw = bytearray()
                async for chunk in response.content.iter_chunked(8192):
                    raw.extend(chunk)
                    if len(raw) > SessionBundle.MAX_BYTES:
                        raise SessionError("HELPER_RESPONSE")
                try:
                    data = json.loads(raw)
                except (ValueError, UnicodeError, RecursionError):
                    if 400 <= response.status < 500:
                        raise SessionError("HELPER_REJECTED") from None
                    raise SessionError("HELPER_RESPONSE") from None
                if response.status != 200:
                    detail = data.get("detail") if isinstance(data, dict) else None
                    if response.status == 403 and detail == "session_helper_disabled":
                        raise SessionError("HELPER_DISABLED")
                    if response.status in (401, 403) and detail in (
                            "session_helper_expired", "session_helper_connection_invalid"):
                        raise SessionError("HELPER_EXPIRED")
                    raise SessionError("HELPER_REJECTED")
                if not isinstance(data, dict):
                    raise SessionError("HELPER_RESPONSE")
                return data
        except (aiohttp.ClientError, TimeoutError, OSError):
            raise SessionError("HELPER_NETWORK") from None

    async def connect(self) -> HelperTicket:
        data = await self._request("POST", "/api/helper/connect", payload={})
        token, expiry = data.get("connection"), data.get("expires_at")
        if (type(data.get("version")) is not int or data["version"] != 1
                or not isinstance(token, str) or not re.fullmatch(r"[A-Za-z0-9_-]{43}", token)
                or not isinstance(expiry, (int, float)) or isinstance(expiry, bool) or not math.isfinite(expiry)
                or not self.clock() < expiry <= self.clock() + 660):
            raise SessionError("HELPER_RESPONSE")
        return HelperTicket(token, float(expiry))

    def accepted(self, data: dict[str, Any]) -> int:
        state = data.get("session")
        if data.get("success") is not True or data.get("allow_helper_connection") is not False or not isinstance(state, dict):
            raise SessionError("HELPER_RESPONSE")
        if state.get("state") != "ready" or any(type(state.get(k)) is not int or state[k] <= 0 for k in ("user_id", "generation")):
            raise SessionError("HELPER_RESPONSE")
        expiry = state.get("expires_at")
        if not isinstance(expiry, (int, float)) or isinstance(expiry, bool) or not math.isfinite(expiry) or expiry <= self.clock():
            raise SessionError("HELPER_RESPONSE")
        return state["user_id"]

    async def receipt(self, ticket: HelperTicket) -> int:
        """A lost POST acknowledgement must not cause a second credential upload."""
        for attempt in range(self.receipt_attempts):
            if attempt:
                await asyncio.sleep(self.receipt_interval)
            try:
                data = await self._request("GET", "/api/helper/result", ticket=ticket)
                if data == {"state": "pending"}:
                    continue
                return self.accepted(data)
            except SessionError:
                continue
        raise SessionError("HELPER_RESULT_UNKNOWN")

    async def send(self, ticket: HelperTicket, seed: ServerSeed) -> int:
        if ticket.expires_at <= self.clock():
            raise SessionError("HELPER_EXPIRED")
        seed.bundle.require_fresh(self.clock())
        seed.cookie.require_fresh(self.clock())
        try:
            data = await self._request("POST", "/api/helper/session", ticket=ticket, payload=seed.to_dict())
            return self.accepted(data)
        except SessionError as error:
            if error.code not in ("HELPER_NETWORK", "HELPER_RESPONSE"):
                raise
            return await self.receipt(ticket)


class NativeChrome:
    """Own only a temporary TDM profile and the Chrome process launched for it."""

    LOGIN_URL = "https://www.twitch.tv/login"

    def __init__(self, executable: Path | None = None, *, startup_timeout: float = 30):
        self.executable = executable
        self.startup_timeout = startup_timeout
        self.profile: Path | None = None
        self.process: subprocess.Popen | None = None
        self.address = ""
        self._closing: asyncio.Task[None] | None = None

    @staticmethod
    def find_chrome() -> Path:
        candidates: list[Path] = []
        if sys.platform == "darwin":
            candidates = [Path(root) / "Google Chrome.app/Contents/MacOS/Google Chrome"
                          for root in ("/Applications", str(Path.home() / "Applications"))]
        elif sys.platform == "win32":
            candidates = [Path(root) / "Google/Chrome/Application/chrome.exe" for name in (
                "PROGRAMFILES", "PROGRAMFILES(X86)", "LOCALAPPDATA") if (root := os.environ.get(name))]
        else:
            candidates = [Path(value) for command in ("google-chrome", "google-chrome-stable")
                          if (value := shutil.which(command))]
        for path in candidates:
            if path.is_file():
                return path
        raise SessionError("HELPER_CHROME_MISSING")

    @staticmethod
    def external_environment() -> dict[str, str]:
        environment = dict(os.environ)
        if not getattr(sys, "frozen", False):
            return environment
        for name in ("LD_LIBRARY_PATH", "LIBPATH"):
            if name + "_ORIG" in environment:
                environment[name] = environment[name + "_ORIG"]
            else:
                environment.pop(name, None)
        root = os.path.normcase(str(getattr(sys, "_MEIPASS", "")).rstrip("/\\"))
        if root:
            for name in ("PATH", "DYLD_LIBRARY_PATH"):
                if name in environment:
                    environment[name] = os.pathsep.join(part for part in environment[name].split(os.pathsep)
                        if not (os.path.normcase(part) == root or os.path.normcase(part).startswith(root + os.sep)))
        return environment

    @staticmethod
    @contextmanager
    def external_libraries() -> Iterator[dict[str, str]]:
        # PyInstaller adjusts shared-library lookup for its own bundled libraries.
        # Chrome and taskkill must inherit the ordinary operating-system lookup.
        reset = None
        if sys.platform == "win32" and getattr(sys, "frozen", False):
            reset = ctypes.windll.kernel32.SetDllDirectoryW
            reset.argtypes = [ctypes.c_wchar_p]
            reset.restype = ctypes.c_int
            if not reset(None):
                raise SessionError("HELPER_BROWSER")
        try:
            yield NativeChrome.external_environment()
        finally:
            if reset is not None:
                reset(str(sys._MEIPASS))

    @staticmethod
    def available_port() -> int:
        # Port zero makes Chrome advertise navigator.webdriver=true. Select the
        # number ourselves and verify browser ownership after Chrome binds it.
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            return int(listener.getsockname()[1])

    def _require_owner(self, result: Any) -> None:
        processes = result.get("processInfo") if isinstance(result, dict) else None
        if self.process is None or not isinstance(processes, list) or not any(
                isinstance(process, dict) and process.get("type") == "browser"
                and type(process.get("id")) is int and process["id"] == self.process.pid
                for process in processes):
            raise SessionError("HELPER_BROWSER_OWNER")

    async def _wait_ready(self) -> None:
        async with asyncio.timeout(self.startup_timeout):  # type: ignore[attr-defined]
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=2), trust_env=False) as http:
                while self.process is not None and self.process.poll() is None:
                    try:
                        endpoint = await self._browser_socket(http)
                        async with http.ws_connect(endpoint, timeout=aiohttp.ClientWSTimeout(ws_close=1)) as socket:
                            protocol = DevToolsConnection(socket)
                            try:
                                result = await protocol.command("SystemInfo.getProcessInfo", timeout=2)
                                self._require_owner(result)
                                return
                            finally:
                                await protocol.close()
                    except SessionError as error:
                        if error.code not in ("HELPER_BROWSER", "BROWSER_PROTOCOL"):
                            raise
                    except (aiohttp.ClientError, TimeoutError, OSError):
                        pass
                    await asyncio.sleep(.1)
        raise SessionError("HELPER_BROWSER")

    async def __aenter__(self) -> NativeChrome:
        executable = self.executable or self.find_chrome()
        if not executable.is_file():
            raise SessionError("HELPER_CHROME_MISSING")
        self.profile = Path(tempfile.mkdtemp(prefix="tdm-login-"))
        try:
            self.profile.chmod(0o700)
            options: dict[str, Any] = {"stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL}
            if sys.platform == "win32":
                options["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
            else:
                options["start_new_session"] = True
            port = self.available_port()
            self.address = f"http://127.0.0.1:{port}"
            with self.external_libraries() as environment:
                self.process = subprocess.Popen([str(executable), f"--user-data-dir={self.profile}",
                    "--remote-debugging-address=127.0.0.1", f"--remote-debugging-port={port}",
                    "--no-first-run", "--no-default-browser-check", "--disable-background-mode",
                    "--disable-sync", self.LOGIN_URL], env=environment, **options)
            await self._wait_ready()
            return self
        except BaseException as error:
            await self.close()
            if isinstance(error, (OSError, TimeoutError, ValueError)):
                raise SessionError("HELPER_BROWSER") from None
            raise

    async def __aexit__(self, *_args: Any) -> None:
        await self.close()

    async def _browser_socket(self, http: aiohttp.ClientSession) -> URL:
        try:
            async with http.get(self.address + "/json/version", allow_redirects=False) as response:
                if response.status != 200:
                    raise SessionError("HELPER_BROWSER")
                data = await response.json()
                return BrowserExporter(self.address).endpoint(data["webSocketDebuggerUrl"])
        except (aiohttp.ClientError, ValueError, TypeError, KeyError, TimeoutError):
            raise SessionError("HELPER_BROWSER") from None

    async def wait_authenticated(self, *, timeout: float) -> None:
        try:
            async with asyncio.timeout(timeout):  # type: ignore[attr-defined]
                async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=5), trust_env=False) as http:
                    endpoint = await self._browser_socket(http)
                    async with http.ws_connect(endpoint, timeout=aiohttp.ClientWSTimeout(ws_close=2)) as socket:
                        protocol = DevToolsConnection(socket)
                        try:
                            while True:
                                if self.process is None or self.process.poll() is not None:
                                    raise SessionError("HELPER_BROWSER")
                                data = await protocol.command("Storage.getCookies", timeout=5)
                                if not isinstance(data, dict) or not isinstance(data.get("cookies"), list):
                                    raise SessionError("HELPER_BROWSER")
                                if any(isinstance(cookie, dict) and cookie.get("name") == "auth-token"
                                       and cookie.get("domain") in (".twitch.tv", "twitch.tv", "www.twitch.tv")
                                       and isinstance(cookie.get("value"), str) and cookie["value"]
                                       for cookie in data["cookies"]):
                                    return
                                await asyncio.sleep(1)
                        finally:
                            await protocol.close()
        except TimeoutError:
            raise SessionError("HELPER_LOGIN_TIMEOUT") from None
        except (aiohttp.ClientError, OSError, ValueError):
            raise SessionError("HELPER_BROWSER") from None

    async def _request_close(self) -> None:
        if not self.address:
            return
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=2), trust_env=False) as http:
            endpoint = await self._browser_socket(http)
            async with http.ws_connect(endpoint, timeout=aiohttp.ClientWSTimeout(ws_close=1)) as socket:
                protocol = DevToolsConnection(socket)
                try:
                    self._require_owner(await protocol.command("SystemInfo.getProcessInfo", timeout=2))
                    await socket.send_json({"id": 2, "method": "Browser.close"})
                finally:
                    await protocol.close()

    def _kill_owned_process(self) -> None:
        assert self.process is not None
        if sys.platform == "win32":
            executable = Path(os.environ.get("SYSTEMROOT", r"C:\Windows")) / "System32/taskkill.exe"
            with self.external_libraries() as environment:
                subprocess.run([str(executable), "/PID", str(self.process.pid), "/T", "/F"],
                    env=environment, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False, timeout=10)
        else:
            with suppress(ProcessLookupError):
                os.killpg(self.process.pid, signal.SIGKILL)

    async def close(self) -> None:
        """Finish bounded owned-resource cleanup even if cancellation arrives here."""
        if self._closing is None:
            self._closing = asyncio.create_task(self._close())
        interrupted = False
        while not self._closing.done():
            try:
                await asyncio.shield(self._closing)
            except asyncio.CancelledError:
                interrupted = True
        self._closing.result()
        if interrupted:
            raise asyncio.CancelledError

    async def _close(self) -> None:
        failed = False
        if self.process is not None:
            try:
                with suppress(SessionError, aiohttp.ClientError, TimeoutError, OSError):
                    await asyncio.wait_for(self._request_close(), timeout=5)
                try:
                    await asyncio.to_thread(self.process.wait, timeout=5)
                except subprocess.TimeoutExpired:
                    await asyncio.to_thread(self._kill_owned_process)
                    await asyncio.to_thread(self.process.wait, timeout=5)
            except (OSError, subprocess.TimeoutExpired):
                failed = True
            finally:
                self.process = None
        await self._remove_profile()
        if failed:
            raise SessionError("HELPER_BROWSER_CLEANUP")

    async def _remove_profile(self) -> None:
        if self.profile is not None:
            profile = self.profile
            for attempt in range(4):
                try:
                    shutil.rmtree(profile)
                    self.profile = None
                    break
                except FileNotFoundError:
                    self.profile = None
                    break
                except OSError:
                    if attempt == 3:
                        raise SessionError("HELPER_PROFILE_CLEANUP") from None
                    await asyncio.sleep(.25)


class NativeLoginHelper:
    """Admission precedes Chrome; success follows server proof and local cleanup."""

    def __init__(self, destination: str, *, browser_factory: Callable[..., Any] = NativeChrome,
                 exporter_factory: Callable[..., Any] = BrowserExporter,
                 clock: Callable[[], float] = time.time, report: Callable[[str], None] = lambda _key: None):
        self.destination = HelperDestination(destination)
        self.browser_factory, self.exporter_factory = browser_factory, exporter_factory
        self.clock, self.report = clock, report

    async def run(self) -> None:
        self.report("connecting")
        async with HelperHTTP(self.destination, clock=self.clock) as client:
            ticket = await client.connect()
            async with self.browser_factory() as browser:
                self.report("login")
                await browser.wait_authenticated(timeout=max(1, ticket.expires_at - self.clock() - 15))
                self.report("capturing")
                exporter = self.exporter_factory(browser.address, timeout=min(120, max(1, ticket.expires_at - self.clock())))
                seed = await exporter.capture_seed()
                self.report("sending")
                await client.send(ticket, seed)
        self.report("success")


class LoginHelperCLI:
    @staticmethod
    async def run_cancellable(helper: NativeLoginHelper) -> None:
        """Translate ordinary POSIX termination into owned-resource cleanup."""
        loop, task = asyncio.get_running_loop(), asyncio.current_task()
        assert task is not None
        handlers = {}
        if sys.platform != "win32":
            for sig in (signal.SIGTERM, signal.SIGHUP):
                handlers[sig] = signal.getsignal(sig)
                loop.add_signal_handler(sig, task.cancel)
        try:
            await helper.run()
        finally:
            for sig, previous in handlers.items():
                loop.remove_signal_handler(sig)
                signal.signal(sig, previous)

    @staticmethod
    def main() -> None:
        for stream in (sys.stdout, sys.stderr):
            if hasattr(stream, "reconfigure"):
                stream.reconfigure(encoding="utf-8", errors="replace")
        # Parse the language first so the ordinary argument help is translated too.
        language_parser = argparse.ArgumentParser(add_help=False)
        language_parser.add_argument("--language", choices=_.get_languages())
        language, _remaining = language_parser.parse_known_args()
        if language.language:
            _.set_language(language.language)
        words = _.t["helper"]
        parser = argparse.ArgumentParser(description=words["title"])
        parser.add_argument("--tdm", help=words["tdm_help"])
        parser.add_argument("--chrome", type=Path, help=words["chrome_help"])
        parser.add_argument("--language", choices=_.get_languages(), help=words["language_help"])
        parser.add_argument("--no-pause", action="store_true", help=words["no_pause_help"])
        args = parser.parse_args()
        result = 0
        try:
            destination = args.tdm or input(words["destination_prompt"]).strip()
            helper = NativeLoginHelper(destination, browser_factory=lambda: NativeChrome(args.chrome),
                report=lambda key: print(words[key], flush=True))  # type: ignore[literal-required]
            print(words["destination"].format(url=helper.destination.url), flush=True)
            asyncio.run(LoginHelperCLI.run_cancellable(helper))
        except SessionError as error:
            # Codes originate in our validators; never print response bodies or exceptions.
            print(_.t["login"]["error_code"].format(error_code=f"SESSION_{error.code}"), file=sys.stderr)
            if error.code == "HELPER_RESULT_UNKNOWN":
                print(words["result_unknown"], file=sys.stderr)
            result = 1
        except (KeyboardInterrupt, EOFError, asyncio.CancelledError):
            print(words["cancelled"], file=sys.stderr)
            result = 130
        except Exception:
            print(_.t["login"]["error_code"].format(error_code="SESSION_HELPER_FAILED"), file=sys.stderr)
            result = 1
        if result != 130 and getattr(sys, "frozen", False) and sys.stdin.isatty() and not args.no_pause:
            with suppress(KeyboardInterrupt, EOFError):
                input(words["wait_to_close"])
        raise SystemExit(result)


if __name__ == "__main__":
    LoginHelperCLI.main()
