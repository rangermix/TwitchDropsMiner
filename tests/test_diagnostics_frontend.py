"""The diagnosis action sends only coarse browser data and safely renders translated results."""

import json
from pathlib import Path

import pytest

from src.i18n.translator import GUISettings
from tests.javascript_helpers import APP_JS, NODE
from tests.test_telegram_frontend import run_javascript


pytestmark = pytest.mark.skipif(NODE is None, reason="Node.js is required for frontend tests")
ROOT = Path(__file__).resolve().parents[1]
LOCALES = sorted((ROOT / "lang").glob("*.json"))
KEYS = {"dump_diagnostics", "diagnostics_help", "diagnostics_saving", "diagnostics_saved", "diagnostics_error", "diagnostics_busy"}
SETUP = r"""
const assert = require('node:assert/strict');
const elements = {};
function element(id) {
    return elements[id] ??= {
        disabled: false, textContent: '', className: '', attrs: {},
        setAttribute(name, value) { this.attrs[name] = value; },
        removeAttribute(name) { delete this.attrs[name]; },
        set innerHTML(value) { throw new Error('must use textContent'); },
    };
}
const document = { getElementById: element };
const state = { translations: { gui: { settings: {
    diagnostics_saving: 'Saving', diagnostics_saved: 'Saved: {path}',
    diagnostics_error: 'Failed', diagnostics_busy: 'Wait',
} } } };
Object.defineProperty(globalThis, 'navigator', { value: {
    userAgent: 'Chrome/123 Windows user-private-canary', onLine: true,
}, configurable: true });
const window = { innerWidth: 1280, innerHeight: 720, location: { href: 'https://private-host/user' } };
const socket = { connected: true };
const button = element('dump-diagnostics-btn'), result = element('diagnostics-result');
const calls = [];
global.fetch = async (url, options) => {
    calls.push({url, ...options});
    return { ok: true, status: 200, json: async () => ({success: true, file: 'diagnostics/test.json'}) };
};
"""


def run_ui(script):
    run_javascript(["dumpDiagnostics"], SETUP + "\n(async () => {\n" + script + r"""
})().catch(error => { process.stderr.write(String(error)); process.exit(1); });
""")


def test_action_posts_allowlisted_browser_description_and_relative_volume_result():
    run_ui(r"""
await dumpDiagnostics();
assert.equal(calls.length, 1);
assert.equal(calls[0].url, '/api/diagnostics');
assert.equal(calls[0].method, 'POST');
assert.equal(calls[0].headers['X-TDM-Request'], '1');
assert.deepEqual(JSON.parse(calls[0].body), {browser: {
    width: 1280, height: 720, browser: 'Chrome', platform: 'Windows',
    tab: 'settings', online: true, socket_connected: true,
}});
assert.ok(!calls[0].body.includes('private'));
assert.equal(result.textContent, 'Saved: data/diagnostics/test.json');
assert.equal(result.className, 'help-text success');
assert.equal(button.disabled, false);
assert.equal(button.attrs['aria-busy'], undefined);
""")


def test_double_activation_is_suppressed_until_request_finishes():
    run_ui(r"""
let finish;
global.fetch = async () => {
    calls.push('request');
    return await new Promise(resolve => { finish = resolve; });
};
const first = dumpDiagnostics();
assert.equal(result.textContent, 'Saving');
assert.equal(button.disabled, true);
assert.equal(button.attrs['aria-busy'], 'true');
await dumpDiagnostics();
assert.equal(calls.length, 1);
finish({ok: true, status: 200, json: async () => ({success: true, file: 'diagnostics/test.json'})});
await first;
assert.equal(button.disabled, false);
""")


@pytest.mark.parametrize("failure", ["network", "http", "json", "application", "bad-file", "busy"])
def test_failures_are_translated_and_controls_recover(failure):
    run_ui("const failure = " + json.dumps(failure) + ";\n" + r"""
global.fetch = async () => {
    if (failure === 'network') throw new Error('private backend message');
    return { ok: !['http', 'busy'].includes(failure), status: failure === 'busy' ? 429 : 500,
        json: async () => {
            if (failure === 'json') throw new Error('invalid json');
            return {success: failure !== 'application', file: failure === 'bad-file' ? null : 'diagnostics/test.json'};
        }
    };
};
await dumpDiagnostics();
assert.equal(result.textContent, failure === 'busy' ? 'Wait' : 'Failed');
assert.equal(result.className, 'help-text error');
assert.equal(button.disabled, false);
assert.equal(button.attrs['aria-busy'], undefined);
""")


def test_path_and_translated_markup_are_rendered_as_plain_text():
    run_ui(r"""
state.translations.gui.settings.diagnostics_saved = '<em>Saved</em> {path}';
global.fetch = async () => ({ok: true, json: async () => ({success: true, file: '<img src=x onerror=bad>'})});
await dumpDiagnostics();
assert.equal(result.textContent, '<em>Saved</em> data/<img src=x onerror=bad>');
""")


def test_activation_before_translations_are_ready_is_safe():
    run_ui(r"""
state.translations = null;
await dumpDiagnostics();
assert.equal(calls.length, 0);
assert.equal(button.disabled, false);
""")


@pytest.mark.parametrize("locale", LOCALES, ids=lambda path: path.stem)
def test_every_locale_has_matching_diagnostic_keys_and_safe_button_help_rendering(locale):
    translations = json.loads(locale.read_text(encoding="utf-8"))
    settings = translations["gui"]["settings"]
    assert GUISettings.__annotations__.keys() >= KEYS
    assert all(isinstance(settings[key], str) and settings[key].strip() for key in KEYS)
    assert settings["diagnostics_saved"].count("{path}") == 1
    run_javascript(["applyTranslations", "tgHelpSetup"], "const translations = " + json.dumps(translations) + ";\n" + r"""
const assert = require('node:assert/strict');
const elements = {};
for (const id of ['dump-diagnostics-btn', 'diagnostics-help']) {
    elements[id] = {textContent: '', set innerHTML(value) { throw new Error('unsafe translation'); }};
}
const document = {
    getElementById(id) { return id === 'settings-tab' ? {querySelector() { return null; }} : elements[id] || null; },
    querySelector() { return null; },
};
function translateHistory() {}
function renderGamesToWatch() {}
applyTranslations(translations);
assert.equal(elements['dump-diagnostics-btn'].textContent, translations.gui.settings.dump_diagnostics);
assert.equal(elements['diagnostics-help'].textContent, translations.gui.settings.diagnostics_help);
""")


def test_native_button_and_live_result_are_wired_to_the_settings_action():
    html = (ROOT / "web" / "index.html").read_text(encoding="utf-8")
    assert 'id="dump-diagnostics-btn" class="action-button" type="button" aria-describedby="diagnostics-help"' in html
    assert 'id="diagnostics-result" class="help-text" role="status" aria-live="polite"' in html
    assert "getElementById('dump-diagnostics-btn').addEventListener('click', dumpDiagnostics)" in APP_JS.read_text(encoding="utf-8")
