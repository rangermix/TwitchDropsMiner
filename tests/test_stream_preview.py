"""The dashboard shows a canonical Twitch thumbnail without a preview toggle."""

import json
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


def run_preview_script(assertions: str):
    source = APP_JS.read_text()
    functions = extract_javascript_function(source, "updateNowWatching")
    script = HARNESS + source.split("// ==================== UI Utilities")[0] + functions
    script += r"""
state.channels = {7: watching};
state.translations = {gui: {channels: {now_watching: 'Watching', online: 'Online',
    viewers: 'viewers'}}};
""" + assertions
    result = subprocess.run([NODE, "-e", script], capture_output=True, text=True, encoding="utf-8")
    assert result.returncode == 0, result.stderr


@pytest.mark.skipif(NODE is None, reason="Node required for DOM behavior tests")
def test_watched_channel_loads_canonical_thumbnail_automatically():
    run_preview_script(r"""
updateNowWatching();
const img = document.getElementById('now-watching-img');
assert.match(img.style.backgroundImage, /live_user_some_streamer-440x248.jpg/);
assert.equal(document.getElementById('now-watching-preview').hidden, false);
assert.match(document.getElementById('now-watching-info').textContent, /配信者.*123/);
const source = img.dataset.src;
now += 1000;
updateNowWatching();
assert.equal(img.dataset.src, source, 'same minute reuses the thumbnail');
now += 60000;
updateNowWatching();
assert.notEqual(img.dataset.src, source, 'new minute may refresh the thumbnail');
""")


@pytest.mark.skipif(NODE is None, reason="Node required for DOM behavior tests")
def test_thumbnail_switches_channel_and_clears_source_when_watching_stops():
    run_preview_script(r"""
updateNowWatching();
const img = document.getElementById('now-watching-img');
state.channels = {8: {...watching, id: 8, login: 'other_streamer'}};
updateNowWatching();
assert.match(img.dataset.src, /live_user_other_streamer-/);
state.channels = {};
updateNowWatching();
assert.equal(img.style.backgroundImage, '');
assert.ok(!img.dataset.src);
assert.ok(document.getElementById('now-watching').classList.contains('hidden'));
state.channels = {7: watching};
updateNowWatching();
assert.match(img.dataset.src, /live_user_some_streamer-/);
state.channels = {7: {...watching, login: ''}};
updateNowWatching();
assert.equal(img.style.backgroundImage, '');
assert.ok(!img.dataset.src);
""")


@pytest.mark.skipif(NODE is None, reason="Node required for DOM behavior tests")
def test_thumbnail_title_updates_with_safe_translations():
    run_preview_script(r"""
updateNowWatching();
const source = document.getElementById('now-watching-img').dataset.src;
state.translations.gui.channels.now_watching = '<b>Chaîne regardée</b>';
updateNowWatching();
assert.equal(document.getElementById('now-watching-title').textContent, '<b>Chaîne regardée</b>');
assert.equal(document.getElementById('now-watching-img').dataset.src, source);
""")


def test_preview_toggle_is_removed_from_dashboard_and_locales():
    root = APP_JS.parents[2]
    source = APP_JS.read_text()
    assert 'previewEnabled' not in source
    assert 'toggleNowWatchingPreview' not in source
    assert 'now-watching-toggle' not in (root / 'web/index.html').read_text()
    removed = {'show_preview', 'hide_preview', 'preview_off', 'preview_help'}
    for locale in (root / 'lang').glob('*.json'):
        channels = json.loads(locale.read_text())['gui']['channels']
        assert removed.isdisjoint(channels), locale.name
