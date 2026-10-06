"""The optional dashboard thumbnail must not load until explicitly enabled."""

import subprocess

import pytest

from tests.javascript_helpers import APP_JS, NODE, extract_javascript_function


HARNESS = r"""
const assert = require('node:assert/strict');
const elements = new Map();
function element(id) {
    const classes = new Set();
    return {dataset: {}, style: {}, hidden: false, textContent: '', disabled: false,
        attributes: {}, classList: {
            add(name) { classes.add(name); }, remove(name) { classes.delete(name); },
            contains(name) { return classes.has(name); }
        }, setAttribute(name, value) { this.attributes[name] = value; }};
}
const document = {getElementById(id) {
    if (!elements.has(id)) elements.set(id, element(id));
    return elements.get(id);
}};
let now = 120000;
Date.now = () => now;
const watching = {id: 7, name: '配信者', login: 'some_streamer', online: true,
    watching: true, viewers: 123};
"""


def run_preview_script(assertions: str, *, with_toggle: bool = False):
    source = APP_JS.read_text()
    functions = extract_javascript_function(source, "updateNowWatching")
    if with_toggle:
        functions += extract_javascript_function(source, "toggleNowWatchingPreview")
    script = HARNESS + source.split("// ==================== UI Utilities")[0] + functions
    script += r"""
state.channels = {7: watching};
state.translations = {gui: {channels: {now_watching: 'Watching', online: 'Online',
    show_preview: 'Show', hide_preview: 'Hide', preview_off: 'Off',
    preview_help: 'Extra bandwidth', viewers: 'viewers'}}};
""" + assertions
    result = subprocess.run([NODE, "-e", script], capture_output=True, text=True, encoding="utf-8")
    assert result.returncode == 0, result.stderr


@pytest.mark.skipif(NODE is None, reason="Node required for DOM behavior tests")
def test_preview_is_off_by_default_through_repeated_channel_updates():
    run_preview_script(r"""
for (let i = 0; i < 3; i++) {
    updateNowWatching();
    const img = document.getElementById('now-watching-img');
    assert.ok(!img.style.backgroundImage, 'default must not load a Twitch thumbnail');
    assert.ok(!img.dataset.src);
    assert.equal(document.getElementById('now-watching-preview').hidden, true);
    now += 60000;
}
assert.equal(state.previewEnabled, false);
assert.match(document.getElementById('now-watching-info').textContent, /配信者.*123/);
assert.equal(document.getElementById('now-watching-toggle').attributes['aria-pressed'], 'false');
""")


@pytest.mark.skipif(NODE is None, reason="Node required for DOM behavior tests")
def test_preview_opt_in_uses_login_and_clears_source_when_disabled_or_no_channel():
    run_preview_script(r"""
updateNowWatching();
toggleNowWatchingPreview();
const img = document.getElementById('now-watching-img');
const button = document.getElementById('now-watching-toggle');
assert.match(img.style.backgroundImage, /live_user_some_streamer-440x248.jpg/);
assert.equal(document.getElementById('now-watching-preview').hidden, false);
assert.equal(button.textContent, 'Hide');
assert.equal(button.attributes['aria-pressed'], 'true');
const firstSource = img.dataset.src;
now += 1000;
updateNowWatching();
assert.equal(img.dataset.src, firstSource);
now += 60000;
updateNowWatching();
assert.notEqual(img.dataset.src, firstSource);
state.channels[7] = {...watching, login: 'other_streamer'};
updateNowWatching();
assert.match(img.dataset.src, /live_user_other_streamer-/);
toggleNowWatchingPreview();
assert.equal(img.style.backgroundImage, '');
assert.ok(!img.dataset.src);
assert.equal(button.textContent, 'Show');
now += 60000;
updateNowWatching();
assert.equal(img.style.backgroundImage, '');
toggleNowWatchingPreview();
assert.match(img.dataset.src, /live_user_other_streamer-/);
state.channels = {};
updateNowWatching();
assert.equal(img.style.backgroundImage, '');
assert.ok(!img.dataset.src);
assert.equal(state.previewEnabled, false);
assert.equal(button.disabled, true);
assert.ok(document.getElementById('now-watching').classList.contains('hidden'));
state.channels = {7: watching};
updateNowWatching();
assert.equal(img.style.backgroundImage, '');
""", with_toggle=True)


@pytest.mark.skipif(NODE is None, reason="Node required for DOM behavior tests")
def test_preview_updates_translations_without_enabling_it():
    run_preview_script(r"""
updateNowWatching();
state.translations.gui.channels.show_preview = '<b>Afficher</b>';
state.translations.gui.channels.now_watching = 'Chaîne regardée';
updateNowWatching();
assert.equal(document.getElementById('now-watching-toggle').textContent, '<b>Afficher</b>');
assert.equal(document.getElementById('now-watching-title').textContent, 'Chaîne regardée');
assert.ok(!document.getElementById('now-watching-img').style.backgroundImage);
""")
