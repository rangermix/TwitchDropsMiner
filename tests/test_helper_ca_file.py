"""Optional --ca-file trust for the helper's TDM connection, with verification kept on."""

import os
import shutil
import ssl
import subprocess
import sys
from contextlib import asynccontextmanager
from pathlib import Path

import pytest
import truststore
from aiohttp import web

from src.auth import login_helper
from src.auth.session_bundle import SessionError


ROOT = Path(__file__).resolve().parents[1]
# CI must not silently lose platform coverage, so only local runs may skip.
needs_openssl = pytest.mark.skipif(shutil.which("openssl") is None and not os.environ.get("CI"),
                                   reason="openssl CLI generates test certificates")


def openssl(*args: str) -> None:
    subprocess.run(["openssl", *args], check=True, capture_output=True)


def extensions(directory: Path, name: str, lines: str) -> list[str]:
    """Config-file extensions work with both OpenSSL and macOS LibreSSL."""
    config = directory / f"{name}.cnf"
    config.write_text("[req]\ndistinguished_name=dn\nprompt=no\n[dn]\nCN=TDM test\n[ext]\n" + lines)
    return ["-config", str(config), "-extensions", "ext"]


def self_signed(directory: Path, name: str, *, san: str = "IP:127.0.0.1", ca: bool = True) -> tuple[Path, Path]:
    # serverAuth and a short validity satisfy Apple's TLS server certificate rules.
    cert, key = directory / f"{name}.pem", directory / f"{name}.key"
    openssl("req", "-x509", "-newkey", "rsa:2048", "-nodes", "-keyout", str(key), "-out", str(cert), "-days", "2",
            *extensions(directory, name, f"subjectAltName={san}\nextendedKeyUsage=serverAuth\n"
                        f"basicConstraints=critical,CA:{'TRUE' if ca else 'FALSE'}\n"))
    return cert, key


def private_ca_chain(directory: Path) -> tuple[Path, Path, Path]:
    """Return a private root CA certificate plus a server certificate it signed."""
    ca, ca_key = directory / "ca.pem", directory / "ca.key"
    openssl("req", "-x509", "-newkey", "rsa:2048", "-nodes", "-keyout", str(ca_key), "-out", str(ca), "-days", "2",
            *extensions(directory, "ca", "basicConstraints=critical,CA:TRUE\nkeyUsage=keyCertSign\n"))
    key, request, cert = directory / "server.key", directory / "server.csr", directory / "server.pem"
    server = extensions(directory, "server", "subjectAltName=IP:127.0.0.1\nextendedKeyUsage=serverAuth\n"
                        "basicConstraints=critical,CA:FALSE\n")
    openssl("req", "-new", "-newkey", "rsa:2048", "-nodes", "-keyout", str(key), "-out", str(request), server[0], server[1])
    openssl("x509", "-req", "-in", str(request), "-CA", str(ca), "-CAkey", str(ca_key), "-CAcreateserial",
            "-days", "2", "-extfile", server[1], "-extensions", "ext", "-out", str(cert))
    return ca, cert, key


@asynccontextmanager
async def tls_instance(cert: Path, key: Path):
    async def connect(_request):
        return web.json_response({"version": 1, "connection": "A" * 43, "expires_at": 1600})

    app = web.Application()
    app.router.add_post("/api/helper/connect", connect)
    server_context = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
    server_context.load_cert_chain(cert, key)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0, ssl_context=server_context)
    await site.start()
    try:
        yield login_helper.HelperDestination(f"https://127.0.0.1:{site._server.sockets[0].getsockname()[1]}")
    finally:
        await runner.cleanup()


async def connect(destination, ca_file=None):
    context = login_helper.HelperHTTP.trust(ca_file)
    async with login_helper.HelperHTTP(destination, ssl_context=context, clock=lambda: 1000) as client:
        return await client.connect()


def test_default_context_keeps_system_verification():
    client = login_helper.HelperHTTP(login_helper.HelperDestination("https://tdm.example.test"))
    assert isinstance(client.ssl_context, truststore.SSLContext)
    assert client.ssl_context.verify_mode == ssl.CERT_REQUIRED
    assert client.ssl_context.check_hostname is True


@needs_openssl
@pytest.mark.asyncio
async def test_self_signed_certificate_requires_ca_file(tmp_path):
    cert, key = self_signed(tmp_path, "tdm")
    async with tls_instance(cert, key) as destination:
        with pytest.raises(SessionError, match="HELPER_NETWORK") as error:
            await connect(destination)
        assert error.value.diagnostic.reason == "tls"
        ticket = await connect(destination, cert)
    assert ticket.expires_at == 1600


@needs_openssl
@pytest.mark.asyncio
async def test_private_ca_trusts_server_certificate_it_signed(tmp_path):
    ca, cert, key = private_ca_chain(tmp_path)
    async with tls_instance(cert, key) as destination:
        assert (await connect(destination, ca)).expires_at == 1600


@needs_openssl
@pytest.mark.asyncio
async def test_ca_file_keeps_hostname_verification(tmp_path):
    cert, key = self_signed(tmp_path, "other", san="DNS:other.example.test")
    async with tls_instance(cert, key) as destination:
        with pytest.raises(SessionError, match="HELPER_NETWORK") as error:
            await connect(destination, cert)
    assert error.value.diagnostic.reason == "tls"


def unusable_files(tmp_path: Path) -> list[Path]:
    (tmp_path / "empty.pem").write_text("")
    (tmp_path / "text.pem").write_text("private-marker not a certificate")
    (tmp_path / "folder").mkdir()
    return [tmp_path / "missing.pem", tmp_path / "empty.pem", tmp_path / "text.pem", tmp_path / "folder"]


def assert_fixed_error(error: SessionError) -> None:
    assert error.code == "HELPER_CA_FILE"
    assert error.diagnostic.stage == "helper_configuration"
    assert "private-marker" not in repr(error) + str(error)
    assert "pem" not in str(error).lower()


def test_unusable_ca_file_fails_with_fixed_error(tmp_path):
    for path in unusable_files(tmp_path):
        with pytest.raises(SessionError) as error:
            login_helper.HelperHTTP.trust(path)
        assert_fixed_error(error.value)


@needs_openssl
def test_server_only_certificate_and_private_key_are_rejected(tmp_path):
    # Native Windows/macOS verification ignores non-CA certificates; refuse them everywhere.
    cert, key = self_signed(tmp_path, "leaf", ca=False)
    for path in (cert, key):
        with pytest.raises(SessionError) as error:
            login_helper.HelperHTTP.trust(path)
        assert_fixed_error(error.value)


def test_helper_rejects_ca_file_before_connecting(tmp_path):
    with pytest.raises(SessionError, match="HELPER_CA_FILE"):
        login_helper.NativeLoginHelper("https://tdm.example.test", ca_file=tmp_path / "missing.pem")


def run_cli(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run([sys.executable, "login_helper.py", "--no-pause", *args], cwd=ROOT,
                          capture_output=True, text=True, encoding="utf-8", check=False, timeout=60)


def test_cli_documents_ca_file_and_reports_fixed_error(tmp_path):
    assert "--ca-file" in run_cli("--help").stdout
    path = tmp_path / "private-marker.pem"
    path.write_text("private-marker")
    result = run_cli("--tdm", "https://tdm.example.test", "--ca-file", str(path))
    output = result.stdout + result.stderr
    assert result.returncode == 1
    assert "SESSION_HELPER_CA_FILE" in output
    assert "private-marker" not in output


@needs_openssl
def test_cli_notes_custom_ca_without_its_path(tmp_path):
    cert, _key = self_signed(tmp_path, "private-marker")
    # Port 1 refuses the connection, so the run ends after configuration.
    result = run_cli("--tdm", "https://127.0.0.1:1", "--ca-file", str(cert))
    output = result.stdout + result.stderr
    assert "custom certificate file" in output
    assert "private-marker" not in output
    assert "SESSION_HELPER_NETWORK" in output
    plain = run_cli("--tdm", "http://127.0.0.1:1", "--ca-file", str(cert))
    assert "custom certificate file" not in plain.stdout + plain.stderr


def test_ca_file_and_insecure_are_mutually_exclusive(tmp_path):
    with pytest.raises(ValueError):
        login_helper.NativeLoginHelper("https://tdm.example.test", insecure=True, ca_file=tmp_path / "ca.pem")
    result = run_cli("--tdm", "https://tdm.example.test", "--insecure", "--ca-file", str(tmp_path / "ca.pem"))
    assert result.returncode == 2
    assert "not allowed with" in result.stderr
