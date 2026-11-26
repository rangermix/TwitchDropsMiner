"""Points tab: per-channel options panel of the idle list and the import of followed channels."""

import json
import re
from pathlib import Path

import pytest

from tests.javascript_helpers import NODE
from tests.test_idle_watch_frontend import run_idle


ROOT = Path(__file__).resolve().parents[1]
HTML = (ROOT / "web" / "index.html").read_text(encoding="utf-8")
CSS = (ROOT / "web" / "static" / "styles.css").read_text(encoding="utf-8")
APP_JS = (ROOT / "web" / "static" / "app.js").read_text(encoding="utf-8")
ENGLISH = json.loads((ROOT / "lang" / "English.json").read_text(encoding="utf-8"))["gui"]["points"]
pytestmark = pytest.mark.skipif(NODE is None, reason="Node.js is required for frontend tests")

HELPERS = r"""
const open = name => rowOf(name).children.find(b => b.dataset.action === 'options');
const panelOf = name => rowOf(name).children.find(c => c.classes.has('idle-options-panel'));
const option = (name, key) => findOption(panelOf(name), key);
const reply = (settings, ok = true) => { global.fetch = async (url, options) => {
    calls.push({url, body: options.body ? JSON.parse(options.body) : null});
    return {ok, status: ok ? 200 : 500, json: async () => ({success: true, settings})}; }; };
const calls = [];
"""


def run(body: str) -> None:
    run_idle(HELPERS + body)


def test_markup_has_the_import_button_with_a_status_line_and_the_strings_exist():
    assert re.search(r'<button type="button" id="idle-import-btn" class="small-btn" data-points-text="import_follows">',
                     HTML)
    assert 'id="idle-import-status" class="help-text" role="status"' in HTML
    for key in ("channel_options", "import_follows", "import_follows_result", "opt_points_limit"):
        assert key in ENGLISH
    assert "innerHTML" not in APP_JS[APP_JS.index("function buildChannelOptionsPanel"):APP_JS.index("async function importFollows")]
    assert ".idle-options-panel" in CSS


def test_the_row_expands_and_collapses_with_aria_expanded_and_keeps_focus_on_the_button():
    run(r"""
renderIdleChannels();
assert.equal(panelOf('beta'), undefined);
assert.equal(open('beta').attrs['aria-expanded'], 'false');
assert.equal(open('beta').tag, 'button');
open('beta').focus();
open('beta').events.click();
assert.ok(panelOf('beta'));
assert.equal(open('beta').attrs['aria-expanded'], 'true');
assert.equal(open('beta').attrs['aria-controls'], panelOf('beta').attrs.id);
assert.equal(rowOf('beta').draggable, false);
assert.equal(rowOf('alpha').draggable, true);
assert.equal(document.activeElement, open('beta'));
open('beta').events.click();
assert.equal(panelOf('beta'), undefined);
assert.equal(open('beta').attrs['aria-expanded'], 'false');
assert.equal(rowOf('beta').draggable, true);
assert.equal(document.activeElement, open('beta'));
// a removed channel forgets its open state
open('beta').events.click();
state.settings.idle_channels = ['alpha', 'gamma'];
renderIdleChannels();
state.settings.idle_channels = ['alpha', 'beta', 'gamma'];
renderIdleChannels();
assert.equal(panelOf('beta'), undefined);
""")


def test_controls_show_the_stored_values_and_the_default_label_follows_the_global_setting():
    run(r"""
Object.assign(state.settings, {claim_channel_points: true, follow_raids: false, claim_moments: true,
    predictions_enabled: false, idle_streak_first: true, join_chat: false});
state.settings.idle_channel_options = {beta: {bonus: false, raids: true, favorite: true, points_limit: 250}};
renderIdleChannels();
open('beta').events.click();
const select = key => option('beta', key);
const labels = key => select(key).children.map(o => o.textContent);
assert.deepEqual(labels('bonus'), ['Default (on)', 'On', 'Off']);
assert.deepEqual(labels('raids'), ['Default (off)', 'On', 'Off']);
assert.deepEqual(['bonus', 'raids', 'moments', 'predictions', 'streaks', 'chat'].map(k => select(k).value),
    ['off', 'on', 'default', 'default', 'default', 'default']);
assert.equal(option('beta', 'favorite').checked, true);
assert.equal(option('beta', 'streak_only').checked, false);
assert.equal(option('beta', 'points_limit').value, '250');
assert.equal(option('beta', 'points_limit').attrs.min, '1');
assert.equal(option('beta', 'points_limit').attrs.max, '100000000');
// the global value changes the default label
state.settings.claim_channel_points = false;
renderIdleChannels();
assert.equal(labels('bonus')[0], 'Default (off)');
// the marker shows for channels with options only
const marker = name => rowOf(name).children.some(c => c.classes.has('idle-option-marker'));
assert.deepEqual(['alpha', 'beta', 'gamma'].map(marker), [false, true, false]);
// no element is built from option text
assert.ok(walk(panelOf('beta')).every(node => node.tag !== 'b'));
""")


def test_a_change_saves_the_whole_options_object_and_shows_the_confirmed_value():
    run(r"""
state.settings.idle_channel_options = {alpha: {chat: true}};
renderIdleChannels();
open('beta').events.click();
open('beta').focus();
option('beta', 'predictions').value = 'on';
option('beta', 'favorite').checked = true;
option('beta', 'points_limit').value = '99.9';
reply({idle_channel_options: {alpha: {chat: true}, beta: {predictions: true, favorite: true, points_limit: 99}}});
option('beta', 'favorite').focus();
option('beta', 'predictions').events.change();
await tick();
assert.deepEqual(calls.pop(), {url: '/api/settings', body: {idle_channel_options:
    {alpha: {chat: true}, beta: {predictions: true, favorite: true, points_limit: 99}}}});
assert.deepEqual(state.settings.idle_channel_options.beta, {predictions: true, favorite: true, points_limit: 99});
assert.equal(option('beta', 'predictions').value, 'on');
assert.equal(option('beta', 'points_limit').value, '99');
// focus stays on the control after the re-render
option('beta', 'favorite').focus();
reply({idle_channel_options: {alpha: {chat: true}, beta: {predictions: true, points_limit: 99}}});
option('beta', 'favorite').checked = false;
option('beta', 'favorite').events.change();
await tick();
assert.equal(document.activeElement, option('beta', 'favorite'));
""")


def test_clamping_and_clearing_options():
    run(r"""
state.settings.idle_channel_options = {};
renderIdleChannels();
open('alpha').events.click();
const send = async value => {
    option('alpha', 'points_limit').value = value;
    reply({idle_channel_options: {}});
    option('alpha', 'points_limit').events.change(); await tick();
    return calls.pop().body.idle_channel_options.alpha;
};
assert.deepEqual(await send('0'), {points_limit: 1});
assert.deepEqual(await send('999999999999'), {points_limit: 100000000});
assert.deepEqual(await send('-5'), {points_limit: 1});
assert.equal(await send(''), undefined);  // empty: the channel has no entry
assert.equal(await send('abc'), undefined);
// everything default again removes the entry
state.settings.idle_channel_options = {alpha: {bonus: true}};
renderIdleChannels();
option('alpha', 'bonus').value = 'default';
reply({idle_channel_options: {}});
option('alpha', 'bonus').events.change(); await tick();
assert.deepEqual(calls.pop().body, {idle_channel_options: {}});
assert.deepEqual(state.settings.idle_channel_options, {});
""")


@pytest.mark.parametrize("failure", ["http", "rejected", "mismatch", "network"])
def test_a_failed_save_rolls_back_to_the_confirmed_options_with_a_translated_error(failure):
    run(r"""
state.translations.gui.points = {channel_options_save_error: 'Options failed'};
state.settings.idle_channel_options = {beta: {chat: true}};
renderIdleChannels();
open('beta').events.click();
option('beta', 'chat').value = 'off';
option('beta', 'moments').value = 'on';
const failure = '""" + failure + r"""';
global.fetch = async () => {
    if (failure === 'network') throw new Error('offline');
    if (failure === 'http') return {ok: false, status: 500};
    if (failure === 'rejected') return {ok: true, json: async () => ({success: false})};
    return {ok: true, json: async () => ({success: true, settings: {idle_channel_options: {beta: {chat: false}}}})};
};
option('beta', 'chat').events.change(); await tick();
assert.deepEqual(state.settings.idle_channel_options, {beta: {chat: true}});
assert.equal(option('beta', 'chat').value, 'on');
assert.equal(option('beta', 'moments').value, 'default');
const error = document.getElementById('idle-channels-error');
assert.equal(error.hidden, false);
assert.equal(error.textContent, 'Options failed');
""")


QUEUE_FETCH = r"""
const pending = [];
global.fetch = (url, options) => {
    calls.push(JSON.parse(options.body));
    return new Promise(resolve => pending.push(resolve));
};
const answer = (ok, options) => pending.shift()(ok
    ? {ok: true, json: async () => ({success: true, settings: {idle_channel_options: options}})}
    : {ok: false, status: 500});
"""


def test_changes_while_saving_are_queued_and_sent_after_it_with_the_last_change_winning():
    run(QUEUE_FETCH + r"""
state.settings.idle_channel_options = {};
renderIdleChannels();
open('alpha').events.click();
open('beta').events.click();
option('beta', 'chat').value = 'on';
option('beta', 'chat').events.change();
// while it is in flight: two changes of alpha (the last wins) and one more of beta
option('alpha', 'raids').value = 'on';
option('alpha', 'raids').events.change();
option('alpha', 'raids').value = 'off';
option('alpha', 'raids').events.change();
option('beta', 'moments').value = 'on';
option('beta', 'moments').events.change();
assert.equal(calls.length, 1);
answer(true, {beta: {chat: true}});
await tick(); await tick();
// one save after the first, on top of the confirmed options
assert.deepEqual(calls, [{idle_channel_options: {beta: {chat: true}}},
    {idle_channel_options: {beta: {chat: true, moments: true}, alpha: {raids: false}}}]);
assert.equal(option('alpha', 'raids').value, 'off');  // not reset to the confirmed value meanwhile
answer(true, {beta: {chat: true, moments: true}, alpha: {raids: false}});
await tick(); await tick();
assert.deepEqual(state.settings.idle_channel_options, {beta: {chat: true, moments: true}, alpha: {raids: false}});
assert.equal(option('alpha', 'raids').value, 'off');
assert.equal(option('beta', 'moments').value, 'on');
assert.equal(calls.length, 2);
""")


def test_a_failed_queued_save_rolls_back_to_the_confirmed_options():
    run(QUEUE_FETCH + r"""
state.translations.gui.points = {channel_options_save_error: 'Options failed'};
state.settings.idle_channel_options = {};
renderIdleChannels();
open('beta').events.click();
option('beta', 'chat').value = 'on';
option('beta', 'chat').events.change();
option('beta', 'raids').value = 'on';
option('beta', 'raids').events.change();
answer(true, {beta: {chat: true}});
await tick(); await tick();
assert.deepEqual(calls[1], {idle_channel_options: {beta: {chat: true, raids: true}}});
answer(false);
await tick(); await tick();
assert.deepEqual(state.settings.idle_channel_options, {beta: {chat: true}});
assert.equal(option('beta', 'raids').value, 'default');
assert.equal(option('beta', 'chat').value, 'on');
assert.equal(document.getElementById('idle-channels-error').hidden, false);
""")


def test_a_queued_change_after_a_failed_save_goes_on_top_of_the_confirmed_options():
    run(QUEUE_FETCH + r"""
state.settings.idle_channel_options = {gamma: {bonus: true}};
renderIdleChannels();
open('beta').events.click();
open('alpha').events.click();
option('beta', 'chat').value = 'on';
option('beta', 'chat').events.change();
option('alpha', 'raids').value = 'on';
option('alpha', 'raids').events.change();
answer(false);
await tick(); await tick();
// beta's failed change is not sent again; alpha's queued one is
assert.deepEqual(calls[1], {idle_channel_options: {gamma: {bonus: true}, alpha: {raids: true}}});
answer(true, {gamma: {bonus: true}, alpha: {raids: true}});
await tick(); await tick();
assert.equal(option('beta', 'chat').value, 'default');
assert.equal(option('alpha', 'raids').value, 'on');
""")


def test_removing_a_channel_takes_the_pruned_options_from_the_reply():
    run(r"""
state.settings.idle_channel_options = {alpha: {chat: true}, beta: {favorite: true}};
renderIdleChannels();
global.fetch = async (url, options) => ({ok: true, json: async () => ({success: true, settings:
    {idle_channels: ['alpha', 'gamma'], idle_channel_options: {alpha: {chat: true}}}})});
// the stub updateSettingsUI merges the reply, so the options are the pruned ones
await saveIdleChannels(['alpha', 'gamma']);
assert.deepEqual(state.settings.idle_channel_options, {alpha: {chat: true}});
""")


def test_the_app_keeps_the_options_across_unrelated_replies_but_takes_them_when_the_idle_list_saves():
    # updateSettingsUI keeps idle_channel_options unless the save names it
    assert "'idle_channel_options', 'claim_channel_points'" in APP_JS
    assert "['idle_channels', 'idle_channel_options']" in APP_JS


# --- import ---


def test_import_disables_the_button_and_shows_the_counts():
    run(r"""
state.translations.gui.points = {import_follows_result: '{added}+{listed}+{over}'};
let finish;
global.fetch = (url, options) => { calls.push({url, method: options.method}); return new Promise(resolve => { finish = resolve; }); };
const button = document.getElementById('idle-import-btn');
const status = document.getElementById('idle-import-status');
const pending = importFollows();
assert.equal(button.disabled, true);
importFollows();  // a second click while it runs sends nothing
assert.equal(calls.length, 1);
assert.deepEqual(calls[0], {url: '/api/points/import-follows', method: 'POST'});
finish({ok: true, status: 200, json: async () => ({success: true, added: 3, already_listed: 2, over_limit: 7})});
await pending;
assert.equal(button.disabled, false);
assert.equal(status.textContent, '3+2+7');
assert.equal(document.getElementById('idle-channels-error').hidden, true);
// the next run clears the old line first
global.fetch = () => new Promise(() => {});
importFollows();
assert.equal(status.textContent, '');
""")


@pytest.mark.parametrize(("status", "expected"), [(409, 'Log in first'), (502, 'Import failed'), (500, 'Import failed')])
def test_a_failed_import_shows_a_translated_error_and_enables_the_button(status, expected):
    run(r"""
state.translations.gui.points = {import_follows_error: 'Import failed', import_follows_login: 'Log in first'};
global.fetch = async () => ({ok: false, status: """ + str(status) + r""", json: async () => ({detail: 'x'})});
await importFollows();
const error = document.getElementById('idle-channels-error');
assert.equal(error.hidden, false);
assert.equal(error.textContent, '""" + expected + r"""');
assert.equal(document.getElementById('idle-import-btn').disabled, false);
assert.equal(document.getElementById('idle-import-status').textContent, '');
""")


def test_an_unusable_import_reply_is_an_error():
    run(r"""
global.fetch = async () => ({ok: true, status: 200, json: async () => ({success: true, added: '3', already_listed: 0, over_limit: 0})});
await importFollows();
assert.equal(document.getElementById('idle-channels-error').hidden, false);
assert.equal(document.getElementById('idle-import-status').textContent, '');
""")
