"""Run Discord form and translated Help behavior without external requests."""

import json

import pytest

from tests.javascript_helpers import NODE
from tests.test_telegram_frontend import ROOT, UI_SETUP, run_javascript


pytestmark = pytest.mark.skipif(NODE is None, reason="Node.js is required for frontend tests")
SETUP = UI_SETUP.replace("telegram", "discord").replace("'telegram-test-result'", "'discord-test-result'") + r"""
state.translations.gui.settings.discord.removed = 'Webhook entfernt';
function webhook(value) { document.getElementById('discord-webhook-url').value = value; }
"""


def run_ui(script):
    run_javascript(["saveDiscordSettings", "updateDiscordSettings", "updateSettingsUI", "toggleMiningHoursRange"], SETUP +
                   "\n(async () => {\n" + script + r"""
})().catch(error => { process.stderr.write(String(error)); process.exit(1); });
""")


@pytest.mark.parametrize("action", ["save", "test"])
def test_blank_reuses_stored_webhook(action):
    run_ui(r"""
updateSettingsUI({ discord_configured: true });
assert.equal(document.getElementById('discord-webhook-url').value, '');
global.fetch = async (url, options) => { calls.push({url, body: JSON.parse(options.body)}); return response(); };
""" + f"await updateDiscordSettings('{action}');\n" + r"""
assert.equal(calls[0].body.discord_webhook_url, '');
assert.equal(result.className, 'verify-result success');
""")


def test_explicit_remove_deletes_webhook_and_clears_mask():
    run_ui(r"""
updateSettingsUI({ discord_configured: true });
webhook('unsubmitted-replacement');
global.fetch = async (url, options) => {
    calls.push(JSON.parse(options.body)); return response({ discord_configured: false, discord_webhook_url: '' });
};
await updateDiscordSettings('remove');
assert.deepEqual(calls, [{ discord_webhook_url: '', discord_remove_webhook: true }]);
assert.equal(state.settings.discord_configured, false);
assert.equal(result.textContent, 'Webhook entfernt');
assert.equal(document.getElementById('discord-webhook-url').placeholder, 'https://discord.com/api/webhooks/ID/TOKEN');
""")


@pytest.mark.parametrize("failure", ["http", "network", "rejected", "malformed"])
@pytest.mark.parametrize("action", ["save", "test", "remove"])
def test_save_errors_never_report_success(failure, action):
    run_ui("const failure = " + json.dumps(failure) + ";\n" + r"""
webhook('dummy-secret');
global.fetch = async (url) => {
    calls.push(url);
    if (url.endsWith('test-discord')) return response();
    if (failure === 'network') throw new Error('dummy-secret');
    if (failure === 'http') return { ok: false, status: 500 };
    return { ok: true, json: async () => failure === 'malformed' ? {} : { success: false } };
};
""" + f"await updateDiscordSettings('{action}');\n" + r"""
assert.equal(result.className, 'verify-result error');
assert.equal(result.textContent, 'Speichern fehlgeschlagen');
assert.ok(!result.textContent.includes('dummy-secret'));
assert.equal(state.settings.discord_configured, undefined);
assert.equal(document.getElementById('save-discord-btn').disabled, false);
""")


@pytest.mark.parametrize("failure", ["http", "network", "rejected"])
def test_connection_failures_prevent_save(failure):
    run_ui("const failure = " + json.dumps(failure) + ";\n" + r"""
webhook('dummy-secret');
global.fetch = async (url) => {
    calls.push(url);
    if (failure === 'network') throw new Error('dummy-secret');
    if (failure === 'http') return { ok: false };
    return { ok: true, json: async () => ({ success: false, message: 'dummy-secret' }) };
};
await updateDiscordSettings('test');
assert.deepEqual(calls, ['/api/settings/test-discord']);
assert.equal(result.textContent, 'Verbindung fehlgeschlagen');
""")


def test_success_waits_for_save_and_concurrent_clicks_are_ignored():
    run_ui(r"""
webhook('dummy-secret');
let finishSave;
global.fetch = async (url) => {
    calls.push(url);
    if (url.endsWith('test-discord')) return response();
    return new Promise(resolve => { finishSave = () => resolve(response()); });
};
const pending = updateDiscordSettings('test');
await new Promise(resolve => setImmediate(resolve));
assert.equal(result.className, 'verify-result loading');
assert.equal(document.getElementById('remove-discord-btn').disabled, true);
await updateDiscordSettings('remove');
assert.deepEqual(calls, ['/api/settings/test-discord', '/api/settings']);
finishSave();
await pending;
assert.equal(result.textContent, 'Verbindung erfolgreich');
assert.equal(state.settings.discord_configured, true);
assert.equal(document.getElementById('discord-webhook-url').value, '');
""")


def test_unconfigured_form_requires_url():
    run_ui(r"""
global.fetch = async () => { calls.push('unexpected'); };
await updateDiscordSettings('test');
await updateDiscordSettings('save');
assert.deepEqual(calls, []);
assert.equal(result.className, 'verify-result error');
""")


def test_discord_help_translation_renders_and_switches_language():
    translations = json.loads((ROOT / 'lang' / 'Deutsch.json').read_text(encoding='utf-8'))
    run_javascript(["applyTranslations", "tgHelpSetup"], 'const translations = ' + json.dumps(translations) + ';\n' + r"""
const assert = require('node:assert/strict');
const helpContent = { children: [], replaceChildren(...children) { this.children = children; } };
const version = { textContent: 'Loading...' };
const document = {
    getElementById(id) {
        if (id === 'help-tab') return { querySelector: () => helpContent };
        if (id === 'current-version') return version;
        return null;
    },
    querySelector() { return null; },
};
function translateHistory() {}
function makeElement(tag, attrs, text, callback) {
    const el = { tag, attrs, text, children: [], appendChild(child) { this.children.push(child); } };
    if (callback) callback(el);
    return el;
}
function makeHelpList(tag, items) { return { tag, items }; }
applyTranslations(translations);
assert.equal(helpContent.children.find(el => el.attrs?.id === 'help-discord-header').text, translations.gui.settings.discord.name);
assert.ok(helpContent.children.some(el => el.items === translations.gui.settings.discord.setup_steps));
assert.equal(version.textContent, translations.gui.footer.loading);
translations.gui.settings.discord.name = 'Another language';
applyTranslations(translations);
assert.equal(helpContent.children.filter(el => el.attrs?.id === 'help-discord-header').length, 1);
assert.equal(helpContent.children.find(el => el.attrs?.id === 'help-discord-header').text, 'Another language');
""")


@pytest.mark.parametrize("action", ["save", "test", "remove"])
@pytest.mark.parametrize("fail", [True, False])
def test_result_messages_work_before_translations_load(action, fail):
    run_ui("const fail = " + json.dumps(fail) + ";\n" + r"""
state.translations = {};
webhook('dummy-secret');
let finishSave;
global.fetch = async (url) => {
    if (url.endsWith('test-discord')) return response();
    return new Promise(resolve => { finishSave = () => resolve(fail ? { ok: false } : response()); });
};
""" + f"const pending = updateDiscordSettings('{action}');\n" + r"""
await new Promise(resolve => setImmediate(resolve));
assert.equal(result.className, 'verify-result loading');
assert.ok(result.textContent.length > 3);
assert.ok(!result.textContent.includes('undefined'));
finishSave();
await pending;
assert.equal(result.className, fail ? 'verify-result error' : 'verify-result success');
assert.ok(result.textContent.length > 3);
assert.ok(!result.textContent.includes('undefined'));
if (fail) assert.equal(result.textContent, 'Failed to save Discord settings.');
""")
