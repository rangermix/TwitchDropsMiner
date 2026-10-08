"""The playlist polling cadence must not turn seconds into credited minutes."""

import asyncio
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from src.services.watch_service import WatchService
from src.utils.async_helpers import AwaitableValue


@pytest.mark.asyncio
@pytest.mark.parametrize('current_session', [True, False])
async def test_playlist_polling_preserves_minute_progress_cadence(current_session):
    clock = [0.0]
    watches = []
    progress_checks = []
    bumps = []
    channel = MagicMock()
    channel.online = True
    channel.id = 123

    async def send_watch():
        watches.append(clock[0])
        return True

    channel.send_watch = send_watch
    selected = AwaitableValue()
    selected.set(channel)
    drop = MagicMock()
    drop.can_earn.return_value = True
    campaign = MagicMock()
    campaign.bump_minutes.side_effect = lambda watched: bumps.append(clock[0])

    async def gql_request(operation):
        progress_checks.append(clock[0])
        return {'data': {'currentUser': {'dropCurrentSession': (
            {'dropID': 'drop', 'currentMinutesWatched': 1} if current_session else None
        )}}}

    twitch = SimpleNamespace(
        watching_channel=selected,
        gui=SimpleNamespace(progress=SimpleNamespace(minute_almost_done=lambda: True)),
        gql_request=gql_request,
        _drops={'drop': drop},
        _inventory_service=SimpleNamespace(get_active_campaign=lambda watched: campaign),
    )
    service = WatchService(twitch)

    async def advance(delay):
        assert delay >= 0
        clock[0] += delay
        if clock[0] >= 130:
            raise asyncio.CancelledError

    with (
        patch('src.services.watch_service.monotonic', side_effect=lambda: clock[0], create=True),
        patch('src.services.watch_service.asyncio.sleep', side_effect=advance),
        patch.object(service, 'watch_sleep', side_effect=advance),
        pytest.raises(asyncio.CancelledError),
    ):
        await service.watch_loop()

    assert len(watches) >= 10
    assert all(b - a <= 15 for a, b in zip(watches, watches[1:], strict=False))
    assert progress_checks
    assert all(b - a >= 59 for a, b in zip(progress_checks, progress_checks[1:], strict=False))
    assert len(progress_checks) <= 3
    if current_session:
        assert not bumps
    else:
        assert bumps == progress_checks


@pytest.mark.asyncio
async def test_blocked_progress_query_is_drained_and_playlist_polling_resumes(monkeypatch):
    clock = [0.0]
    watches = []
    bumps = []
    entered, release, drained = asyncio.Event(), asyncio.Event(), asyncio.Event()
    channel = MagicMock()
    channel.online = True
    channel.id = 123

    async def send_watch():
        watches.append(clock[0])
        return True

    async def gql_request(operation):
        entered.set()
        try:
            await release.wait()
        finally:
            drained.set()

    channel.send_watch = send_watch
    selected = AwaitableValue()
    selected.set(channel)
    campaign = MagicMock()
    campaign.bump_minutes.side_effect = lambda watched: bumps.append(clock[0])
    twitch = SimpleNamespace(
        watching_channel=selected,
        gui=SimpleNamespace(progress=SimpleNamespace(minute_almost_done=lambda: True)),
        gql_request=gql_request,
        _drops={},
        _inventory_service=SimpleNamespace(get_active_campaign=lambda watched: campaign),
    )
    service = WatchService(twitch)

    async def advance(delay):
        clock[0] += delay
        if clock[0] >= 35:
            raise asyncio.CancelledError

    loop = asyncio.get_running_loop()
    original_call_at = loop.call_at
    scheduled = []

    def capture_timeout(when, callback, *args, context=None):
        handle = original_call_at(when, callback, *args, context=context)
        if isinstance(getattr(callback, '__self__', None), asyncio.Timeout):
            scheduled.append((callback, args, handle))
        return handle

    monkeypatch.setattr(loop, 'call_at', capture_timeout)
    with (
        patch('src.services.watch_service.monotonic', side_effect=lambda: clock[0]),
        patch.object(service, 'watch_sleep', side_effect=advance),
    ):
        task = asyncio.create_task(service.watch_loop())
        try:
            await entered.wait()
            active = [deadline for deadline in scheduled if not deadline[2].cancelled()]
            assert active, 'CurrentDrop query must not stall HLS polling indefinitely'
            callback, args, handle = active[0]
            handle.cancel()
            clock[0] += 5
            callback(*args)
            with pytest.raises(asyncio.CancelledError):
                await task
            assert drained.is_set()
            assert 25 in watches
            assert bumps == [25]
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
