"""Private server bootstrap state; no live cookies or network access."""

import copy
import json
import stat

import pytest

from src.auth.session_bundle import PrivateSessionFile, SessionError
from tests.test_imported_session import bundle_data


def seed_data():
    return {
        "version": 1, "bundle": bundle_data(),
        "sdk_cookie": {"value": "private-sdk-cookie", "expires_at": 9000},
    }


def test_seed_preserves_expired_integrity_context_for_new_issuance(tmp_path):
    from src.auth.server_seed import ServerSeed

    seed = ServerSeed.from_dict(seed_data(), now=5000)
    assert seed.bundle.expires_at < 5000
    seed.cookie.require_fresh(5000)
    with pytest.raises(SessionError, match="SDK_EXPIRED"):
        seed.cookie.require_fresh(9000)
    path = tmp_path / "server-seed.json"
    PrivateSessionFile(path).write(seed.to_dict())
    assert ServerSeed.from_dict(PrivateSessionFile(path).read(), now=5000) == seed
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert "private-sdk-cookie" not in repr(seed)
    assert "test-token" not in repr(seed)


@pytest.mark.parametrize("cookie", [
    {"value": "secret\r\nInjected: true", "expires_at": 9000},
    {"value": "secret; other=value", "expires_at": 9000},
    {"value": "", "expires_at": 9000},
    {"value": "x" * 8193, "expires_at": 9000},
    {"value": "secret", "expires_at": float("nan")},
    {"value": "secret", "expires_at": float("inf")},
    {"value": "secret", "expires_at": True},
    {"value": "secret", "expires_at": -1},
    {"value": "secret", "expires_at": 9000, "domain": "evil.test"},
])
def test_invalid_cookie_is_rejected_without_exposing_value(cookie):
    from src.auth.server_seed import ServerSeed

    data = seed_data()
    data["sdk_cookie"] = cookie
    with pytest.raises(SessionError, match="SDK_COOKIE") as error:
        ServerSeed.from_dict(data, now=1000)
    assert "secret" not in str(error.value)


@pytest.mark.parametrize("mutation", [
    lambda d: d.update(version=2),
    lambda d: d.update(extra="private"),
    lambda d: d.pop("sdk_cookie"),
    lambda d: d["bundle"]["headers"].update(authorization="Bearer private"),
])
def test_invalid_seed_envelope_is_rejected(mutation):
    from src.auth.server_seed import ServerSeed

    data = copy.deepcopy(seed_data())
    mutation(data)
    with pytest.raises(SessionError):
        ServerSeed.from_dict(data, now=1000)


def test_cookie_parameters_cannot_select_another_host_or_script_access():
    from src.auth.server_seed import ServerSeed

    cookie = ServerSeed.from_dict(seed_data(), now=1000).cookie.to_browser_cookie()
    assert cookie == {
        "name": "KP_UIDz-ssn", "value": "private-sdk-cookie", "expires": 9000,
        "domain": "k.twitchcdn.net", "path": "/", "secure": True,
        "httpOnly": True, "sameSite": "None",
    }


def test_browser_cookie_selection_rejects_missing_expired_or_ambiguous_seed():
    from src.auth.server_seed import SDKCookie

    valid = {
        "name": "KP_UIDz-ssn", "value": "private-sdk-cookie", "expires": 9000,
        "domain": "k.twitchcdn.net", "path": "/", "secure": True, "httpOnly": True,
    }
    for candidates in (
        [], [{**valid, "domain": "evil.test"}], [{**valid, "path": "/other"}],
        [{**valid, "secure": False}], [{**valid, "httpOnly": False}], [valid, valid],
    ):
        with pytest.raises(SessionError, match="SDK_COOKIE"):
            SDKCookie.from_browser(candidates, now=1000)
    with pytest.raises(SessionError, match="SDK_EXPIRED"):
        SDKCookie.from_browser([{**valid, "expires": 999}], now=1000)
    chosen = SDKCookie.from_browser([valid, {"name": "unrelated", "value": "never-export"}], now=1000)
    assert "never-export" not in json.dumps(chosen.to_dict())
