"""Run authentication UI behavior in Node with deterministic DOM/network doubles."""

import json
import subprocess
from pathlib import Path

import pytest

from tests.javascript_helpers import NODE


pytestmark = pytest.mark.skipif(NODE is None, reason="Node.js is required for frontend tests")
ROOT = Path(__file__).resolve().parents[1]
SOURCE = (ROOT / "web/static/auth.js").read_text(encoding="utf-8")
TRANSLATIONS = json.loads((ROOT / "lang/English.json").read_text(encoding="utf-8"))["gui"]["auth"]


def run(script):
    harness = r"""
const assert = require('node:assert/strict');
const elements = new Map();
const events = new Map();
const requests = [];
const redirects = [];
let responder = async () => ({ok: true, status: 200, json: async () => ({success: true})});
global.window = global;
window.location = {href: 'http://miner.test/', origin: 'http://miner.test',
    replace: url => redirects.push(url), reload: () => redirects.push('reload')};
window.addEventListener = (event, handler) => events.set(event, handler);
window.fetch = async (input, options) => { requests.push({input, options}); return responder(); };
function element(id, properties = {}) {
    const node = {value: '', textContent: '', hidden: false, checked: false, disabled: false,
        dataset: {}, addEventListener: () => {}, ...properties};
    Object.defineProperty(node, 'innerHTML', {set() {throw new Error('Unsafe HTML');}});
    elements.set(id, node);
    return node;
}
global.document = {
    getElementById: id => elements.get(id),
    querySelectorAll: selector => selector.includes('password')
        ? [...elements.values()].filter(node => node.password)
        : selector === '[data-auth-text]' ? [...elements.values()].filter(node => node.dataset.authText)
        : [...elements.values()].filter(node => node.button),
    addEventListener: (event, handler) => events.set(event, handler)
};
element('web-auth-result');
element('submit', {button: true});
"""
    result = subprocess.run([NODE, "-"], input=harness + SOURCE + "\n" +
        "dashboardAuth.translations = " + json.dumps(TRANSLATIONS) + ";\n" +
        "(async () => {" + script + "})().catch(error => { console.error(error); process.exit(1); });",
        capture_output=True, text=True, check=False)
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize("remember", [False, True])
def test_login_submits_cookie_preference_and_clears_password(remember):
    run("element('web-auth-remember', {checked: " + json.dumps(remember) + "});" + r"""
        const password = element('web-auth-password', {value: 'test password', password: true});
        await dashboardAuth.submit('login');
        const body = JSON.parse(requests[0].options.body);
        assert.equal(body.password, 'test password');
        assert.equal(body.remember, elements.get('web-auth-remember').checked);
        assert.equal(requests[0].options.headers.get('X-TDM-Request'), '1');
        assert.equal(password.value, '');
        assert.deepEqual(redirects, ['/']);
    """)


@pytest.mark.parametrize("status,detail", [(401, "invalid_password"), (429, "rate_limited"),
                                        (500, "request_failed"), (400, "password_mismatch")])
def test_errors_are_visible_without_false_success(status, detail):
    run(f"responder = async () => ({{ok: false, status: {status}, json: async () => ({{detail: '{detail}'}})}});" + r"""
        element('web-auth-current', {value: 'wrong'});
        await dashboardAuth.submit('disable');
        assert.equal(redirects.length, 0);
        assert.ok(elements.get('web-auth-result').textContent.length > 0);
        assert.equal(dashboardAuth.busy, false);
        assert.equal(elements.get('submit').disabled, false);
    """)


def test_change_disable_and_logout_request_contracts():
    run(r"""
        element('web-auth-current', {value: 'old password'});
        element('web-auth-new', {value: 'new password'});
        element('web-auth-confirm', {value: 'new password'});
        await dashboardAuth.submit('change');
        assert.deepEqual(JSON.parse(requests[0].options.body), {
            action: 'change', current_password: 'old password',
            password: 'new password', confirm_password: 'new password'
        });
        await dashboardAuth.submit('disable');
        assert.deepEqual(JSON.parse(requests[1].options.body), {
            action: 'disable', current_password: 'old password', password: '', confirm_password: ''
        });
        await dashboardAuth.submit('logout');
        assert.equal(requests[2].input, '/api/auth/logout');
        assert.equal(redirects.at(-1), '/login');
    """)


def test_network_error_and_double_submit():
    run(r"""
        responder = async () => {throw new Error('network failure');};
        await dashboardAuth.submit('disable');
        assert.equal(elements.get('web-auth-result').textContent, dashboardAuth.translations.request_failed);
        dashboardAuth.busy = true;
        await dashboardAuth.submit('disable');
        assert.equal(requests.length, 1);
        assert.equal(redirects.length, 0);
    """)


@pytest.mark.parametrize("response", [
    "async () => {throw new Error('network failure');}",
    "async () => ({ok: false, status: 503})",
    "async () => ({ok: true, status: 200, json: async () => {throw new SyntaxError('invalid JSON');}})",
], ids=["network", "http-503", "invalid-json"])
def test_initial_status_failure_allows_login_without_reloading(response):
    run("responder = " + response + ";" + r"""
        dashboardAuth.translations = {};
        const submit = elements.get('submit');
        submit.disabled = true;
        let submitLogin;
        element('web-auth-login', {
            querySelectorAll: () => [submit],
            addEventListener: (event, handler) => {if (event === 'submit') submitLogin = handler;}
        });
        const password = element('web-auth-password', {value: 'test password', password: true});
        element('web-auth-remember', {checked: false});
        events.get('DOMContentLoaded')();
        await new Promise(resolve => setImmediate(resolve));
        assert.equal(requests[0].input, '/api/auth/status');
        assert.equal(elements.get('web-auth-result').textContent, 'Request failed. Try again.');
        assert.equal(submit.disabled, false);
        assert.deepEqual(redirects, []);

        responder = async () => ({ok: true, status: 200, json: async () => ({success: true})});
        let prevented = false;
        submitLogin({preventDefault: () => {prevented = true;}});
        assert.equal(prevented, true);
        assert.equal(submit.disabled, true);
        await new Promise(resolve => setImmediate(resolve));
        assert.equal(requests.length, 2);
        assert.equal(requests[1].input, '/api/auth/login');
        assert.deepEqual(JSON.parse(requests[1].options.body), {password: 'test password', remember: false});
        assert.equal(requests[1].options.headers.get('X-TDM-Request'), '1');
        assert.equal(password.value, '');
        assert.equal(elements.get('web-auth-result').textContent, '');
        assert.deepEqual(redirects, ['/']);
    """)


def test_initial_status_failure_keeps_settings_disabled():
    run(r"""
        responder = async () => {throw new Error('network failure');};
        element('web-auth-settings');
        elements.get('submit').disabled = true;
        events.get('DOMContentLoaded')();
        await new Promise(resolve => setImmediate(resolve));
        assert.equal(elements.get('submit').disabled, true);
        assert.equal(elements.get('web-auth-result').textContent, dashboardAuth.translations.request_failed);
        assert.deepEqual(redirects, []);
    """)


def test_logout_error_is_visible_outside_settings():
    run(r"""
        const result = element('web-auth-logout-result');
        responder = async () => {throw new Error('network failure');};
        await dashboardAuth.submit('logout');
        assert.equal(result.textContent, dashboardAuth.translations.request_failed);
        assert.equal(redirects.length, 0);
    """)


def test_session_expiry_redirect_and_cross_origin_header_isolation():
    run(r"""
        responder = async () => ({status: 401});
        await fetch('/api/settings');
        assert.deepEqual(redirects, ['/login']);
        redirects.length = 0;
        await fetch('https://external.test/', {method: 'POST'});
        assert.equal(requests.at(-1).options.headers, undefined);
        assert.equal(redirects.length, 0);
        await fetch(new Request('http://miner.test/api/reload', {method: 'POST', headers: {'Example': 'value'}}));
        assert.equal(requests.at(-1).options.headers.get('Example'), 'value');
        assert.equal(requests.at(-1).options.headers.get('X-TDM-Request'), '1');
    """)


def test_safe_translation_rendering_and_protection_controls():
    run(r"""
        const title = element('title', {dataset: {authText: 'title'}});
        ['web-auth-logout', 'web-auth-current-row', 'web-auth-current', 'web-auth-disable',
            'web-auth-save', 'web-auth-state'].forEach(id => element(id));
        responder = async () => ({ok: true, status: 200, json: async () => ({enabled: true,
            authenticated: true, translations: {...dashboardAuth.translations, title: '<img onerror=bad>'}})});
        await dashboardAuth.refresh();
        assert.equal(title.textContent, '<img onerror=bad>');
        assert.equal(elements.get('web-auth-current').required, true);
        assert.equal(elements.get('web-auth-logout').hidden, false);
        assert.equal(elements.get('web-auth-save').textContent, dashboardAuth.translations.change);
        responder = async () => ({ok: true, json: async () => ({enabled: true, authenticated: false, translations: {}})});
        await dashboardAuth.refresh();
        assert.deepEqual(redirects, ['/login']);
    """)
