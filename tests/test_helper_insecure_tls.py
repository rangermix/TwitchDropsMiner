"""--insecure is limited to HTTPS hosts resolving only to private addresses, then pinned."""

import asyncio
import socket

import pytest

from src.auth import login_helper
from src.auth.session_bundle import SessionError
from tests.test_helper_ca_file import run_cli
from tests.test_login_helper import CLOCK, instance, loopback_tls


class FakeResolver:
    def __init__(self, *answers):
        self.answers, self.calls = list(answers), []

    async def resolve(self, host, port=0, family=socket.AF_INET):
        self.calls.append(host)
        answer = self.answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return [{"hostname": host, "host": value, "port": port, "family": socket.AF_INET6 if ":" in value
                 else socket.AF_INET, "proto": 0, "flags": 0} for value in answer]

    async def close(self):
        pass


def private_resolver(*answers, url="https://tdm.home.arpa:8443"):
    fake = FakeResolver(*answers)
    return login_helper.PrivateResolver(login_helper.HelperDestination(url), fake), fake


@pytest.mark.parametrize("address", [
    "10.1.2.3", "172.16.0.1", "172.31.255.255", "192.168.1.10", "127.0.0.1",
    "::1", "fd12:3456::1", "fe80::1", "fe80::1%eth0", "::ffff:192.168.1.2",
])
def test_private_and_loopback_addresses_are_allowed(address):
    assert login_helper.PrivateResolver.allowed(address)


@pytest.mark.parametrize("address", [
    "8.8.8.8", "172.32.0.1", "100.64.0.1", "169.254.1.1", "0.0.0.0", "2001:db8::1",
    "2606:4700::1111", "::ffff:8.8.8.8", "::", "not-an-address",
])
def test_public_and_other_addresses_are_refused(address):
    assert not login_helper.PrivateResolver.allowed(address)


@pytest.mark.asyncio
@pytest.mark.parametrize("answer", [["192.168.1.10", "8.8.8.8"], ["fd00::1", "2001:4860::8888"], []])
async def test_mixed_or_empty_answers_are_refused(answer):
    resolver, _fake = private_resolver(answer)
    with pytest.raises(SessionError, match="HELPER_INSECURE_TLS") as error:
        await resolver.vet()
    assert error.value.diagnostic.stage == "helper_configuration"
    assert "tdm.home.arpa" not in repr(error.value) + str(error.value)


@pytest.mark.asyncio
async def test_plain_http_is_refused_without_resolving():
    resolver, fake = private_resolver(["192.168.1.10"], url="http://192.168.1.10:8080")
    with pytest.raises(SessionError, match="HELPER_INSECURE_TLS"):
        await resolver.vet()
    assert fake.calls == []


@pytest.mark.asyncio
async def test_resolution_failure_is_a_network_error():
    resolver, _fake = private_resolver(OSError("no such host"))
    with pytest.raises(SessionError, match="HELPER_NETWORK") as error:
        await resolver.vet()
    assert error.value.diagnostic.reason == "dns"


@pytest.mark.asyncio
async def test_connections_are_pinned_to_vetted_addresses():
    # A later DNS answer cannot redirect the connection to a public address.
    resolver, fake = private_resolver(["192.168.1.10", "fd00::10"], ["8.8.8.8"])
    await resolver.vet()
    ipv4 = await resolver.resolve("tdm.home.arpa", 8443, socket.AF_INET)
    assert [result["host"] for result in ipv4] == ["192.168.1.10"]
    both = await resolver.resolve("tdm.home.arpa", 8443, socket.AF_UNSPEC)
    assert [result["host"] for result in both] == ["192.168.1.10", "fd00::10"]
    assert fake.calls == ["tdm.home.arpa"]
    with pytest.raises(OSError):
        await resolver.resolve("other.example.test", 443, socket.AF_UNSPEC)


@pytest.mark.asyncio
async def test_unvetted_resolver_refuses_every_host():
    resolver, _fake = private_resolver(["192.168.1.10"])
    with pytest.raises(OSError):
        await resolver.resolve("tdm.home.arpa", 8443, socket.AF_UNSPEC)


@pytest.mark.asyncio
async def test_default_connections_keep_system_resolution():
    async with instance() as (address, _requests), \
            login_helper.HelperHTTP(login_helper.HelperDestination(address), clock=lambda: CLOCK) as client:
        assert client.resolver is None
        await client.connect()


@pytest.mark.asyncio
async def test_public_literal_is_refused_before_connecting():
    destination = login_helper.HelperDestination("https://8.8.8.8:443")
    with pytest.raises(SessionError, match="HELPER_INSECURE_TLS"):
        async with login_helper.HelperHTTP(destination, insecure=True):
            pass


@pytest.mark.asyncio
async def test_insecure_connects_through_pinned_loopback_name():
    async with instance(ssl_context=loopback_tls()) as (address, requests):
        port = address.rsplit(":", 1)[1]
        destination = login_helper.HelperDestination(f"https://localhost:{port}")
        async with login_helper.HelperHTTP(destination, clock=lambda: CLOCK, insecure=True) as client:
            await client.connect()
            assert client.resolver is not None and client.resolver.addresses
        assert [row[0] for row in requests] == ["/api/helper/connect"]


@pytest.mark.asyncio
async def test_native_helper_warns_before_refusing_plain_http():
    output = []
    helper = login_helper.NativeLoginHelper("http://192.168.1.10:8080", insecure=True, report=output.append)
    with pytest.raises(SessionError, match="HELPER_INSECURE_TLS"):
        await helper.run()
    assert output == ["insecure_warning", "connecting"]


def test_cli_warns_and_refuses_public_address_without_echoing_it():
    result = run_cli("--tdm", "https://8.8.8.8:443", "--insecure")
    assert result.returncode == 1
    assert "HTTPS certificate verification is disabled" in result.stderr
    assert "SESSION_HELPER_INSECURE_TLS" in result.stderr
    assert "8.8.8.8" not in result.stderr


@pytest.mark.asyncio
async def test_resolution_timeout_is_a_network_error(monkeypatch):
    class Slow(FakeResolver):
        async def resolve(self, host, port=0, family=socket.AF_INET):
            await asyncio.sleep(1)

    monkeypatch.setattr(login_helper.PrivateResolver, "DNS_TIMEOUT", 0.01)
    resolver = login_helper.PrivateResolver(login_helper.HelperDestination("https://tdm.home.arpa:8443"), Slow())
    with pytest.raises(SessionError, match="HELPER_NETWORK") as error:
        await resolver.vet()
    assert error.value.diagnostic.reason == "timeout"


@pytest.mark.asyncio
async def test_connector_uses_only_the_pinned_resolver(monkeypatch):
    # tdm.test resolves only through the vetting resolver, so the connection succeeds
    # only if aiohttp's connector asks the pinned resolver instead of the system.
    monkeypatch.setattr(login_helper.aiohttp, "ThreadedResolver", lambda: FakeResolver(["127.0.0.1"]))
    async with instance(ssl_context=loopback_tls()) as (address, requests):
        destination = login_helper.HelperDestination(f"https://tdm.test:{address.rsplit(':', 1)[1]}")
        async with login_helper.HelperHTTP(destination, clock=lambda: CLOCK, insecure=True) as client:
            await client.connect()
        assert [row[0] for row in requests] == ["/api/helper/connect"]
