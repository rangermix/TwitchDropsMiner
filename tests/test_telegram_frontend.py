"""Execute Telegram UI behavior in Node without making network requests."""

import json
import subprocess
from pathlib import Path

import pytest

from tests.javascript_helpers import APP_JS, NODE, extract_javascript_function


pytestmark = pytest.mark.skipif(NODE is None, reason="Node.js is required for frontend tests")
ROOT = Path(__file__).resolve().parents[1]


def run_javascript(names: list[str], script: str) -> None:
    source = APP_JS.read_text(encoding="utf-8")
    functions = []
    for name in names:
        function = extract_javascript_function(source, name)
        start = source.index(f"function {name}(")
        if source[start - 6 : start] == "async ":
            function = f"async {function}"
        functions.append(function)
    completed = subprocess.run(
        [NODE, "-"],
        input="\n".join(functions) + "\n" + script,
        text=True,
        capture_output=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr


def test_help_translations_do_not_abort_remaining_page_translations():
    translations = json.loads((ROOT / "lang" / "Deutsch.json").read_text(encoding="utf-8"))
    run_javascript(
        ["applyTranslations", "tgHelpSetup"],
        "const translations = " + json.dumps(translations) + ";\n" + r"""
const assert = require('node:assert/strict');
const helpContent = { children: [], replaceChildren(...children) { this.children = children; } };
const version = { textContent: 'Loading...' };
const language = { textContent: '' };
const document = {
    getElementById(id) {
        if (id === 'help-tab') return { querySelector: () => helpContent };
        if (id === 'current-version') return version;
        return null;
    },
    querySelector(selector) { return selector === '.language-selector span' ? language : null; },
};
function makeElement(tag, attrs, text, callback) {
    const el = { tag, attrs, text, children: [], appendChild(child) { this.children.push(child); } };
    if (callback) callback(el);
    return el;
}
function makeHelpList(tag, items) { return { tag, items }; }
applyTranslations(translations);
const telegram = translations.gui.settings.telegram;
assert.equal(helpContent.children.find(el => el.attrs?.id === 'help-telegram-header').text, telegram.name);
assert.deepEqual(helpContent.children.find(el => el.items === telegram.setup_steps).items, telegram.setup_steps);
assert.equal(version.textContent, translations.gui.footer.loading);
assert.equal(language.textContent, translations.gui.header.language);
// Switching languages replaces Help instead of retaining the first translation.
translations.gui.settings.telegram.name = 'Another language';
applyTranslations(translations);
assert.equal(helpContent.children.filter(el => el.attrs?.id === 'help-telegram-header').length, 1);
assert.equal(helpContent.children.find(el => el.attrs?.id === 'help-telegram-header').text, 'Another language');
""",
    )


UI_FUNCTIONS = [
    "testTelegramConnection",
    "saveTelegramSettings",
    "handleSaveTelegramClick",
    "updateSettingsUI",
]
UI_SETUP = r"""
const assert = require('node:assert/strict');
const elements = {};
const document = {
    getElementById(id) {
        return elements[id] ??= { value: '', placeholder: '', textContent: '', style: {} };
    },
    body: { classList: { add() {}, remove() {} } },
};
function applyInventoryViewMode() {}
function renderGamesToWatch() {}
function renderChannels() {}
function renderInventory() {}
function updateGameTagsDisplay() {}
const state = { settings: {}, translations: { gui: { settings: { telegram: {
    saved: 'Gespeichert', success: 'Verbindung erfolgreich', error: 'Verbindung fehlgeschlagen',
    save_error: 'Speichern fehlgeschlagen', missing_credentials: 'Zugangsdaten fehlen',
    save_settings: 'Speichern', test_connection: 'Verbindung testen',
} } } } };
const calls = [];
console.log = () => {};
console.error = () => {};
const result = document.getElementById('telegram-test-result');
function response(settings = { telegram_configured: true, telegram_bot_token: '••••••••', telegram_chat_id: '42' }) {
    return { ok: true, status: 200, json: async () => ({ success: true, settings }) };
}
function input(token, chat) {
    document.getElementById('telegram-bot-token').value = token;
    document.getElementById('telegram-chat-id').value = chat;
}
"""


def run_ui(script: str) -> None:
    run_javascript(UI_FUNCTIONS, UI_SETUP + "\n(async () => {\n" + script + r"""
})().catch(error => { process.stderr.write(String(error)); process.exit(1); });
""")


@pytest.mark.parametrize("action", ["testTelegramConnection", "handleSaveTelegramClick"])
def test_saved_token_can_be_used_without_reentering_secret(action):
    run_ui(r"""
updateSettingsUI({ telegram_configured: true, telegram_bot_token: '••••••••', telegram_chat_id: '42' });
assert.equal(document.getElementById('telegram-bot-token').value, '');
let fetch = global.fetch = async (url, options) => { calls.push({ url, body: JSON.parse(options.body) }); return response(); };
""" + f"await {action}();\n" + r"""
assert.ok(calls.length > 0, 'saved credentials must reach the API');
assert.equal(calls[0].body.telegram_bot_token, '');
assert.equal(calls[0].body.telegram_chat_id, '42');
assert.equal(result.className, 'verify-result success');
""")


def test_chat_id_can_be_cleared_to_disable_notifications_with_saved_token():
    run_ui(r"""
updateSettingsUI({ telegram_configured: true, telegram_chat_id: '42' });
input('', '');
global.fetch = async (url, options) => { calls.push(JSON.parse(options.body)); return response({ telegram_configured: true, telegram_chat_id: '' }); };
await handleSaveTelegramClick();
assert.deepEqual(calls, [{ telegram_bot_token: '', telegram_chat_id: '' }]);
assert.equal(state.settings.telegram_chat_id, '');
assert.equal(result.className, 'verify-result success');
""")


@pytest.mark.parametrize("failure", ["http", "network", "rejected"])
@pytest.mark.parametrize("action", ["handleSaveTelegramClick", "testTelegramConnection"])
def test_save_failures_are_reported_even_after_successful_connection_test(failure, action):
    run_ui("const failure = " + json.dumps(failure) + ";\n" + r"""
input('dummy-token', '42');
global.fetch = async (url) => {
    calls.push(url);
    if (url.endsWith('test-telegram')) return response();
    if (failure === 'network') throw new Error('offline');
    if (failure === 'http') return { ok: false, status: 500 };
    return { ok: true, status: 200, json: async () => ({ success: false }) };
};
""" + f"await {action}();\n" + r"""
assert.ok(calls.includes('/api/settings'));
assert.equal(result.className, 'verify-result error', 'a failed save must never report success');
assert.ok(result.textContent.includes('Speichern fehlgeschlagen'), 'show a translated save error');
assert.equal(state.settings.telegram_configured, undefined);
""")


def test_connection_success_is_shown_only_after_settings_are_saved():
    run_ui(r"""
input('dummy-token', '42');
let finishSave;
global.fetch = async (url) => {
    calls.push(url);
    if (url.endsWith('test-telegram')) return response();
    return new Promise(resolve => { finishSave = () => resolve(response()); });
};
const pending = testTelegramConnection();
await new Promise(resolve => setImmediate(resolve));
assert.equal(result.className, 'verify-result loading');
finishSave();
await pending;
assert.equal(result.className, 'verify-result success');
assert.equal(result.textContent, 'Verbindung erfolgreich');
assert.equal(state.settings.telegram_configured, true);
assert.equal(document.getElementById('telegram-bot-token').value, '');
""")


@pytest.mark.parametrize("action", ["testTelegramConnection", "handleSaveTelegramClick"])
def test_unconfigured_form_requires_both_credentials(action):
    run_ui(r"""
input('', '42');
global.fetch = async () => { calls.push('unexpected request'); return response(); };
""" + f"await {action}();\n" + r"""
assert.deepEqual(calls, []);
assert.equal(result.className, 'verify-result error');
""")
