"""Renewal scheduling, destination restrictions and scoped transport."""

import asyncio
import json
from unittest.mock import AsyncMock

import pytest
from aiohttp import web

from src.auth.session_bundle import SessionBundle, SessionError
from tests.test_imported_session import bundle_data


def connection(endpoint='https://tdm.test/api/session/renew'):
    return {'version': 1, 'endpoint': endpoint, 'credential': 'a' * 43, 'user_id': 42}


def test_connection_file_destination_and_secret_boundaries():
    from src.auth.session_renewal import RenewalConnection

    assert 'a' * 43 not in repr(RenewalConnection.from_dict(connection()))
    for address in ('http://tdm.test/api/session/renew', 'https://user:secret@tdm.test/api/session/renew',
                    'https://tdm.test/api/session/renew?secret=x', 'https://tdm.test/api/session/renew#secret',
                    'https://tdm.test/api/settings', 'http://127.1/api/session/renew'):
        with pytest.raises(SessionError, match='CONNECTION'):
            RenewalConnection.from_dict(connection(address))
    for address in ('http://localhost:8080/api/session/renew', 'http://127.0.0.1:8080/api/session/renew',
                    'http://[::1]:8080/api/session/renew'):
        RenewalConnection.from_dict(connection(address))


@pytest.mark.asyncio
async def test_loop_renews_before_expiry_and_retries_without_reusing_bundle():
    from src.auth.session_renewal import RenewalLoop

    clock = [1000]
    exporter = AsyncMock()
    exporter.capture.side_effect = [SessionError('BROWSER_PROTOCOL'),
                                   SessionBundle.from_dict(bundle_data(), now=1000),
                                   SessionBundle.from_dict(bundle_data(4300, 'new'), now=4300)]
    sender = AsyncMock()
    sender.send.side_effect = [{'expires_at': 4600, 'generation': 1}, {'expires_at': 7900, 'generation': 2}]
    sleeps, events = [], []

    async def sleep(delay):
        sleeps.append(delay)
        clock[0] += delay
        if len(sleeps) == 3:
            raise asyncio.CancelledError

    loop = RenewalLoop(exporter, sender, clock=lambda: clock[0], sleep=sleep, report=events.append)
    with pytest.raises(asyncio.CancelledError):
        await loop.run()
    assert sleeps == [5, 3295, 3300]
    assert sender.send.await_count == 2
    assert exporter.capture.await_count == 3
    assert 'test-token' not in json.dumps(events)
    assert [event['event'] for event in events] == ['retry', 'renewed', 'renewed']


@pytest.mark.asyncio
async def test_revocation_stops_loop_and_does_not_retry():
    from src.auth.session_renewal import RenewalLoop

    exporter, sender, sleep = AsyncMock(), AsyncMock(), AsyncMock()
    exporter.capture.return_value = SessionBundle.from_dict(bundle_data(), now=1000)
    sender.send.side_effect = SessionError('PAIRING')
    loop = RenewalLoop(exporter, sender, clock=lambda: 1000, sleep=sleep, report=lambda event: None)
    with pytest.raises(SessionError, match='PAIRING'):
        await loop.run()
    sleep.assert_not_called()


@pytest.mark.asyncio
async def test_real_sender_scoped_headers_identity_and_redirect_refusal():
    from src.auth.session_renewal import RenewalConnection, RenewalSender

    seen = []
    mode = ['ok']

    async def receive(request):
        seen.append((dict(request.headers), await request.json()))
        if mode[0] == 'redirect':
            return web.Response(status=307, headers={'Location': '/sink'})
        data = {'success': True, 'session': {
            'state': 'ready', 'user_id': 42 if mode[0] == 'ok' else 43,
            'expires_at': 4600, 'generation': 2,
        }}
        raw = json.dumps(data).encode()
        response = web.StreamResponse(headers={'Content-Type': 'application/json'})
        await response.prepare(request)
        await response.write(raw[:12])
        await asyncio.sleep(0.01)
        await response.write(raw[12:])
        await response.write_eof()
        return response

    app = web.Application()
    app.router.add_post('/api/session/renew', receive)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, '127.0.0.1', 0)
    await site.start()
    address = f'http://127.0.0.1:{site._server.sockets[0].getsockname()[1]}/api/session/renew'
    sender = RenewalSender(RenewalConnection.from_dict(connection(address)))
    bundle = SessionBundle.from_dict(bundle_data(), now=1000)
    try:
        assert (await sender.send(bundle))['generation'] == 2
        assert seen[0][0]['Authorization'] == 'Bearer ' + 'a' * 43
        assert seen[0][0]['X-TDM-Request'] == '1'
        assert 'Cookie' not in seen[0][0]
        assert seen[0][1] == bundle_data()
        mode[0] = 'account'
        with pytest.raises(SessionError, match='ACCOUNT_MISMATCH'):
            await sender.send(bundle)
        mode[0] = 'redirect'
        with pytest.raises(SessionError, match='DESTINATION'):
            await sender.send(bundle)
        assert len(seen) == 3
    finally:
        await runner.cleanup()


@pytest.mark.asyncio
async def test_short_remaining_validity_renews_before_expiry():
    from src.auth.session_renewal import RenewalLoop

    clock = [1000]
    exporter, sender = AsyncMock(), AsyncMock()
    data = bundle_data()
    data['expires_at'] = 1040
    exporter.capture.return_value = SessionBundle.from_dict(data, now=1000)

    async def send(bundle):
        clock[0] += 15
        return {'expires_at': 1040, 'generation': 1}

    sender.send.side_effect = send
    delays = []

    async def sleep(delay):
        delays.append(delay)
        raise asyncio.CancelledError

    loop = RenewalLoop(exporter, sender, clock=lambda: clock[0], sleep=sleep, report=lambda e: None)
    with pytest.raises(asyncio.CancelledError):
        await loop.run()
    assert 0 < delays[0] < 25
