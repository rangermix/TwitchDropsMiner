"""Checks for the streamlink-backed client-integrity support."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock

from src.api.gql_client import GQLClient
from src.auth import integrity
from src.auth.auth_state import _AuthState
from src.config.client_info import ClientType
from tests.test_watch_drop_filtering import _campaign, _drop


def _auth_state() -> _AuthState:
    state = _AuthState(MagicMock())
    state.access_token = "tok"
    state.device_id = "dev"
    state.session_id = "sess"
    return state


def test_integrity_header_only_when_asked_and_held():
    state = _auth_state()

    # no token held -> never sent, even when requested
    assert "Client-Integrity" not in state.headers(gql=True, integrity=True)

    state.integrity_token = "v4.local.abc"
    # held but not requested -> still not sent, so ungated calls stay untouched
    assert "Client-Integrity" not in state.headers(gql=True)
    assert state.headers(gql=True, integrity=True)["Client-Integrity"] == "v4.local.abc"


def test_expiry_drives_refresh():
    state = _auth_state()
    assert state.integrity_expired  # nothing held yet

    state.integrity_expires = datetime.now(timezone.utc) + timedelta(minutes=30)
    assert not state.integrity_expired

    state.integrity_expires = datetime.now(timezone.utc) - timedelta(seconds=1)
    assert state.integrity_expired


def test_refresh_stores_token_and_renews_early(monkeypatch):
    state = _auth_state()
    expires_at = datetime.now(timezone.utc) + timedelta(hours=1)

    async def fake_acquire(access_token, device_id):
        assert (access_token, device_id) == ("tok", "dev")
        return "v4.local.xyz", expires_at

    monkeypatch.setattr(integrity, "acquire", fake_acquire)
    asyncio.run(state._refresh_integrity())

    assert state.integrity_token == "v4.local.xyz"
    # renewed ahead of the real expiry, so a request never races it
    assert state.integrity_expires == expires_at - integrity.RENEW_MARGIN
    assert not state.integrity_expired


def test_failure_is_not_fatal_and_backs_off(monkeypatch):
    state = _auth_state()
    calls = 0

    async def failing_acquire(access_token, device_id):
        nonlocal calls
        calls += 1
        return None

    monkeypatch.setattr(integrity, "acquire", failing_acquire)

    asyncio.run(state._refresh_integrity())
    assert calls == 1
    assert state.integrity_token is None  # miner keeps running without it

    # second attempt inside the cooldown must not respawn Chromium
    asyncio.run(state._refresh_integrity())
    assert calls == 1

    state._integrity_failed_at = (
        datetime.now(timezone.utc) - integrity.FAILURE_COOLDOWN - timedelta(seconds=1)
    )
    asyncio.run(state._refresh_integrity())
    assert calls == 2


def test_integrity_failure_degrades_instead_of_crashing():
    # What Twitch answers for the WEB client when the catalog is gated.
    gated = {
        "data": {"currentUser": {"id": "1", "dropCampaigns": None}},
        "errors": [{
            "message": "failed integrity check",
            "path": ["currentUser", "dropCampaigns"],
            "extensions": {"code": "IntegrityCheckFailed"},
        }],
    }

    class FakeHTTP:
        @asynccontextmanager
        async def request(self, *args, **kwargs):
            response = MagicMock()
            response.json = AsyncMock(return_value=gated)
            yield response

    state = _auth_state()
    state.validate = AsyncMock(return_value=state)  # type: ignore[method-assign]
    client = GQLClient(FakeHTTP(), state, ClientType.WEB)  # type: ignore[arg-type]

    # Must return, not raise: the miner keeps running on its inventory.
    result = asyncio.run(client.request({"operationName": "Campaigns"}, integrity=True))
    assert result["data"]["currentUser"]["dropCampaigns"] is None


def test_claim_sends_integrity():
    # claimDropRewards is gated: without the header every claim fails.
    campaign = _campaign("claim", [_drop("drop-1", "Watch", 30)])
    drop = campaign.timed_drops["drop-1"]
    drop.update_claim("inst-1")
    twitch = campaign._twitch
    twitch.gql_request = AsyncMock(
        return_value={"data": {"claimDropRewards": {"status": "ELIGIBLE_FOR_ALL"}}}
    )

    assert asyncio.run(drop._claim())
    assert twitch.gql_request.await_args.kwargs.get("integrity") is True


if __name__ == "__main__":
    import pytest

    raise SystemExit(pytest.main([__file__, "-q"]))
