from __future__ import annotations

import asyncio
from collections import OrderedDict, deque
from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

from src.config import State, WebsocketTopic
from src.core.client import Twitch
from src.services.inventory_service import InventoryService
from src.services.watch_service import WatchService
from src.utils import AwaitableValue
from src.web import app as web_app_module


def _make_cache_test_client() -> tuple[Any, dict[str, object], MagicMock, MagicMock]:
    """Build a Twitch instance with real state containers and mocked I/O."""
    twitch: Any = object.__new__(Twitch)
    settings: dict[str, object] = {
        "language": "English",
        "games_to_watch": ["Game A"],
    }
    auth_state = MagicMock(name="auth_state")
    game = SimpleNamespace(name="Game A")
    channel = SimpleNamespace(id=123, name="channel-a", game=game)

    twitch.settings = settings
    twitch._auth_state = auth_state
    twitch.inventory = [object()]
    twitch._campaigns = {"campaign-a": object()}
    twitch._drops = {"drop-a": object()}
    twitch._mnt_triggers = deque([datetime.now(timezone.utc)])
    twitch.wanted_games = [game]
    twitch.channels = OrderedDict([(channel.id, channel)])
    twitch.watching_channel = AwaitableValue()
    twitch.watching_channel.set(channel)
    twitch._watching_restart = asyncio.Event()
    twitch._manual_target_channel = channel
    twitch._manual_target_game = game

    twitch.websocket = MagicMock(name="websocket")
    twitch.gui = SimpleNamespace(
        clear_drop=MagicMock(name="clear_drop"),
        clear_channel_selection=MagicMock(name="clear_channel_selection"),
        broadcast_manual_mode_change=MagicMock(name="broadcast_manual_mode_change"),
        broadcast_wanted_items=MagicMock(name="broadcast_wanted_items"),
        set_games=MagicMock(name="set_games"),
        channels=SimpleNamespace(
            clear=MagicMock(name="channels_clear"),
            clear_watching=MagicMock(name="clear_watching"),
        ),
        inv=SimpleNamespace(clear=MagicMock(name="inventory_clear")),
        progress=SimpleNamespace(stop_timer=MagicMock(name="stop_timer")),
    )
    twitch._watch_service = WatchService(twitch)

    maintenance_task = MagicMock(name="maintenance_task")
    maintenance_task.done.return_value = False
    twitch._mnt_task = maintenance_task
    return twitch, settings, auth_state, maintenance_task


def test_clear_cached_state_discards_only_derived_runtime_state():
    twitch, settings, auth_state, maintenance_task = _make_cache_test_client()
    settings_snapshot = settings.copy()
    original_settings = twitch.settings
    original_auth_state = twitch._auth_state

    InventoryService(twitch).clear_cached_state()

    assert twitch.inventory == []
    assert twitch._campaigns == {}
    assert twitch._drops == {}
    assert list(twitch._mnt_triggers) == []
    assert twitch.wanted_games == []
    assert twitch.channels == OrderedDict()
    assert not twitch.watching_channel.has_value()
    assert twitch._watching_restart.is_set()
    assert twitch._manual_target_channel is None
    assert twitch._manual_target_game is None
    assert twitch._mnt_task is None

    twitch.gui.clear_drop.assert_called_once_with()
    twitch.gui.channels.clear_watching.assert_called_once_with()
    twitch.gui.progress.stop_timer.assert_called_once_with()
    twitch.gui.channels.clear.assert_called_once_with()
    twitch.gui.clear_channel_selection.assert_called_once_with()
    twitch.gui.inv.clear.assert_called_once_with()
    twitch.gui.set_games.assert_called_once_with(set())
    twitch.gui.broadcast_wanted_items.assert_called_once_with()
    twitch.gui.broadcast_manual_mode_change.assert_called_once_with({"active": False})
    maintenance_task.cancel.assert_called_once_with()
    twitch.websocket.remove_topics.assert_called_once_with(
        [
            WebsocketTopic.as_str("Channel", "StreamState", 123),
            WebsocketTopic.as_str("Channel", "StreamUpdate", 123),
        ]
    )

    assert twitch.settings is original_settings
    assert twitch.settings == settings_snapshot
    assert twitch._auth_state is original_auth_state
    assert auth_state.mock_calls == []


@pytest.mark.asyncio
async def test_inventory_replacement_removes_stale_campaign_lookup_entries():
    twitch = SimpleNamespace(
        _drops={"stale-drop": object()},
        _campaigns={"stale-campaign": object()},
        inventory=[object()],
        _mnt_triggers=deque([datetime.now(timezone.utc)]),
        _mnt_task=None,
        _state=State.IDLE,
        gui=SimpleNamespace(
            status=SimpleNamespace(update=MagicMock()),
            inv=SimpleNamespace(clear=MagicMock(), add_campaign=AsyncMock()),
        ),
        gql_request=AsyncMock(
            side_effect=[
                {
                    "data": {
                        "currentUser": {
                            "inventory": {
                                "dropCampaignsInProgress": [],
                                "gameEventDrops": [],
                            }
                        }
                    }
                },
                {"data": {"currentUser": {"dropCampaigns": []}}},
            ]
        ),
        _maintenance_service=SimpleNamespace(run_maintenance_task=AsyncMock()),
    )

    await InventoryService(cast(Twitch, twitch)).fetch_inventory()
    assert twitch._campaigns == {}
    assert twitch._drops == {}
    assert twitch.inventory == []
    assert list(twitch._mnt_triggers) == []
    twitch.gui.inv.clear.assert_called_once_with()
    await twitch._mnt_task


def _make_refresh_test_client(state: State = State.IDLE) -> Twitch:
    twitch = object.__new__(Twitch)
    twitch._state = state
    twitch._state_change = asyncio.Event()
    twitch._inventory_refresh_pending = False
    twitch._clear_cache_pending = False
    return twitch


def test_refresh_requests_coalesce_and_clear_cache_keeps_priority():
    twitch = _make_refresh_test_client(State.CHANNEL_SWITCH)

    assert twitch.request_inventory_refresh()
    assert twitch.request_inventory_refresh(clear_cache=True)
    assert twitch.request_inventory_refresh()

    assert twitch._inventory_refresh_pending is True
    assert twitch._clear_cache_pending is True
    assert twitch._state_change.is_set()

    # A normal in-flight state transition may run after the request. The queued
    # refresh must still win when the state machine next checks pending work.
    twitch.change_state(State.IDLE)
    twitch._activate_pending_inventory_refresh()
    assert twitch._state is State.INVENTORY_FETCH
    assert twitch._clear_cache_pending is True


def test_refresh_request_is_rejected_after_shutdown_starts():
    twitch = _make_refresh_test_client(State.EXIT)

    assert twitch.request_inventory_refresh(clear_cache=True) is False
    assert twitch._inventory_refresh_pending is False
    assert twitch._clear_cache_pending is False
    assert not twitch._state_change.is_set()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("endpoint", "expected_kwargs"),
    [
        (web_app_module.trigger_reload, {}),
        (web_app_module.clear_all_cache, {"clear_cache": True}),
    ],
)
async def test_refresh_endpoints_delegate(endpoint, expected_kwargs):
    twitch_client = MagicMock()
    twitch_client.request_inventory_refresh.return_value = True

    with patch.object(web_app_module, "twitch_client", twitch_client):
        assert await endpoint() == {"success": True}

    twitch_client.request_inventory_refresh.assert_called_once_with(**expected_kwargs)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "endpoint", [web_app_module.trigger_reload, web_app_module.clear_all_cache]
)
async def test_refresh_endpoints_return_503_without_client(endpoint):
    with (
        patch.object(web_app_module, "twitch_client", None),
        pytest.raises(HTTPException) as exc_info,
    ):
        await endpoint()

    assert exc_info.value.status_code == 503
    assert exc_info.value.detail == "Twitch client not initialized"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("endpoint", "expected_kwargs"),
    [
        (web_app_module.trigger_reload, {}),
        (web_app_module.clear_all_cache, {"clear_cache": True}),
    ],
)
async def test_refresh_endpoints_return_409_during_shutdown(endpoint, expected_kwargs):
    twitch_client = MagicMock()
    twitch_client.request_inventory_refresh.return_value = False

    with (
        patch.object(web_app_module, "twitch_client", twitch_client),
        pytest.raises(HTTPException) as exc_info,
    ):
        await endpoint()

    assert exc_info.value.status_code == 409
    assert exc_info.value.detail == "Twitch client is shutting down"
    twitch_client.request_inventory_refresh.assert_called_once_with(**expected_kwargs)
