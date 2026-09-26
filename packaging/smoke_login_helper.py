"""Exercise the packaged executable without Python on PATH or live credentials."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


class RefusingInstance(BaseHTTPRequestHandler):
    requests: list[tuple[str, str, bytes]] = []

    @staticmethod
    def reply() -> tuple[int, dict]:
        return 403, {"detail": "session_helper_disabled"}

    def do_POST(self) -> None:
        body = self.rfile.read(int(self.headers.get("Content-Length", "0")))
        self.requests.append((self.path, self.headers.get("X-TDM-Request", ""), body))
        status, payload = self.reply()
        reply = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(reply)))
        self.end_headers()
        self.wfile.write(reply)

    def log_message(self, _format, *_args) -> None:
        pass


class AdmittingInstance(RefusingInstance):
    requests: list[tuple[str, str, bytes]] = []

    @staticmethod
    def reply() -> tuple[int, dict]:
        # Exercise normal startup and timeout cleanup with no account credentials.
        return 200, {"version": 1, "connection": "A" * 43, "expires_at": time.time() + 20}


class PackagedSmoke:
    def __init__(self, executable: Path):
        self.executable = executable.resolve(strict=True)

    def run(self) -> None:
        server = ThreadingHTTPServer(("127.0.0.1", 0), RefusingInstance)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with tempfile.TemporaryDirectory(prefix="tdm-helper-smoke-") as directory:
                environment = {**os.environ, "PATH": "", "PYTHONPATH": "", "PYTHONHOME": ""}
                environment.pop("VIRTUAL_ENV", None)
                options = {"cwd": directory, "env": environment, "capture_output": True,
                           "text": True, "encoding": "utf-8", "timeout": 60, "check": False}
                help_result = subprocess.run([str(self.executable), "--help"], **options)
                assert help_result.returncode == 0, help_result.stderr
                assert "--tdm" in help_result.stdout and "--output" not in help_result.stdout
                translated = subprocess.run([str(self.executable), "--language", "简体中文", "--help"], **options)
                assert translated.returncode == 0, translated.stderr
                assert "连接 Twitch 与 TDM" in translated.stdout
                refused = subprocess.run([str(self.executable), "--tdm",
                    f"http://127.0.0.1:{server.server_port}", "--no-pause"], **options)
                assert refused.returncode == 1, refused.stdout + refused.stderr
                assert "SESSION_HELPER_DISABLED" in refused.stderr
                assert RefusingInstance.requests == [("/api/helper/connect", "1", b"{}")]
                assert list(Path(directory).iterdir()) == [], "Helper retained local files"
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)
        print("Packaged helper: startup, locales, admission and no-local-files checks passed.")

    def browser(self) -> None:
        # Only the test harness imports source for installed-Chrome discovery. All
        # browser startup, control and cleanup run in the actual packaged binary.
        sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
        from src.auth.login_helper import NativeChrome

        chrome = NativeChrome.find_chrome()
        server = ThreadingHTTPServer(("127.0.0.1", 0), AdmittingInstance)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with tempfile.TemporaryDirectory(prefix="tdm-helper-browser-smoke-") as directory:
                environment = {**os.environ, "TMPDIR": directory, "TMP": directory, "TEMP": directory,
                               "PYTHONPATH": "", "PYTHONHOME": ""}
                environment.pop("VIRTUAL_ENV", None)
                result = subprocess.run([str(self.executable), "--tdm",
                    f"http://127.0.0.1:{server.server_port}", "--chrome", str(chrome), "--no-pause"],
                    cwd=directory, env=environment, capture_output=True, text=True,
                    encoding="utf-8", timeout=90, check=False)
                assert result.returncode == 1, result.stdout + result.stderr
                # This code is reachable only after the CDP ownership check succeeds;
                # a launch, ownership or cleanup failure yields another code.
                assert "SESSION_HELPER_LOGIN_TIMEOUT" in result.stderr, result.stdout + result.stderr
                assert AdmittingInstance.requests == [("/api/helper/connect", "1", b"{}")]
                assert list(Path(directory).iterdir()) == [], "Helper retained temporary browser files"
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)
        print("Packaged helper: installed Chrome startup, control, timeout and profile cleanup passed.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("executable", type=Path)
    parser.add_argument("--browser", action="store_true")
    args = parser.parse_args()
    smoke = PackagedSmoke(args.executable)
    smoke.run()
    if args.browser:
        smoke.browser()
