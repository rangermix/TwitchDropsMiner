"""Optional alert for wanted campaigns that end within 24 hours with unclaimed drops."""

import json
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from src.models.game import Game
from src.services.campaign_alert_service import CampaignEndingAlerts
from src.services.discord_service import DiscordNotifier, _escape_embed
from src.services.telegram_service import TelegramNotifier
from tests.javascript_helpers import NODE
from tests.test_release_notifications import notification_javascript


NOW = datetime.now(timezone.utc)
WEBHOOK = "https://discord.com/api/webhooks/123/token"
GAME = Game({"id": "1", "name": "Game <b>"})


def _campaign(cid="c1", *, ends_in=timedelta(hours=5), game=GAME, earnable=True, wanted=True,
              active=True):
    return SimpleNamespace(
        id=cid, name="Camp *one*", game=game, ends_at=NOW + ends_in, active=active,
        can_earn_within=lambda stamp: earnable,
        has_wanted_unclaimed_benefits=lambda benefits: wanted and benefits == {"BADGE": True},
        mineable_watch_drops=(object(), object()),
    )


def _twitch(*campaigns, enabled=True, wanted=(GAME,)):
    return SimpleNamespace(
        settings=SimpleNamespace(
            notification_events={"campaign_ending": enabled},
            telegram_bot_token="", telegram_chat_id="", discord_webhook_url=WEBHOOK,
            mining_benefits={"BADGE": True},
        ),
        inventory=list(campaigns), wanted_games=list(wanted), diagnostics=None,
    )


@pytest.mark.parametrize("campaign", [
    _campaign(ends_in=timedelta(hours=25)),
    _campaign(game=Game({"id": "2", "name": "Other"})),
    _campaign(earnable=False),
    _campaign(wanted=False),
    _campaign(active=False),
])
def test_only_wanted_mineable_campaigns_ending_soon_are_due(tmp_path, campaign):
    assert CampaignEndingAlerts(_twitch(campaign), tmp_path).due(NOW) == []


@pytest.mark.asyncio
async def test_alerts_once_and_survives_a_restart(tmp_path):
    campaign = _campaign()
    send = AsyncMock(return_value=True)
    with patch.object(DiscordNotifier, "notify_campaign_ending", send):
        await CampaignEndingAlerts(_twitch(campaign), tmp_path).check()
        await CampaignEndingAlerts(_twitch(campaign), tmp_path).check()
    send.assert_awaited_once_with(campaign)
    saved = json.loads((tmp_path / CampaignEndingAlerts.FILENAME).read_text(encoding="utf-8"))
    assert list(saved) == ["c1"]


@pytest.mark.asyncio
async def test_failed_delivery_is_retried_and_disabled_events_send_nothing(tmp_path):
    campaign = _campaign()
    send = AsyncMock(side_effect=[False, RuntimeError("boom"), True])
    with patch.object(DiscordNotifier, "notify_campaign_ending", send):
        await CampaignEndingAlerts(_twitch(campaign, enabled=False), tmp_path).check()
        assert send.await_count == 0
        alerts = CampaignEndingAlerts(_twitch(campaign), tmp_path)
        await alerts.check()
        await alerts.check()
        await alerts.check()
        await alerts.check()
    assert send.await_count == 3


@pytest.mark.asyncio
async def test_alerts_for_several_campaigns_are_sent_together(tmp_path):
    import asyncio

    started = []

    async def slow(_notifier, campaign):
        started.append(campaign.id)
        await asyncio.sleep(0.05)
        return True

    alerts = CampaignEndingAlerts(_twitch(_campaign("a"), _campaign("b"), _campaign("c")), tmp_path)
    with patch.object(DiscordNotifier, "notify_campaign_ending", slow):
        await asyncio.wait_for(alerts.check(), 0.12)
    assert sorted(started) == ["a", "b", "c"]


@pytest.mark.asyncio
async def test_expired_entries_are_pruned_and_a_damaged_file_is_ignored(tmp_path):
    path = tmp_path / CampaignEndingAlerts.FILENAME
    path.write_text("{broken", encoding="utf-8")
    assert CampaignEndingAlerts(_twitch(), tmp_path).due(NOW) == []
    path.write_text(json.dumps({"old": (NOW - timedelta(hours=1)).isoformat()}), encoding="utf-8")
    with patch.object(DiscordNotifier, "notify_campaign_ending", AsyncMock(return_value=True)):
        await CampaignEndingAlerts(_twitch(_campaign()), tmp_path).check()
    assert list(json.loads(path.read_text(encoding="utf-8"))) == ["c1"]


@pytest.mark.asyncio
async def test_messages_escape_twitch_text():
    campaign = _campaign()
    telegram = TelegramNotifier("token", "chat")
    telegram._send_message = AsyncMock(return_value=True)
    assert await telegram.notify_campaign_ending(campaign)
    text = telegram._send_message.await_args.args[0]
    assert "Game &lt;b&gt;" in text and "Drops left:</b> 2" in text
    discord = DiscordNotifier(WEBHOOK)
    discord._send_embed = AsyncMock(return_value=True)
    assert await discord.notify_campaign_ending(campaign)
    sent = _escape_embed(discord._send_embed.await_args.args[0])  # as _send_embed posts it
    fields = {f["name"]: f["value"] for f in sent["fields"]}
    assert fields["Campaign"] == "Camp \\*one\\*", "escaped exactly once"
    assert fields["Ends"].endswith(" UTC")


@pytest.mark.skipif(NODE is None, reason="Node.js is required for frontend tests")
def test_checkbox_renders_and_saves_the_campaign_ending_event():
    script = notification_javascript() + """
const assert = require('node:assert/strict');
const ending = {value: 'campaign_ending', checked: false, disabled: false};
const error = {hidden: true, textContent: ''};
const elements = {'notification-events-error': error, 'notify-campaign_ending': ending};
const document = {getElementById: id => elements[id]};
const state = {settings: {notification_events_revision: 0,
    notification_events: {drop_claimed: true, new_release: false, campaign_ending: true}}, translations: {}};
const reportSettingsError = () => assert.fail('Unexpected save failure');
renderNotificationEvents();
assert.equal(ending.checked, true);
ending.checked = false;
global.fetch = async (_, options) => {
    assert.deepEqual(JSON.parse(options.body), {notification_events: {campaign_ending: false}});
    return {ok: true, json: async () => ({success: true, settings: {notification_events_revision: 1,
        notification_events: {drop_claimed: true, new_release: false, campaign_ending: false}}})};
};
onNotificationEventChange({target: ending}).then(() => {
    assert.equal(ending.checked, false);
    assert.equal(state.settings.notification_events.campaign_ending, false);
}).catch(error => { console.error(error); process.exit(1); });
"""
    result = subprocess.run([NODE, "-"], input=script, text=True, capture_output=True)
    assert result.returncode == 0, result.stderr


def test_markup_and_translations_exist():
    root = Path(__file__).resolve().parents[1]
    html = (root / "web" / "index.html").read_text(encoding="utf-8")
    assert 'id="notify-campaign_ending" value="campaign_ending"' in html
    for path in (root / "lang").glob("*.json"):
        events = json.loads(path.read_text(encoding="utf-8"))["gui"]["settings"]["notification_events"]
        assert events["campaign_ending"].strip(), path.name
