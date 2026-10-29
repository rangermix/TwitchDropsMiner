"""One-time native browser login, sent directly to the selected TDM instance."""

from __future__ import annotations

import argparse
import asyncio
import ctypes
import ipaddress
import json
import math
import os
import re
import shutil
import signal
import socket
import ssl
import stat
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
from aiohttp.abc import AbstractResolver, ResolveResult
from yarl import URL

from src.auth.helper_errors import HelperDiagnostics
from src.auth.server_seed import ServerSeed
from src.auth.session_bundle import SessionBundle, SessionError
from src.auth.session_diagnostics import SessionDiagnostic
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
            raise SessionError("HELPER_DESTINATION", stage="helper_configuration", reason="invalid_response") from None


class PrivateResolver(AbstractResolver):
    """Limit --insecure to HTTPS hosts resolving only to private addresses, then pin them."""

    # 100.64.0.0/10 (carrier-grade NAT) is excluded because other ISP customers share
    # it; IPv4 link-local 169.254.0.0/16 is not on the approved list either.
    NETWORKS = tuple(ipaddress.ip_network(network) for network in (
        "10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "127.0.0.0/8",
        "::1/128", "fc00::/7", "fe80::/10",
    ))

    DNS_TIMEOUT = 10

    def __init__(self, destination: HelperDestination, resolver: AbstractResolver | None = None):
        url = URL(destination.url)
        self.https, self.host, self.port = url.scheme == "https", url.raw_host or "", url.port or 0
        self.resolver = resolver or aiohttp.ThreadedResolver()
        self.addresses: list[ResolveResult] | None = None

    @classmethod
    def allowed(cls, value: str) -> bool:
        try:
            address = ipaddress.ip_address(value.split("%", 1)[0])
        except ValueError:
            return False
        if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped is not None:
            address = address.ipv4_mapped
        return any(address in network for network in cls.NETWORKS)

    async def vet(self) -> None:
        if not self.https:
            raise SessionError("HELPER_INSECURE_TLS", stage="helper_configuration")
        try:
            # Vetting precedes the session, so its connect timeout does not apply here.
            results = await asyncio.wait_for(
                self.resolver.resolve(self.host, self.port, socket.AF_UNSPEC), self.DNS_TIMEOUT)
        except TimeoutError:
            raise SessionError("HELPER_NETWORK", stage="helper_connect", reason="timeout") from None
        except OSError:
            raise SessionError("HELPER_NETWORK", stage="helper_connect", reason="dns") from None
        # Every result must qualify, so a mixed answer cannot fall back to a public address.
        if not results or not all(self.allowed(result["host"]) for result in results):
            raise SessionError("HELPER_INSECURE_TLS", stage="helper_configuration")
        self.addresses = results

    async def resolve(self, host: str, port: int = 0,
                      family: socket.AddressFamily = socket.AF_INET) -> list[ResolveResult]:
        addresses = [result for result in self.addresses or ()
                     if family == socket.AF_UNSPEC or result["family"] == family]
        if host != self.host or not addresses:
            raise OSError("unvetted host")
        return addresses

    async def close(self) -> None:
        await self.resolver.close()


@dataclass(frozen=True)
class HelperTicket:
    connection: str = field(repr=False)
    expires_at: float


class HelperHTTP:
    """Bounded direct protocol, without redirects, proxies or dashboard cookies."""

    def __init__(self, destination: HelperDestination, *, clock: Callable[[], float] = time.time,
                 receipt_attempts: int = 20, receipt_interval: float = 3, insecure: bool = False,
                 ssl_context: ssl.SSLContext | None = None):
        self.destination, self.clock = destination, clock
        self.receipt_attempts, self.receipt_interval = receipt_attempts, receipt_interval
        self.insecure = insecure
        self.ssl_context = ssl_context or self.trust()
        self.resolver: PrivateResolver | None = None
        self.http: aiohttp.ClientSession

    async def __aenter__(self) -> HelperHTTP:
        if self.insecure:
            self.resolver = PrivateResolver(self.destination)
            await self.resolver.vet()
        self.http = aiohttp.ClientSession(cookie_jar=aiohttp.DummyCookieJar(), trust_env=False,
            timeout=aiohttp.ClientTimeout(total=300, connect=15),
            connector=aiohttp.TCPConnector(ssl=False if self.insecure else self.ssl_context,
                                           resolver=self.resolver),
            headers={"X-TDM-Request": "1", "Accept": "application/json"})
        return self

    async def __aexit__(self, *_args: Any) -> None:
        await self.http.close()
        if self.resolver is not None:
            # aiohttp closes only resolvers it created itself.
            await self.resolver.close()

    @staticmethod
    def trust(ca_file: Path | None = None) -> ssl.SSLContext:
        """System roots plus optional CA certificates for this helper only; verification stays on."""
        context = truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        if ca_file is not None:
            try:
                if not ca_file.is_file():
                    raise OSError
                probe = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
                probe.load_verify_locations(cafile=ca_file)
                # Windows and macOS verification only receives CA certificates, so
                # reject server-only certificates everywhere instead of ignoring them.
                roots = probe.get_ca_certs(binary_form=True)
                if not roots:
                    raise ssl.SSLError
                context.load_verify_locations(cadata=b"".join(roots))
            except (OSError, ssl.SSLError, ValueError):
                raise SessionError("HELPER_CA_FILE", stage="helper_configuration") from None
        return context

    @staticmethod
    def _server_error(path: str, status: int, data: Any) -> SessionError:
        """Trust only matching fixed protocol codes, never arbitrary remote text."""
        detail = data.get("detail") if isinstance(data, dict) else None
        known = next((code for code in HelperDiagnostics.SERVER_CODES
                      if detail == "session_" + code.lower()), None)
        # Retain the historical expired-ticket aliases without letting an
        # unexpected status turn a gateway response into a definitive failure.
        if (known is not None and status != HelperDiagnostics.expected_status(known)
                and not (known == "HELPER_EXPIRED" and status in (401, 403))):
            known = None
        diagnostic = None
        if known is not None and isinstance(data, dict) and "diagnostic" in data:
            raw = data["diagnostic"]
            if not isinstance(raw, dict) or raw.get("code") != known:
                known = None
            else:
                diagnostic = SessionDiagnostic.from_dict(raw)
        context = diagnostic or SessionDiagnostic()

        def error(code: str) -> SessionError:
            return SessionError(code, stage=context.stage, reason=context.reason,
                                operation=context.operation, http_status=status,
                                upstream_status=context.upstream_status)

        if status == 503 and known == "BROWSER_START":
            return error("HELPER_SERVER_BROWSER")
        if path == "/api/helper/result" and known in HelperDiagnostics.RECEIPT_FAILURES:
            assert known is not None
            return error(known)
        if path == "/api/helper/connect" and status >= 500 and known in HelperDiagnostics.CONNECT_CODES:
            assert known is not None
            return error(known)
        if status >= 500:
            return error("HELPER_NETWORK")
        if status == 403 and known == "HELPER_DISABLED":
            return error("HELPER_DISABLED")
        if status in (401, 403) and (known in ("HELPER_EXPIRED", "CONNECTION")
                                   or (detail == "session_helper_connection_invalid"
                                       and isinstance(data, dict) and "diagnostic" not in data)):
            return error("HELPER_EXPIRED")
        if status == 429 and known == "BUSY":
            return error("HELPER_BUSY")
        if path == "/api/helper/connect" and known in HelperDiagnostics.CONNECT_CODES:
            assert known is not None
            return error(known)
        if 400 <= status < 500 and path in ("/api/helper/session", "/api/helper/result") and known:
            return error(known)
        return SessionError("HELPER_REJECTED", http_status=status, reason="http_error")

    async def _request(self, method: str, path: str, *, ticket: HelperTicket | None = None,
                       payload: dict[str, Any] | None = None) -> dict[str, Any]:
        headers = {"Authorization": f"Bearer {ticket.connection}"} if ticket else {}
        stage = {"/api/helper/connect": "helper_connect", "/api/helper/session": "session_upload",
                 "/api/helper/result": "helper_receipt"}[path]
        status = None
        try:
            async with self.http.request(method, self.destination.url + path, json=payload,
                    headers=headers, allow_redirects=False,
                    timeout=aiohttp.ClientTimeout(total=300 if path.endswith("/session") else 10, connect=10)) as response:
                status = response.status
                if 300 <= response.status < 400:
                    raise SessionError("HELPER_REDIRECT", reason="redirect")
                raw = bytearray()
                async for chunk in response.content.iter_chunked(8192):
                    raw.extend(chunk)
                    if len(raw) > SessionBundle.MAX_BYTES:
                        raise SessionError("HELPER_RESPONSE", reason="response_limit")
                try:
                    data = json.loads(raw)
                except (ValueError, UnicodeError, RecursionError):
                    if response.status >= 500:
                        raise SessionError("HELPER_NETWORK", reason="invalid_json") from None
                    if 400 <= response.status < 500:
                        raise SessionError("HELPER_REJECTED", reason="invalid_json") from None
                    raise SessionError("HELPER_RESPONSE", reason="invalid_json") from None
                if response.status != 200:
                    raise self._server_error(path, response.status, data)
                if not isinstance(data, dict):
                    raise SessionError("HELPER_RESPONSE", reason="invalid_response")
                return data
        except SessionError as error:
            raise error.annotate(stage=stage, http_status=status, reason="http_error" if status != 200 else "invalid_response") from None
        except aiohttp.ClientSSLError:
            raise SessionError("HELPER_NETWORK", stage=stage, reason="tls", http_status=status) from None
        except aiohttp.ClientConnectorDNSError:
            raise SessionError("HELPER_NETWORK", stage=stage, reason="dns", http_status=status) from None
        except TimeoutError:
            raise SessionError("HELPER_NETWORK", stage=stage, reason="timeout", http_status=status) from None
        except (aiohttp.ClientError, OSError):
            raise SessionError("HELPER_NETWORK", stage=stage, reason="connection", http_status=status) from None

    async def connect(self) -> HelperTicket:
        data = await self._request("POST", "/api/helper/connect", payload={})
        token, expiry = data.get("connection"), data.get("expires_at")
        if (type(data.get("version")) is not int or data["version"] != 1
                or not isinstance(token, str) or not re.fullmatch(r"[A-Za-z0-9_-]{43}", token)
                or not isinstance(expiry, (int, float)) or isinstance(expiry, bool) or not math.isfinite(expiry)
                or not self.clock() < expiry <= self.clock() + 660):
            raise SessionError("HELPER_RESPONSE", stage="helper_connect", reason="invalid_response", http_status=200)
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

    async def receipt(self, ticket: HelperTicket, *, last_error: SessionError | None = None) -> int:
        """A lost POST acknowledgement must not cause a second credential upload."""
        last = last_error or SessionError("HELPER_RESULT_UNKNOWN", stage="helper_receipt")
        for attempt in range(self.receipt_attempts):
            if attempt:
                await asyncio.sleep(self.receipt_interval)
            try:
                data = await self._request("GET", "/api/helper/result", ticket=ticket)
                if data == {"state": "pending"}:
                    continue
                try:
                    return self.accepted(data)
                except SessionError as error:
                    raise error.annotate(stage="helper_receipt", reason="invalid_response", http_status=200) from None
            except SessionError as error:
                if error.code in HelperDiagnostics.RECEIPT_FAILURES or error.code == "HELPER_SERVER_BROWSER":
                    raise
                last = error
                continue
        context = last.diagnostic
        raise SessionError("HELPER_RESULT_UNKNOWN", stage=context.stage, reason=context.reason,
                           operation=context.operation, http_status=context.http_status,
                           upstream_status=context.upstream_status).annotate(stage="helper_receipt")

    async def send(self, ticket: HelperTicket, seed: ServerSeed) -> int:
        if ticket.expires_at <= self.clock():
            raise SessionError("HELPER_EXPIRED", stage="session_upload")
        try:
            seed.bundle.require_fresh(self.clock())
            seed.cookie.require_fresh(self.clock())
        except SessionError as error:
            raise error.annotate(stage="session_upload") from None
        try:
            data = await self._request("POST", "/api/helper/session", ticket=ticket, payload=seed.to_dict())
            try:
                return self.accepted(data)
            except SessionError as error:
                raise error.annotate(stage="session_upload", reason="invalid_response", http_status=200) from None
        except SessionError as error:
            if error.code not in ("HELPER_NETWORK", "HELPER_RESPONSE"):
                raise
            return await self.receipt(ticket, last_error=error)


class NativeChrome:
    """Own only a temporary TDM profile and the Chrome process launched for it."""

    LOGIN_URL = "https://www.twitch.tv/login"
    LOGIN_MESSAGE = "login"
    LOGIN_MISSING_CODE = "HELPER_LOGIN_MISSING"

    def __init__(self, executable: Path | None = None, *, startup_timeout: float = 30):
        self.executable = executable
        self.startup_timeout = startup_timeout
        self.profile: Path | None = None
        self.process: subprocess.Popen | None = None
        self.address = ""
        self._closing: asyncio.Task[None] | None = None
        self._capturing = False
        self._verified = False

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

    @classmethod
    @contextmanager
    def external_libraries(cls) -> Iterator[dict[str, str]]:
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
            yield cls.external_environment()
        finally:
            if reset is not None:
                reset(getattr(sys, "_MEIPASS", None))

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
        self._verified = True

    def find_executable(self) -> Path:
        executable = self.executable or self.find_chrome()
        if not executable.is_file():
            raise SessionError("HELPER_CHROME_MISSING")
        return executable

    def launch_arguments(self, executable: Path, port: int) -> list[str]:
        args = [str(executable), f"--user-data-dir={self.profile}",
            "--no-first-run", "--no-default-browser-check", "--disable-background-mode",
            "--disable-sync"]
        if self._capturing:
            args.extend(["--remote-debugging-address=127.0.0.1", f"--remote-debugging-port={port}", "about:blank"])
        else:
            args.append(self.LOGIN_URL)
        return args

    def exporter(self, *, timeout: float) -> BrowserExporter:
        return BrowserExporter(self.address, timeout=timeout)

    def _launch_browser(self, executable: Path) -> None:
        assert self.profile is not None
        self.executable = executable
        options: dict[str, Any] = {"stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL}
        if sys.platform == "win32":
            options["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
        else:
            options["start_new_session"] = True
        port = self.available_port() if self._capturing else 0
        self.address = f"http://127.0.0.1:{port}" if self._capturing else ""
        with self.external_libraries() as environment:
            # Keep browser auxiliary files inside the owned cleanup boundary.
            environment.update({name: str(self.profile / "tmp") for name in ("TMPDIR", "TMP", "TEMP")})
            self.process = subprocess.Popen(self.launch_arguments(executable, port), env=environment, **options)

    async def _wait_ready(self) -> None:
        if not self._capturing:
            if self.process is None or self.process.poll() is not None:
                raise SessionError("HELPER_BROWSER")
            return
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
        executable = self.find_executable()
        self.profile = Path(tempfile.mkdtemp(prefix="tdm-login-"))
        try:
            self.profile.chmod(0o700)
            temporary = self.profile / "tmp"
            temporary.mkdir(mode=0o700)
            self._launch_browser(executable)
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

    async def _restart_for_capture(self) -> None:
        if self._capturing:
            return
        # Let the user close the owned instance normally so its cookies/storage
        # are flushed. Never read a live profile or force-close successful login.
        while self.process is not None and self.process.poll() is None:
            await asyncio.sleep(.2)
        if self.process is None or self.process.returncode != 0:
            raise SessionError("HELPER_BROWSER")
        self._capturing = True
        self._verified = False
        self._launch_browser(self.find_executable())
        await self._wait_ready()

    async def _check_login(self) -> None:
        if self.process is None or self.process.poll() is not None:
            raise SessionError("HELPER_BROWSER")
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=5), trust_env=False) as http:
            endpoint = await self._browser_socket(http)
            async with http.ws_connect(endpoint, timeout=aiohttp.ClientWSTimeout(ws_close=2)) as socket:
                protocol = DevToolsConnection(socket)
                try:
                    self._require_owner(await protocol.command("SystemInfo.getProcessInfo", timeout=2))
                    data = await protocol.command("Storage.getCookies", timeout=5)
                    if not isinstance(data, dict) or not isinstance(data.get("cookies"), list):
                        raise SessionError("BROWSER_PROTOCOL")
                    if not any(isinstance(cookie, dict) and cookie.get("name") == "auth-token"
                               and cookie.get("domain") in (".twitch.tv", "twitch.tv", "www.twitch.tv")
                               and isinstance(cookie.get("value"), str) and cookie["value"]
                               for cookie in data["cookies"]):
                        raise SessionError(self.LOGIN_MISSING_CODE)
                finally:
                    await protocol.close()

    async def wait_authenticated(self, *, timeout: float) -> None:
        try:
            async with asyncio.timeout(timeout):  # type: ignore[attr-defined]
                await self._restart_for_capture()
                await self._check_login()
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
                    # Python 3.12 is required; the repository's Mypy target is still 3.10.
                    shutil.rmtree(profile, onexc=self._remove_readonly)  # type: ignore[call-arg]
                    self.profile = None
                    break
                except OSError:
                    # A missing child does not prove the entire profile was removed.
                    if not profile.exists():
                        self.profile = None
                        break
                    if attempt == 3:
                        raise SessionError("HELPER_PROFILE_CLEANUP") from None
                    await asyncio.sleep(.25)

    def _remove_readonly(self, function: Callable[..., Any], path: str, error: BaseException) -> None:
        target = Path(path)
        if (sys.platform != "win32" or not isinstance(error, PermissionError)
                or function not in (os.unlink, os.rmdir) or self.profile is None
                or target.is_symlink()
                or not target.resolve().is_relative_to(self.profile.resolve())):
            raise error
        info = target.lstat()
        if getattr(info, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT:
            raise error
        mode = info.st_mode
        if mode & stat.S_IWRITE:
            raise error
        # Only clear the read-only attribute on an owned entry that failed deletion.
        target.chmod(mode | stat.S_IWRITE)
        function(path)


class NativeChromium(NativeChrome):
    """Use native Chromium with the same manual-login and CDP capture flow."""

    @staticmethod
    def find_chromium() -> Path:
        if sys.platform == "darwin":
            candidates = [Path(root) / "Chromium.app/Contents/MacOS/Chromium"
                          for root in ("/Applications", str(Path.home() / "Applications"))]
        elif sys.platform == "win32":
            candidates = [Path(root) / "Chromium/Application/chrome.exe" for name in (
                "PROGRAMFILES", "PROGRAMFILES(X86)", "LOCALAPPDATA") if (root := os.environ.get(name))]
        else:
            candidates = [Path(value) for command in ("chromium", "chromium-browser")
                          if (value := shutil.which(command))]
        for path in candidates:
            if path.is_file():
                return path
        raise SessionError("HELPER_CHROMIUM_MISSING")

    def find_executable(self) -> Path:
        executable = self.executable or self.find_chromium()
        if not executable.is_file():
            raise SessionError("HELPER_CHROMIUM_MISSING")
        return executable


class NativeFirefox(NativeChrome):
    """Use installed Firefox while sharing owned-process/profile cleanup."""

    LOGIN_MISSING_CODE = "HELPER_FIREFOX_LOGIN"

    def __init__(self, executable: Path | None = None, *, startup_timeout: float = 30):
        super().__init__(executable, startup_timeout=startup_timeout)
        self.remote: DevToolsConnection | None = None
        self.http: aiohttp.ClientSession | None = None

    @staticmethod
    def external_environment() -> dict[str, str]:
        environment = NativeChrome.external_environment()
        # An inherited Marionette flag also marks an ordinary launch as automated.
        for name in ("MOZ_MARIONETTE", "MOZ_MARIONETTE_PREF_STATE_ACROSS_RESTARTS",
                     "MOZ_REMOTE_ALLOW_SYSTEM_ACCESS"):
            environment.pop(name, None)
        return environment

    @staticmethod
    def find_firefox() -> Path:
        candidates: list[Path] = []
        if sys.platform == "darwin":
            candidates = [Path(root) / "Firefox.app/Contents/MacOS/firefox"
                          for root in ("/Applications", str(Path.home() / "Applications"))]
        elif sys.platform == "win32":
            candidates = [Path(root) / "Mozilla Firefox/firefox.exe" for name in (
                "PROGRAMFILES", "PROGRAMFILES(X86)", "LOCALAPPDATA") if (root := os.environ.get(name))]
        else:
            candidates = [Path(value) for command in ("firefox", "firefox-esr")
                          if (value := shutil.which(command))]
        for path in candidates:
            if path.is_file():
                return path
        raise SessionError("HELPER_FIREFOX_MISSING")

    def find_executable(self) -> Path:
        executable = self.executable or self.find_firefox()
        if not executable.is_file():
            raise SessionError("HELPER_FIREFOX_MISSING")
        return executable

    def launch_arguments(self, executable: Path, port: int) -> list[str]:
        self.executable = executable
        args = [str(executable), "--new-instance", "--profile", str(self.profile)]
        if sys.platform == "win32":
            # The native launcher must stay alive until its browser child exits.
            args.append("--wait-for-browser")
        if self._capturing:
            args.extend(["--remote-debugging-port", str(port), "about:blank"])
        else:
            # RemoteAgent enables navigator.webdriver at startup, even before
            # session.new. Leave manual Twitch sign-in entirely outside BiDi.
            args.append(self.LOGIN_URL)
        return args

    def _owns_pid(self, pid: int) -> bool:
        if self.process is None or self.process.poll() is not None:
            return False
        if pid == self.process.pid:
            return True
        if sys.platform != "win32" or self.executable is None:
            return False
        # Windows Firefox's launcher stays alive while its browser child runs.
        # Verify the direct parent and executable using OS data, not BiDi claims.
        from ctypes import wintypes

        class ProcessEntry(ctypes.Structure):
            _fields_ = [("size", wintypes.DWORD), ("usage", wintypes.DWORD),
                ("pid", wintypes.DWORD), ("heap", ctypes.c_size_t), ("module", wintypes.DWORD),
                ("threads", wintypes.DWORD), ("parent", wintypes.DWORD),
                ("priority", wintypes.LONG), ("flags", wintypes.DWORD),
                ("name", wintypes.WCHAR * 260)]

        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
        kernel.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel.OpenProcess.restype = wintypes.HANDLE
        kernel.QueryFullProcessImageNameW.argtypes = [wintypes.HANDLE, wintypes.DWORD,
                                                     wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)]
        first, following = kernel.Process32FirstW, kernel.Process32NextW
        for function in (first, following):
            function.argtypes = [wintypes.HANDLE, ctypes.POINTER(ProcessEntry)]
            function.restype = wintypes.BOOL
        snapshot = kernel.CreateToolhelp32Snapshot(2, 0)
        if snapshot == wintypes.HANDLE(-1).value:
            return False
        try:
            entry = ProcessEntry()
            entry.size = ctypes.sizeof(entry)
            available = first(snapshot, ctypes.byref(entry))
            while available:
                if entry.pid == pid:
                    if entry.parent != self.process.pid:
                        return False
                    handle = kernel.OpenProcess(0x1000, False, pid)
                    if not handle:
                        return False
                    try:
                        path, size = ctypes.create_unicode_buffer(32768), wintypes.DWORD(32768)
                        return bool(kernel.QueryFullProcessImageNameW(handle, 0, path, ctypes.byref(size))
                                    and Path(path.value).resolve() == self.executable.resolve())
                    finally:
                        kernel.CloseHandle(handle)
                available = following(snapshot, ctypes.byref(entry))
            return False
        finally:
            kernel.CloseHandle(snapshot)

    def _require_firefox_owner(self, result: Any) -> None:
        capabilities = result.get("capabilities") if isinstance(result, dict) else None
        if (not isinstance(capabilities, dict) or self.process is None or self.profile is None
                or type(capabilities.get("moz:processID")) is not int
                or not self._owns_pid(capabilities["moz:processID"])
                or not isinstance(capabilities.get("moz:profile"), str)
                or Path(capabilities["moz:profile"]).resolve() != self.profile.resolve()):
            raise SessionError("HELPER_BROWSER_OWNER")
        self._verified = True
        version = str(capabilities.get("browserVersion", "")).split(".")[0]
        if not version.isdecimal() or int(version) < 143:
            raise SessionError("HELPER_FIREFOX_VERSION")

    async def _wait_ready(self) -> None:
        from src.auth.firefox_session import FirefoxPage

        if not self._capturing:
            if self.process is None or self.process.poll() is not None:
                raise SessionError("HELPER_BROWSER")
            return
        self.http = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=2), trust_env=False)
        async with asyncio.timeout(self.startup_timeout):  # type: ignore[attr-defined]
            while self.process is not None and self.process.poll() is None:
                try:
                    socket = await self.http.ws_connect(self.address.replace("http:", "ws:") + "/session",
                        max_msg_size=16 * 1024 * 1024, timeout=aiohttp.ClientWSTimeout(ws_close=1))
                except (aiohttp.ClientError, TimeoutError, OSError):
                    await asyncio.sleep(.1)
                    continue
                self.remote = DevToolsConnection(socket, extra_events=FirefoxPage.EVENTS)
                self._require_firefox_owner(await self.remote.command("session.new", {"capabilities": {}}, timeout=5))
                return
        raise SessionError("HELPER_BROWSER")

    async def _check_login(self) -> None:
        from src.auth.firefox_session import FirefoxPage

        assert self.remote is not None
        if self.process is None or self.process.poll() is not None:
            raise SessionError("HELPER_BROWSER")
        data = await self.remote.command("storage.getCookies", {
            "filter": {"name": "auth-token"},
            "partition": {"type": "storageKey", "userContext": "default"},
        }, timeout=5)
        if not isinstance(data, dict) or not isinstance(data.get("cookies"), list):
            raise SessionError("BROWSER_PROTOCOL")
        if not any(isinstance(cookie, dict)
                   and cookie.get("domain") in (".twitch.tv", "twitch.tv", "www.twitch.tv")
                   and cookie.get("name") == "auth-token" and FirefoxPage.string(cookie.get("value"))
                   for cookie in data["cookies"]):
            raise SessionError(self.LOGIN_MISSING_CODE)

    def exporter(self, *, timeout: float) -> BrowserExporter:
        from src.auth.firefox_session import FirefoxExporter

        assert self.remote is not None
        return FirefoxExporter(self.address, self.remote, timeout=timeout)

    async def _request_close(self) -> None:
        if self.remote is not None and self._verified:
            await self.remote.command("browser.close", timeout=2)

    async def _close(self) -> None:
        try:
            await super()._close()
        finally:
            if self.remote is not None:
                await self.remote.close()
                self.remote = None
            if self.http is not None:
                await self.http.close()
                self.http = None


class BrowserSelection:
    @staticmethod
    def create(choice: str = "auto", *, chrome: Path | None = None, chromium: Path | None = None,
               firefox: Path | None = None) -> NativeChrome:
        browsers = (
            ("chrome", NativeChrome, chrome, NativeChrome.find_chrome, "HELPER_CHROME_MISSING"),
            ("chromium", NativeChromium, chromium, NativeChromium.find_chromium, "HELPER_CHROMIUM_MISSING"),
            ("firefox", NativeFirefox, firefox, NativeFirefox.find_firefox, "HELPER_FIREFOX_MISSING"),
        )
        for name, browser, executable, _find, _missing in browsers:
            if executable is not None or choice == name:
                return browser(executable)
        for _name, browser, _executable, find, missing in browsers:
            try:
                return browser(find())
            except SessionError as error:
                if error.code != missing:
                    raise
        raise SessionError("HELPER_BROWSER_MISSING")


class NativeLoginHelper:
    """Admission precedes the browser; success follows proof and local cleanup."""

    def __init__(self, destination: str, *,
                 insecure: bool = False, ca_file: Path | None = None,
                 browser_factory: Callable[..., Any] = BrowserSelection.create,
                 exporter_factory: Callable[..., Any] | None = None,
                 clock: Callable[[], float] = time.time, report: Callable[[str], None] = lambda _key: None):
        self.destination = HelperDestination(destination)
        if insecure and ca_file is not None:
            raise ValueError("--insecure and --ca-file are exclusive")
        self.insecure, self.ssl_context = insecure, HelperHTTP.trust(ca_file)
        self.browser_factory, self.exporter_factory = browser_factory, exporter_factory
        self.clock, self.report = clock, report

    async def run(self) -> None:
        phase = "helper_connect"
        try:
            if self.insecure:
                self.report("insecure_warning")
            self.report("connecting")
            async with HelperHTTP(self.destination, clock=self.clock, insecure=self.insecure,
                                  ssl_context=self.ssl_context) as client:
                ticket = await client.connect()
                phase = "browser_start"
                async with self.browser_factory() as browser:
                    try:
                        phase = "browser_login"
                        self.report(getattr(browser, "LOGIN_MESSAGE", "login"))
                        await browser.wait_authenticated(timeout=max(1, ticket.expires_at - self.clock() - 15))
                        phase = "browser_capture"
                        self.report("capturing")
                        timeout = min(120, max(1, ticket.expires_at - self.clock()))
                        exporter = (self.exporter_factory(browser.address, timeout=timeout) if self.exporter_factory
                                    else browser.exporter(timeout=timeout))
                        seed = await exporter.capture_seed()
                        phase = "session_upload"
                        self.report("sending")
                        await client.send(ticket, seed)
                    except SessionError as error:
                        raise self._phase_error(error, phase) from None
                    except Exception as error:
                        raise self._unexpected_error(error, phase) from None
                    finally:
                        phase = "browser_cleanup"
            self.report("success")
        except SessionError as error:
            raise self._phase_error(error, phase) from None
        except Exception as error:
            raise self._unexpected_error(error, phase) from None

    @staticmethod
    def _unexpected_error(error: Exception, phase: str) -> SessionError:
        return SessionError("HELPER_FAILED", stage=phase,
                            reason="timeout" if isinstance(error, TimeoutError) else "unexpected_exception")

    @staticmethod
    def _phase_error(error: SessionError, phase: str) -> SessionError:
        if error.code in ("HELPER_BROWSER_CLEANUP", "HELPER_PROFILE_CLEANUP"):
            phase = "browser_cleanup"
        reason = {"HELPER_LOGIN_TIMEOUT": "timeout", "CAPTURE_TIMEOUT": "timeout",
                  "SDK_TIMEOUT": "timeout", "CAPTURE_LIMIT": "response_limit"}.get(error.code)
        return error.annotate(stage=phase, reason=reason)


class LoginHelperCLI:
    @staticmethod
    def report_error(error: str | SessionError) -> None:
        code, guidance = HelperDiagnostics.describe(error.code if isinstance(error, SessionError) else error)
        print(_.t["login"]["error_code"].format(error_code=code), file=sys.stderr)
        print(guidance, file=sys.stderr)
        if isinstance(error, SessionError):
            diagnostic = error.diagnostic.to_dict()
            diagnostic["code"] = code.removeprefix("SESSION_")
            print("SESSION_DIAGNOSTIC " + json.dumps(diagnostic, sort_keys=True), file=sys.stderr)

    @staticmethod
    async def run_cancellable(helper: NativeLoginHelper) -> None:
        """Translate ordinary POSIX termination into owned-resource cleanup."""
        loop, task = asyncio.get_running_loop(), asyncio.current_task()
        assert task is not None
        handlers: dict[signal.Signals, Any] = {}
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
        tls = parser.add_mutually_exclusive_group()
        tls.add_argument("--insecure", action="store_true", help=words["insecure_help"])
        tls.add_argument("--ca-file", type=Path, help=words["ca_file_help"])
        parser.add_argument("--browser", choices=("auto", "chrome", "chromium", "firefox"),
                            default="auto", help=words["browser_help"])
        paths = parser.add_mutually_exclusive_group()
        paths.add_argument("--chrome", type=Path, help=words["chrome_help"])
        paths.add_argument("--chromium", type=Path, help=words["chromium_help"])
        paths.add_argument("--firefox", type=Path, help=words["firefox_help"])
        parser.add_argument("--language", choices=_.get_languages(), help=words["language_help"])
        parser.add_argument("--no-pause", action="store_true", help=words["no_pause_help"])
        args = parser.parse_args()
        if any(getattr(args, name) is not None and args.browser not in ("auto", name)
               for name in ("chrome", "chromium", "firefox")):
            parser.error(words["browser_conflict"])
        result = 0
        try:
            destination = HelperDestination(args.tdm or input(words["destination_prompt"]).strip()).url
            helper = NativeLoginHelper(destination, insecure=args.insecure, ca_file=args.ca_file,
                browser_factory=lambda: BrowserSelection.create(
                args.browser, chrome=args.chrome, chromium=args.chromium, firefox=args.firefox),
                report=lambda key: print(words[key],  # type: ignore[literal-required]
                    file=sys.stderr if key == "insecure_warning" else sys.stdout, flush=True))
            print(words["destination"].format(url=helper.destination.url), flush=True)
            if args.ca_file is not None and helper.destination.url.startswith("https://"):
                print(words["ca_file_in_use"], flush=True)
            asyncio.run(LoginHelperCLI.run_cancellable(helper))
        except SessionError as error:
            # Codes originate in our validators; never print response bodies or exceptions.
            LoginHelperCLI.report_error(error)
            result = 1
        except (KeyboardInterrupt, EOFError, asyncio.CancelledError):
            print(words["cancelled"], file=sys.stderr)
            result = 130
        except Exception:
            LoginHelperCLI.report_error(SessionError("HELPER_FAILED", stage="helper_configuration",
                                                    reason="unexpected_exception"))
            result = 1
        if result != 130 and getattr(sys, "frozen", False) and sys.stdin.isatty() and not args.no_pause:
            with suppress(KeyboardInterrupt, EOFError):
                input(words["wait_to_close"])
        raise SystemExit(result)


if __name__ == "__main__":
    LoginHelperCLI.main()
