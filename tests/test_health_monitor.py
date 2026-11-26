"""Optional alerts when the miner needs a Twitch login or stops mining."""

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from src.services.discord_service import DiscordNotifier
from src.services.health_monitor import HealthMonitor
from src.services.telegram_service import TelegramNotifier
from src.utils.async_helpers import AwaitableValue


WEBHOOK = "https://discord.com/api/webhooks/123/token"
LOGIN, STALL = HealthMonitor.LOGIN_DELAY, HealthMonitor.STALL_DELAY


def _twitch(*, logged_in=True, mining=True, enabled=True):
    watching = AwaitableValue()
    return SimpleNamespace(
        settings=SimpleNamespace(
            notification_events={"mining_problems": enabled},
            telegram_bot_token="", telegram_chat_id="", discord_webhook_url=WEBHOOK,
        ),
        is_mining_time=lambda: mining, _auth_state=SimpleNamespace(logged_in=logged_in),
        watching_channel=watching, _watch_service=SimpleNamespace(last_watch_success=None),
        diagnostics=None,
    )


@pytest.fixture
def sent():
    send = AsyncMock(return_value=True)
    with patch.object(DiscordNotifier, "notify_health", send):
        yield send


def _calls(send):
    return [(call.args[0], call.kwargs["resolved"]) for call in send.await_args_list]


@pytest.mark.asyncio
async def test_login_alert_after_30_minutes_then_one_recovery(sent):
    twitch = _twitch(logged_in=False)
    monitor = HealthMonitor(twitch)
    await monitor.check(0)
    await monitor.check(LOGIN - 1)
    assert sent.await_count == 0
    await monitor.check(LOGIN)
    await monitor.check(LOGIN + 60)
    twitch._auth_state.logged_in = True
    await monitor.check(LOGIN + 120)
    await monitor.check(LOGIN + 180)
    assert _calls(sent) == [
        (HealthMonitor.MESSAGES["login"][0], False), (HealthMonitor.MESSAGES["login"][1], True),
    ]


@pytest.mark.asyncio
async def test_stall_counts_from_selection_or_last_success(sent):
    twitch = _twitch()
    monitor = HealthMonitor(twitch)
    channel = SimpleNamespace(id=1)
    twitch.watching_channel.set(channel)
    await monitor.check(100)  # selection time
    twitch._watch_service.last_watch_success = 100 + STALL - 60
    await monitor.check(100 + STALL)
    assert sent.await_count == 0
    await monitor.check(100 + 2 * STALL - 60)
    assert _calls(sent) == [(HealthMonitor.MESSAGES["stalled"][0], False)]
    twitch._watch_service.last_watch_success = 100 + 2 * STALL
    await monitor.check(100 + 2 * STALL + 1)
    assert _calls(sent)[-1] == (HealthMonitor.MESSAGES["stalled"][1], True)


@pytest.mark.asyncio
async def test_switching_channels_restarts_the_stall_timer(sent):
    twitch = _twitch()
    monitor = HealthMonitor(twitch)
    twitch.watching_channel.set(SimpleNamespace(id=1))
    await monitor.check(0)
    twitch.watching_channel.set(SimpleNamespace(id=2))
    await monitor.check(STALL - 1)
    await monitor.check(2 * STALL - 2)
    assert sent.await_count == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("kwargs", [{"mining": False}, {"enabled": False}])
async def test_closed_hours_or_disabled_event_send_nothing(sent, kwargs):
    twitch = _twitch(logged_in=False, **kwargs)
    twitch.watching_channel.set(SimpleNamespace(id=1))
    monitor = HealthMonitor(twitch)
    await monitor.check(0)
    await monitor.check(10 * LOGIN)
    assert sent.await_count == 0


@pytest.mark.asyncio
async def test_closing_hours_neither_recovers_nor_counts_toward_an_alert(sent):
    twitch = _twitch(logged_in=False)
    monitor = HealthMonitor(twitch)
    await monitor.check(0)
    await monitor.check(LOGIN)
    twitch.is_mining_time = lambda: False
    await monitor.check(LOGIN + 60)
    assert _calls(sent) == [(HealthMonitor.MESSAGES["login"][0], False)]
    twitch.is_mining_time = lambda: True
    twitch._auth_state.logged_in = True
    await monitor.check(LOGIN + 120)
    assert _calls(sent)[-1] == (HealthMonitor.MESSAGES["login"][1], True)
    # An unalerted problem restarts its timer after closed hours.
    twitch._auth_state.logged_in = False
    await monitor.check(10 * LOGIN)
    twitch.is_mining_time = lambda: False
    await monitor.check(11 * LOGIN)
    twitch.is_mining_time = lambda: True
    await monitor.check(11 * LOGIN + 60)
    assert sent.await_count == 2


def _stalled():
    twitch = _twitch()
    monitor = HealthMonitor(twitch)
    twitch.watching_channel.set(SimpleNamespace(id=1))
    return twitch, monitor


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["reloaded", "offline", "logged_out", "closed"])
async def test_a_stall_never_reports_a_false_recovery(sent, change):
    twitch, monitor = _stalled()
    await monitor.check(0)
    await monitor.check(STALL)
    assert _calls(sent) == [(HealthMonitor.MESSAGES["stalled"][0], False)]
    if change == "reloaded":  # CHANNELS_FETCH re-links a new object for the same channel
        twitch.watching_channel.set(SimpleNamespace(id=1))
    elif change == "offline":
        twitch.watching_channel.clear()
    elif change == "logged_out":
        twitch._auth_state.logged_in = False
    else:
        twitch.is_mining_time = lambda: False
        await monitor.check(STALL + 30)
        twitch.is_mining_time = lambda: True
    await monitor.check(STALL + 60)
    assert all(not resolved for _, resolved in _calls(sent)), _calls(sent)
    if change == "reloaded":
        assert sent.await_count == 1, "still the same stall: no repeated alert"


@pytest.mark.asyncio
async def test_failed_alert_is_retried(sent):
    sent.side_effect = [False, RuntimeError("down"), True]
    monitor = HealthMonitor(_twitch(logged_in=False))
    for now in (0, LOGIN, LOGIN + 60, LOGIN + 120, LOGIN + 180):
        await monitor.check(now)
    assert sent.await_count == 3


@pytest.mark.asyncio
async def test_messages_are_fixed_text():
    telegram = TelegramNotifier("token", "chat")
    telegram._send_message = AsyncMock(return_value=True)
    assert await telegram.notify_health("Mining <stalled>", resolved=False)
    assert telegram._send_message.await_args.args[0] == "⚠️ Mining &lt;stalled&gt;"
    discord = DiscordNotifier(WEBHOOK)
    discord._send_embed = AsyncMock(return_value=True)
    assert await discord.notify_health("Mining resumed", resolved=True)
    assert discord._send_embed.await_args.args[0]["description"] == "Mining resumed"


def test_markup_and_translations_exist():
    root = Path(__file__).resolve().parents[1]
    html = (root / "web" / "index.html").read_text(encoding="utf-8")
    assert 'id="notify-mining_problems" value="mining_problems"' in html
    for path in (root / "lang").glob("*.json"):
        events = json.loads(path.read_text(encoding="utf-8"))["gui"]["settings"]["notification_events"]
        assert events["mining_problems"].strip(), path.name
