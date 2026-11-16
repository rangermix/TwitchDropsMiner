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
from src.utils import json_save
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
    assert ReleaseService.is_newer("2.6.0", "2.6.0-rc.1")
    assert ReleaseService.is_newer("2.6.0+build.2", "2.5.0+build.1")
    assert not ReleaseService.is_newer("2.6.0", "2.7.0-rc.1")
    assert not ReleaseService.is_newer("2.6.0+build.2", "2.6.0+build.1")
    for tag in ("2.6", "2.6.0-beta", "latest", None, " 2.6.0", "2.6.0\n", "02.6.0", "٢.6.0"):
        assert not ReleaseService.is_newer(tag, "2.5.0")


@pytest.mark.asyncio
async def test_fetch_latest_accepts_only_this_repository_release_page():
    with github({"tag_name": "v9.0.0", "html_url": RELEASE_URL}):
        assert await ReleaseService.fetch_latest() == Release("9.0.0", RELEASE_URL)
    with github({"tag_name": "v9.0.0", "html_url": "https://evil.example/<b>"}):
        assert await ReleaseService.fetch_latest() == Release("9.0.0", RELEASE_URL)
    with github({"tag_name": "v9.0.0", "html_url": RELEASE_URL.replace("9.0.0", "8.0.0")}):
        assert await ReleaseService.fetch_latest() == Release("9.0.0", RELEASE_URL)
    with github({"tag_name": "v9.0.0+build.2"}):
        assert await ReleaseService.fetch_latest() == Release("9.0.0+build.2", RELEASE_URL + "%2Bbuild.2")
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
            last_notified_releases={"telegram": "", "discord": ""}, save=MagicMock(),
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
        self.assertEqual(self.settings.last_notified_releases, {"telegram": "9.0.0", "discord": "9.0.0"})
        self.assertEqual(self.settings.save.call_count, 2)
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
        self.assertEqual(self.settings.last_notified_releases, {"telegram": "", "discord": ""})
        self.settings.save.assert_not_called()
        self.telegram.return_value = True
        self.assertTrue(await self.service.check())
        self.assertEqual(self.settings.last_notified_releases, {"telegram": "9.0.0", "discord": ""})

    async def test_provider_exception_retries_without_repeating_success(self):
        self.telegram.side_effect = RuntimeError
        self.assertTrue(await self.service.check())
        self.assertEqual(self.settings.last_notified_releases, {"telegram": "", "discord": "9.0.0"})
        self.telegram.side_effect = None
        self.assertTrue(await self.service.check())
        self.assertEqual(self.telegram.await_count, 2)
        self.discord.assert_awaited_once()

    async def test_older_latest_and_build_changes_do_not_repeat_a_release(self):
        self.assertTrue(await self.service.check())
        for version in ("8.0.0", "9.0.0+build.2", "9.0.0"):
            self.fetch.return_value = Release(version, RELEASE_URL)
            self.assertFalse(await self.service.check())
        self.telegram.assert_awaited_once()
        self.discord.assert_awaited_once()

    async def test_disabling_alerts_during_fetch_prevents_a_send(self):
        async def fetch_after_disable(_):
            self.settings.notification_events["new_release"] = False
            return Release("9.0.0", RELEASE_URL)

        self.fetch.side_effect = fetch_after_disable
        self.assertFalse(await self.service.check())
        self.fetch.assert_awaited_once()
        self.telegram.assert_not_awaited()
        self.discord.assert_not_awaited()

    async def test_destination_changed_during_fetch_is_used(self):
        new_webhook = "https://discord.com/api/webhooks/222/ccc_ddd"

        async def fetch_after_change(_):
            self.settings.telegram_chat_id = ""
            self.settings.discord_webhook_url = new_webhook
            return Release("9.0.0", RELEASE_URL)

        self.fetch.side_effect = fetch_after_change
        with patch.object(DiscordNotifier, "notify_new_release", autospec=True, return_value=True) as sent:
            self.assertTrue(await self.service.check())
        self.telegram.assert_not_awaited()
        self.assertEqual(sent.await_args.args[0].webhook_url, new_webhook)

    async def test_delivered_version_persists_even_if_other_provider_is_cancelled(self):
        accepted = asyncio.Event()

        async def pending(_):
            await accepted.wait()
            await asyncio.Future()

        self.telegram.side_effect = pending
        self.settings.save.side_effect = accepted.set
        task = asyncio.create_task(self.service.check())
        await asyncio.wait_for(accepted.wait(), timeout=1)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertEqual(self.settings.last_notified_releases, {"telegram": "", "discord": "9.0.0"})
        self.settings.save.assert_called_once()

    async def test_save_failure_retries_without_resending(self):
        self.settings.save.side_effect = OSError
        self.settings.discord_webhook_url = ""
        self.assertTrue(await self.service.check())
        self.settings.save.assert_called_once()
        self.settings.save.side_effect = None
        self.settings.notification_events["new_release"] = False
        self.assertFalse(await self.service.check())
        self.assertEqual(self.settings.save.call_count, 2)
        self.telegram.assert_awaited_once()

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


@pytest.mark.asyncio
async def test_notification_settings_revision_and_cursor_persist(tmp_path, monkeypatch):
    settings_path = tmp_path / "settings.json"
    monkeypatch.setattr("src.config.settings.SETTINGS_PATH", settings_path)
    settings_path.write_text('{"last_notified_release":"8.0.0"}', encoding="utf-8")
    settings = Settings()
    assert settings.last_notified_releases == {"telegram": "8.0.0", "discord": "8.0.0"}
    broadcaster = AsyncMock()
    manager = SettingsManager(broadcaster, settings, MagicMock(spec=ConsoleOutputManager))
    initial = manager.get_settings()
    assert initial["notification_events_revision"] == 0
    assert "last_notified_releases" not in initial
    first = manager.update_settings({"notification_events": {"new_release": True}})
    second = manager.update_settings({"notification_events": {"drop_claimed": False}})
    unchanged = manager.update_settings({"notification_events": {"drop_claimed": False}})
    await asyncio.sleep(0)
    assert first["notification_events"] == {"drop_claimed": True, "new_release": True}
    assert first["notification_events_revision"] == 1
    assert second["notification_events_revision"] == 2
    assert unchanged["notification_events_revision"] == 2
    assert all("last_notified_releases" not in call.args[1] for call in broadcaster.emit.await_args_list)
    restarted = Settings()
    assert restarted.notification_events == {"drop_claimed": False, "new_release": True}
    assert restarted.notification_events_revision == 2
    assert restarted.last_notified_releases == {"telegram": "8.0.0", "discord": "8.0.0"}

    monkeypatch.setattr(settings, "save", MagicMock(side_effect=OSError))
    with pytest.raises(OSError):
        manager.update_settings({"notification_events": {"new_release": False}})
    assert settings.notification_events == restarted.notification_events
    assert settings.notification_events_revision == 2
    assert broadcaster.emit.await_count == 3


@pytest.mark.asyncio
async def test_release_delivery_cannot_mutate_default_settings(tmp_path, monkeypatch):
    settings_path = tmp_path / "settings.json"
    monkeypatch.setattr("src.config.settings.SETTINGS_PATH", settings_path)
    settings_path.write_text("{}", encoding="utf-8")
    settings = Settings()
    settings.notification_events = {"drop_claimed": True, "new_release": True}
    settings.discord_webhook_url = WEBHOOK
    service = ReleaseService(SimpleNamespace(settings=settings, diagnostics=None))
    with (
        patch.object(ReleaseService, "fetch_latest", new=AsyncMock(return_value=Release("9.0.0", RELEASE_URL))),
        patch.object(DiscordNotifier, "_send_embed", new=AsyncMock(return_value=True)),
    ):
        assert await service.check()
    assert Settings().last_notified_releases == {"telegram": "", "discord": "9.0.0"}
    settings_path.write_text("{}", encoding="utf-8")
    assert Settings().last_notified_releases == {"telegram": "", "discord": ""}


def test_failed_json_serialization_preserves_existing_settings(tmp_path):
    path = tmp_path / "settings.json"
    json_save(path, {"new_release": True})
    with pytest.raises(TypeError):
        json_save(path, {"new_release": object()})
    assert json.loads(path.read_text(encoding="utf-8")) == {"new_release": True}
    assert list(tmp_path.iterdir()) == [path]


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


def notification_javascript():
    source = APP_JS.read_text(encoding="utf-8")
    return "\n".join([
        extract_javascript_function(source, "reconcileNotificationEvents"),
        extract_javascript_function(source, "renderNotificationEvents"),
        "async " + extract_javascript_function(source, "onNotificationEventChange"),
    ]) + "\n"


@pytest.mark.skipif(NODE is None, reason="Node.js is required for frontend tests")
@pytest.mark.parametrize("ok", [True, False])
def test_notification_checkbox_saves_only_its_event_and_reverts_on_failure(ok):
    script = notification_javascript() + """
const assert = require('node:assert/strict');
const input = {value: 'new_release', checked: true, disabled: false};
const error = {hidden: true, textContent: ''};
const elements = {'notification-events-error': error, 'notify-new_release': input};
const document = {getElementById: id => elements[id]};
const state = {settings: {notification_events_revision: 0, notification_events: {new_release: false}},
  translations: {gui: {settings: {notification_events: {save_error: 'Saved failed'}}}}};
const requests = [], reported = [];
const reportSettingsError = (element, message) => { element.textContent = message; reported.push(message); };
const ok = __OK__;
global.fetch = async (url, options) => {
  requests.push(JSON.parse(options.body));
  return {ok, json: async () => ({success: true,
    settings: {notification_events_revision: 1, notification_events: {drop_claimed: true, new_release: true}}})};
};
(async () => {
  const pending = onNotificationEventChange({target: input});
  assert.equal(input.disabled, true);
  await pending;
  assert.deepEqual(requests, [{notification_events: {new_release: true}}]);
  assert.equal(input.disabled, false);
  assert.equal(input.checked, ok);
  assert.equal(state.settings.notification_events_revision, ok ? 1 : 0);
  assert.deepEqual(reported, ok ? [] : ['Saved failed']);
  assert.equal(error.hidden, ok);
})().catch(failure => { console.error(failure); process.exit(1); });
"""
    script = script.replace("__OK__", json.dumps(ok))
    result = subprocess.run([NODE, "-"], input=script, text=True, capture_output=True)
    assert result.returncode == 0, result.stderr


@pytest.mark.skipif(NODE is None, reason="Node.js is required for frontend tests")
def test_delayed_settings_cannot_replace_newer_checkbox_choices():
    script = notification_javascript() + """
const assert = require('node:assert/strict');
const drop = {value: 'drop_claimed', checked: false, disabled: false};
const release = {value: 'new_release', checked: true, disabled: false};
const error = {hidden: true, textContent: ''};
const elements = {'notification-events-error': error, 'notify-new_release': release,
    'notify-drop_claimed': drop};
const document = {getElementById: id => elements[id]};
const snapshot = (revision, drop_claimed, new_release) => ({
    notification_events_revision: revision, notification_events: {drop_claimed, new_release}
});
const state = {settings: snapshot(0, true, false), translations: {}};
const reportSettingsError = () => assert.fail('Unexpected save failure');
const replies = {};
global.fetch = (_, options) => new Promise(resolve => {
    const [key] = Object.keys(JSON.parse(options.body).notification_events);
    replies[key] = settings => resolve({ok: true, json: async () => ({success: true, settings})});
});
(async () => {
    const dropSave = onNotificationEventChange({target: drop});
    const releaseSave = onNotificationEventChange({target: release});
    state.settings = reconcileNotificationEvents(snapshot(1, false, false));
    renderNotificationEvents();
    assert.equal(release.checked, true);
    replies.new_release(snapshot(2, false, true));
    await releaseSave;
    replies.drop_claimed(snapshot(1, false, false));
    await dropSave;
    state.settings = reconcileNotificationEvents(snapshot(1, false, false)); // Late socket update.
    renderNotificationEvents();
    assert.equal(state.settings.notification_events_revision, 2);
    assert.equal(release.checked, true);
    assert.equal(drop.checked, false);
})().catch(error => { console.error(error); process.exit(1); });
"""
    result = subprocess.run([NODE, "-"], input=script, text=True, capture_output=True)
    assert result.returncode == 0, result.stderr
