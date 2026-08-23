from unittest.mock import MagicMock

from src.config import State
from src.core.client import Twitch


def _client(*, state: State, inventory_loaded: bool) -> Twitch:
    client = object.__new__(Twitch)
    client._state = state
    client._state_change = MagicMock()
    client._games_update_pending = False
    client._inventory_loaded = inventory_loaded
    return client


def test_pending_games_update_wins_after_an_active_state_step_transitions():
    client = _client(state=State.CHANNELS_FETCH, inventory_loaded=True)

    assert client.request_games_update() is True
    assert client._state is State.CHANNELS_FETCH
    assert client._games_update_pending is True

    # Simulate the active state step choosing its normal next state after the
    # request arrived. The queued policy update must still take priority.
    client.change_state(State.CHANNEL_SWITCH)
    client._activate_pending_games_update()

    assert client._state is State.GAMES_UPDATE
    assert client._games_update_pending is True
    assert client._state_change.set.call_count == 3


def test_pending_games_update_cannot_bypass_the_initial_inventory_fetch():
    client = _client(state=State.INVENTORY_FETCH, inventory_loaded=False)

    assert client.request_games_update() is True
    client._activate_pending_games_update()

    assert client._state is State.INVENTORY_FETCH
    assert client._games_update_pending is True

    client._inventory_loaded = True
    client._activate_pending_games_update()

    # Even after the fetch sets its loaded marker, it owns INVENTORY_FETCH until
    # that step chooses a normal successor. The pending request wins immediately
    # after that transition.
    assert client._state is State.INVENTORY_FETCH
    client.change_state(State.CHANNELS_CLEANUP)
    client._activate_pending_games_update()

    assert client._state is State.GAMES_UPDATE
    assert client._games_update_pending is True


def test_games_update_request_is_rejected_after_shutdown_starts():
    client = _client(state=State.EXIT, inventory_loaded=True)

    assert client.request_games_update() is False
    assert client._games_update_pending is False
    client._state_change.set.assert_not_called()
