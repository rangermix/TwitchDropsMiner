"""New-release notifications and per-event notification choices."""

import asyncio
import io
import json
import subprocess
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.config.settings import NotificationEventSettings, Settings
from src.services.discord_service import DiscordNotifier
from src.services.release_service import Release, ReleaseService
from src.services.telegram_service import TelegramNotifier
from src.web.managers.console import ConsoleOutputManager
from src.web.managers.settings import SettingsManager
from tests.javascript_helpers import APP_JS, NODE, extract_javascript_function
from tests.test_watch_drop_filtering import _campaign, _drop


RELEASE_URL = "https://github.com/rangermix/TwitchDropsMiner/releases/tag/v9.0.0"
WEBHOOK = "https://discord.com/api/webhooks/111/aaa_bbb"


def github(payload, status=200):
    """Patch aiohttp so GitHub answers with a JSON payload."""
    stream = io.BytesIO(json.dumps(payload).encode())
    response = SimpleNamespace(status=status, content=SimpleNamespace(
        read=AsyncMock(side_effect=lambda size: stream.read(size))))
    context = MagicMock()
    context.__aenter__ = AsyncMock(return_value=response)
    context.__aexit__ = AsyncMock(return_value=False)
    session = MagicMock()
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock(return_value=False)
    session.get.return_value = context
    return patch("aiohttp.ClientSession", lambda: session)


def test_versions_compare_numerically_and_reject_other_tags():
    assert ReleaseService.is_newer("2.10.0", "2.9.0")
    assert ReleaseService.is_newer("v3.0.0", "2.99.99")
    assert not ReleaseService.is_newer("2.5.0", "2.5.0")
    assert not ReleaseService.is_newer("2.4.9", "2.5.0")
    for tag in ("2.6", "2.6.0-beta", "latest", None, " 2.6.0", "2.6.0\n"):
        assert not ReleaseService.is_newer(tag, "2.5.0")


@pytest.mark.asyncio
async def test_fetch_latest_accepts_only_this_repository_release_page():
    with github({"tag_name": "v9.0.0", "html_url": RELEASE_URL}):
        assert await ReleaseService.fetch_latest() == Release("9.0.0", RELEASE_URL)
    with github({"tag_name": "v9.0.0", "html_url": "https://evil.example/<b>"}):
        assert await ReleaseService.fetch_latest() == Release("9.0.0", ReleaseService.RELEASES_URL)
    with github({"tag_name": "<b>9</b>"}):
        assert await ReleaseService.fetch_latest() is None
    with github({"message": "rate limited"}, status=403):
        assert await ReleaseService.fetch_latest() is None
    with github({"tag_name": "v9.0.0", "body": "x" * 300_000}):
        assert await ReleaseService.fetch_latest() is None


class TestReleaseCheck(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.settings = SimpleNamespace(
            notification_events={"drop_claimed": True, "new_release": True},
            telegram_bot_token="token", telegram_chat_id="42", discord_webhook_url=WEBHOOK,
            last_notified_release="", save=MagicMock(),
        )
        self.service = ReleaseService(SimpleNamespace(settings=self.settings, diagnostics=None))
        self.fetch = self.enterContext(patch.object(
            ReleaseService, "fetch_latest", new=AsyncMock(return_value=Release("9.0.0", RELEASE_URL))))
        self.telegram = self.enterContext(
            patch.object(TelegramNotifier, "_send_message", new=AsyncMock(return_value=True)))
        self.discord = self.enterContext(
            patch.object(DiscordNotifier, "_send_embed", new=AsyncMock(return_value=True)))

    async def test_newer_release_is_announced_once_on_every_channel(self):
        self.assertTrue(await self.service.check())
        self.assertIn("9.0.0", self.telegram.await_args.args[0])
        self.assertIn(RELEASE_URL, self.telegram.await_args.args[0])
        self.assertEqual(self.discord.await_args.args[0]["url"], RELEASE_URL)
        self.assertEqual(self.settings.last_notified_release, "9.0.0")
        self.settings.save.assert_called_once_with()
        self.assertFalse(await self.service.check())
        self.telegram.assert_awaited_once()
        self.discord.assert_awaited_once()

    async def test_disabled_event_or_no_channel_skips_github(self):
        self.settings.notification_events["new_release"] = False
        self.assertFalse(await self.service.check())
        self.settings.notification_events["new_release"] = True
        self.settings.telegram_chat_id = ""
        self.settings.discord_webhook_url = ""
        self.assertFalse(await self.service.check())
        self.fetch.assert_not_awaited()

    async def test_current_release_is_not_announced(self):
        self.fetch.return_value = Release("0.0.1", RELEASE_URL)
        self.assertFalse(await self.service.check())
        self.telegram.assert_not_awaited()

    async def test_failed_delivery_is_retried_next_check(self):
        self.telegram.return_value = False
        self.discord.return_value = False
        self.assertFalse(await self.service.check())
        self.assertEqual(self.settings.last_notified_release, "")
        self.settings.save.assert_not_called()

    async def test_run_survives_a_failing_check(self):
        sleep = AsyncMock(side_effect=asyncio.CancelledError)
        with patch.object(ReleaseService, "check", new=AsyncMock(side_effect=RuntimeError)), \
                patch("src.services.release_service.asyncio.sleep", new=sleep):
            with self.assertRaises(asyncio.CancelledError):
                await self.service.run()
        sleep.assert_awaited_once_with(ReleaseService.CHECK_INTERVAL)


def test_notification_events_merge_partial_updates_and_reject_unknown_values():
    current = {"drop_claimed": False, "new_release": False}
    assert NotificationEventSettings.normalize({"new_release": True}, current) == {
        "drop_claimed": False, "new_release": True}
    for invalid in ({"other": True}, {"new_release": "yes"}, ["new_release"]):
        with pytest.raises(ValueError):
            NotificationEventSettings.normalize(invalid, current)


def test_rejected_notification_events_change_nothing():
    settings = MagicMock(spec=Settings)
    settings.notification_events = {"drop_claimed": True, "new_release": False}
    settings.inventory_filters = {}
    manager = SettingsManager(MagicMock(), settings, MagicMock(spec=ConsoleOutputManager))
    with pytest.raises(ValueError):
        manager.update_settings({"notification_events": {"new_release": True, "other": True}})
    assert settings.notification_events == {"drop_claimed": True, "new_release": False}
    settings.save.assert_not_called()


class TestClaimNotificationChoice(unittest.IsolatedAsyncioTestCase):
    async def test_disabled_drop_claimed_event_keeps_claim_and_history(self):
        campaign = _campaign("choice", [_drop("drop-1", "Watch", 30)])
        drop = campaign.timed_drops["drop-1"]
        drop.update_claim("inst-1")
        twitch = campaign._twitch
        twitch.settings.discord_webhook_url = WEBHOOK
        twitch.settings.notification_events = {"drop_claimed": False, "new_release": True}
        twitch.gql_request = AsyncMock(
            return_value={"data": {"claimDropRewards": {"status": "ELIGIBLE_FOR_ALL"}}})
        twitch.gui.broadcast_wanted_items_now = AsyncMock()
        with patch.object(DiscordNotifier, "_send_embed", new=AsyncMock(return_value=True)) as sent:
            self.assertTrue(await drop.claim())
        self.assertTrue(drop.is_claimed)
        sent.assert_not_awaited()
        twitch.drop_history.record.assert_called_once_with(drop, campaign)


@pytest.mark.skipif(NODE is None, reason="Node.js is required for frontend tests")
@pytest.mark.parametrize("ok", [True, False])
def test_notification_checkbox_saves_only_its_event_and_reverts_on_failure(ok):
    source = APP_JS.read_text(encoding="utf-8")
    script = "async " + extract_javascript_function(source, "onNotificationEventChange") + f"""
const assert = require('node:assert/strict');
const input = {{value: 'new_release', checked: true, disabled: false}};
const error = {{hidden: true, textContent: ''}};
const document = {{getElementById: id => id === 'notification-events-error' ? error : null}};
const state = {{translations: {{gui: {{settings: {{notification_events: {{save_error: 'Saved failed'}}}}}}}}}};
const requests = [], rendered = [], reported = [];
const updateSettingsUI = settings => rendered.push(settings);
const reportSettingsError = (element, message) => {{ element.textContent = message; reported.push(message); }};
global.fetch = async (url, options) => {{
  requests.push(JSON.parse(options.body));
  return {{ok: {str(ok).lower()}, json: async () => ({{success: true,
    settings: {{notification_events: {{drop_claimed: true, new_release: true}}}}}})}};
}};
(async () => {{
  const pending = onNotificationEventChange({{target: input}});
  assert.equal(input.disabled, true);
  await pending;
  assert.deepEqual(requests, [{{notification_events: {{new_release: true}}}}]);
  assert.equal(input.disabled, false);
  assert.equal(input.checked, {str(ok).lower()});
  assert.equal(rendered.length, {int(ok)});
  assert.deepEqual(reported, {'[]' if ok else "['Saved failed']"});
  assert.equal(error.hidden, {str(ok).lower()});
}})().catch(failure => {{ console.error(failure); process.exit(1); }});
"""
    result = subprocess.run([NODE, "-"], input=script, text=True, capture_output=True)
    assert result.returncode == 0, result.stderr
