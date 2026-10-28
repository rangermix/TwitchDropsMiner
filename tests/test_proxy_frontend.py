"""Proxy saves stay isolated from autosaves and recover visibly from rejection."""

import subprocess

import pytest

from tests.javascript_helpers import APP_JS, NODE, extract_javascript_function


pytestmark = pytest.mark.skipif(NODE is None, reason="Node.js is required for frontend tests")
MASK = "•" * 8


def test_proxy_save_is_separate_and_restores_on_rejection():
    source = APP_JS.read_text(encoding="utf-8")
    script = "\n".join([
        extract_javascript_function(source, "reportSettingsError"),
        "async " + extract_javascript_function(source, "saveProxy"),
        "async " + extract_javascript_function(source, "saveSettings"),
    ]) + r"""
const assert = require('node:assert/strict');
const MASK = '""" + MASK + r"""';
const stored = `http://user:${MASK}@proxy.lan:3128`;
const element = (extra = {}) => ({ value: '', checked: false, disabled: false, style: {}, ...extra });
const elements = {
    'set-proxy-btn': element(), 'proxy-url': element(), 'proxy-verify-result': element(),
    'dark-mode': element(), 'inventory-list-view': element(), 'language': element({value: 'English'}),
    'connection-quality': element({value: '1'}), 'minimum-refresh-interval': element({value: '30'}),
};
const document = { getElementById: id => elements[id] || element() };
const state = {
    settings: { proxy: stored, games_to_watch: ['A'] },
    translations: { gui: { settings: { proxy_save_error: '<b> Proxy failed' } } },
};
function parseDropNameBlacklist() { return []; }
// Builds that queue settings writes route autosaves through this helper.
async function queueSettingsWrite(settings) {
    return fetch('/api/settings', { method: 'POST', body: JSON.stringify(settings) });
}
function getInventoryFilters() { return {}; }
let mode = 'success', calls = [];
async function fetch(url, options) {
    assert.equal(url, '/api/settings');
    const request = JSON.parse(options.body);
    calls.push(request);
    if ('proxy' in request) assert.deepEqual(Object.keys(request), ['proxy']);
    if (mode === 'network') throw new Error('offline');
    return {
        ok: mode !== 'http',
        async json() {
            return { success: mode !== 'application', settings: { proxy: request.proxy.replace('new', MASK) } };
        },
    };
}
(async () => {
    const input = elements['proxy-url'], result = elements['proxy-verify-result'];
    // Unchanged masked value is not resent.
    input.value = stored;
    await saveProxy();
    assert.equal(calls.length, 0);

    for (const failure of ['http', 'network', 'application']) {
        mode = failure;
        input.value = `http://user:${MASK}@proxy.lan:3129`;
        await saveProxy();
        assert.equal(state.settings.proxy, stored);
        assert.equal(input.value, stored);
        assert.equal(result.textContent, '<b> Proxy failed');
        assert.equal(result.className, 'verify-result error');
        assert.equal(elements['set-proxy-btn'].disabled, false);
    }

    // Autosaves never carry the proxy, so a rejected proxy cannot block them.
    calls = [];
    await saveSettings();
    assert.equal(calls.length, 1);
    assert.ok(!('proxy' in calls[0]));
    assert.deepEqual(calls[0].games_to_watch, ['A']);

    mode = 'success';
    input.value = 'http://user:new@proxy.lan:3129';
    await saveProxy();
    assert.equal(state.settings.proxy, `http://user:${MASK}@proxy.lan:3129`);
    assert.equal(input.value, state.settings.proxy, 'the input shows the masked value');
    assert.equal(result.style.display, 'none');
})().catch(error => { console.error(error); process.exitCode = 1; });
"""
    result = subprocess.run([NODE, "-"], input=script, text=True, capture_output=True)
    assert result.returncode == 0, result.stderr
