"""Exercise the dashboard Pause menu without a backend."""

import subprocess

import pytest

from tests.javascript_helpers import APP_JS, NODE, extract_javascript_function


pytestmark = pytest.mark.skipif(NODE is None, reason="Node.js is required for frontend tests")


def test_pause_menu_picks_a_duration_and_resume_skips_the_menu():
    source = APP_JS.read_text(encoding="utf-8")
    script = "\n".join([
        extract_javascript_function(source, "setPauseMenuOpen"),
        extract_javascript_function(source, "updatePauseUI"),
        extract_javascript_function(source, "onPauseButton"),
        "async " + extract_javascript_function(source, "requestPause"),
    ]) + r"""
const assert = require('node:assert/strict');
const button = {
    textContent: '', disabled: false, attrs: {}, classes: new Set(),
    setAttribute(k, v) { this.attrs[k] = v; }, removeAttribute(k) { delete this.attrs[k]; },
    classList: { toggle(c, on) { on ? button.classes.add(c) : button.classes.delete(c); } },
    focus() { focused = 'button'; }
};
let focused = null;
const firstItem = { focus() { focused = 'first-item'; } };
const menu = { hidden: true, querySelector: () => firstItem };
const document = { getElementById: id => id === 'pause-menu' ? menu : button };
const state = { paused: false, translations: { gui: { status: { pause: 'Pausa', resume: 'Riprendi' } } } };
const lines = [];
function addConsoleLine(line) { lines.push(line); }
let fail = false; const bodies = [];
async function fetch(url, options) {
    assert.equal(url, '/api/pause');
    assert.ok(button.disabled);
    const body = JSON.parse(options.body); bodies.push(body);
    if (fail) return { ok: false, status: 500 };
    return { ok: true, async json() { return { success: true, paused: body.paused, resume_at: null }; } };
}
(async () => {
    updatePauseUI(false);
    assert.equal(button.textContent, 'Pausa ▾');
    assert.equal(button.attrs['aria-haspopup'], 'menu');
    onPauseButton();
    assert.equal(menu.hidden, false, 'pause opens the duration menu');
    assert.equal(button.attrs['aria-expanded'], 'true');
    assert.equal(focused, 'first-item', 'opening the menu focuses its first item');
    assert.equal(bodies.length, 0, 'opening the menu sends nothing');
    onPauseButton();
    assert.equal(menu.hidden, true, 'second click closes it');

    onPauseButton();
    const pending = requestPause(true, '1h');
    assert.equal(menu.hidden, true, 'menu closes before the request, so items cannot resubmit');
    await pending;
    assert.equal(focused, 'button', 'focus returns to the re-enabled button');
    assert.deepEqual(bodies.at(-1), { paused: true, duration: '1h' });
    assert.equal(state.paused, true);
    assert.equal(menu.hidden, true);
    assert.equal(button.textContent, 'Riprendi');
    assert.ok(button.classes.has('paused'));
    assert.equal(button.attrs['aria-haspopup'], undefined);
    assert.equal(button.attrs['aria-controls'], undefined);
    assert.equal(button.attrs['aria-expanded'], undefined, 'Resume is not a menu button');

    onPauseButton();  // paused: resumes directly, no menu
    assert.equal(menu.hidden, true);
    await new Promise(resolve => setImmediate(resolve));
    assert.deepEqual(bodies.at(-1), { paused: false, duration: null });
    assert.equal(state.paused, false);

    fail = true;
    onPauseButton();
    await requestPause(true, null);
    assert.equal(state.paused, false, 'failed request keeps the last confirmed state');
    assert.equal(menu.hidden, true);
    assert.equal(button.disabled, false);
    assert.equal(lines.length, 1);
})().catch(error => { console.error(error); process.exitCode = 1; });
"""
    result = subprocess.run([NODE, "-"], input=script, text=True, capture_output=True)
    assert result.returncode == 0, result.stderr
