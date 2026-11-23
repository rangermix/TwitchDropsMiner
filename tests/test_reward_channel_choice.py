"""Channel choice and directory lookups when a game has reward and regular campaigns."""

import asyncio

import pytest

from src.config.settings import default_settings
from src.models.game import Game
from tests.test_reward_campaigns import GTA, _build, _channel, _regular
from tests.test_watched_channel_recovery import OFFLINE, _reloading_miner


def _live(client, channel_id, viewers, *, drops_enabled):
    channel = _channel(client, drops_enabled=drops_enabled, channel_id=channel_id)
    channel._stream.viewers = viewers
    return channel


def _fetching_client(tmp_path, monkeypatch, *, regular, reward):
    """CHANNELS_FETCH then CHANNEL_SWITCH for GTA; the watched channel 1 has gone offline."""
    client, _watched = _reloading_miner(tmp_path, monkeypatch, discovered=[], stream_status=[OFFLINE])
    client.settings.mining_benefits = default_settings["mining_benefits"].copy()
    client.wanted_games = [Game(GTA)]
    client.inventory = [
        *([_regular(client, game=GTA)] if regular else []),
        *([_build(twitch=client)] if reward else []),
    ]
    calls = []

    async def live_streams(game, *, drops_enabled=True, **_):
        calls.append(drops_enabled)
        if drops_enabled:
            return [_live(client, 2, 100, drops_enabled=True), _live(client, 4, 500, drops_enabled=True)]
        # The unfiltered directory also lists the drops-enabled channels, without the flag.
        return [_live(client, 3, 1000, drops_enabled=False), _live(client, 2, 100, drops_enabled=False)]

    client.get_live_streams = live_streams
    return client, calls


async def _select(client):
    try:
        await asyncio.wait_for(client._run(), 1)
    finally:
        if client._watching_task is not None:
            client._watching_task.cancel()
    return client.watching_channel.get_with_default(None).id


@pytest.mark.asyncio
async def test_mixed_game_picks_a_drops_enabled_channel_over_a_bigger_flagless_one(tmp_path, monkeypatch):
    client, _calls = _fetching_client(tmp_path, monkeypatch, regular=True, reward=True)
    assert await _select(client) == 4


@pytest.mark.asyncio
async def test_regular_only_game_still_picks_the_top_drops_enabled_channel(tmp_path, monkeypatch):
    client, _calls = _fetching_client(tmp_path, monkeypatch, regular=True, reward=False)
    assert await _select(client) == 4


def test_mixed_game_switches_away_from_a_healthy_flagless_channel(tmp_path, monkeypatch):
    client, _calls = _fetching_client(tmp_path, monkeypatch, regular=True, reward=True)
    flagless = _live(client, 3, 1000, drops_enabled=False)
    enabled = _live(client, 2, 100, drops_enabled=True)
    client.watching_channel.set(flagless)
    assert client.can_watch(flagless) and client.can_watch(enabled)
    assert client.should_switch(enabled)
    assert not client.should_switch(_live(client, 5, 50, drops_enabled=False))


@pytest.mark.asyncio
async def test_reward_only_game_uses_one_unfiltered_lookup_and_the_top_viewer_channel(tmp_path, monkeypatch):
    client, calls = _fetching_client(tmp_path, monkeypatch, regular=False, reward=True)
    assert await _select(client) == 3
    assert calls == [False]


@pytest.mark.asyncio
@pytest.mark.parametrize(("regular", "reward", "expected"), [
    (True, True, [True, False]),
    (True, False, [True]),
])
async def test_regular_campaign_keeps_the_drops_enabled_lookup(tmp_path, monkeypatch, regular, reward, expected):
    client, calls = _fetching_client(tmp_path, monkeypatch, regular=regular, reward=reward)
    await _select(client)
    assert calls == expected
    # The drops-enabled Channel object found first is the one that is kept.
    assert client.channels[2].drops_enabled is True


@pytest.mark.parametrize(("reward", "expected"), [(False, (0, 0)), (True, (-2, -1))])
def test_campaign_count_only_ranks_channels_in_games_with_a_reward_campaign(
    tmp_path, monkeypatch, reward, expected
):
    # Without a reward campaign the order stays mining priority, ACL, then viewers.
    client, _calls = _fetching_client(tmp_path, monkeypatch, regular=True, reward=reward)
    enabled = _live(client, 2, 100, drops_enabled=True)
    flagless = _live(client, 3, 1000, drops_enabled=False)
    service = client._channel_service
    assert (
        service.get_selection_priority(enabled)[2],
        service.get_selection_priority(flagless)[2],
    ) == expected
