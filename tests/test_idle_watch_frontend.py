"""Idle watching channel list and the channel points claim and share toggles (now on the Points tab)."""

import json
import re
from pathlib import Path

import pytest

from tests.javascript_helpers import NODE
from tests.test_account_link_frontend import SETUP
from tests.test_reward_priority_frontend import REWARD_DOM
from tests.test_telegram_frontend import run_javascript


ROOT = Path(__file__).resolve().parents[1]
pytestmark = pytest.mark.skipif(NODE is None, reason="Node.js is required for frontend tests")
APP_JS = (ROOT / "web" / "static" / "app.js").read_text(encoding="utf-8")
# the constants of the per-channel options, which the extracted functions read
OPTION_CONSTANTS = APP_JS[APP_JS.index("const CHANNEL_OPTION_FEATURES"):APP_JS.index("let followImportBusy") + len("let followImportBusy = false;")]
POINTS_SETTINGS_JS = re.search(r"const POINTS_SETTINGS = \{.*?\n\};", APP_JS, re.S).group(0)
IDLE_FUNCTIONS = ["makeElement", "renderIdleChannels", "saveIdleChannels", "addIdleChannel",
                  "handleDragStart", "handleDragOver", "dragOverRows", "pointerPastMiddle", "handleDrop",
                  "globalChannelOption", "findOption", "readChannelOptions", "sortedJson", "labelled",
                  "buildChannelOptionsPanel", "withQueuedOptions", "saveChannelOptions", "importFollows"]

# REWARD_DOM supplies the fake DOM; add the idle controls and the state the functions read.
IDLE_DOM = REWARD_DOM + POINTS_SETTINGS_JS + OPTION_CONSTANTS + r"""
for (const id of ['idle-channels-title', 'idle-channels-help', 'idle-channels-list', 'idle-channel-input',
                  'idle-channel-add', 'idle-channels-error'])
    root.appendChild(Object.assign(new Node(id === 'idle-channel-input' ? 'input' : 'div'), {id}));
let idleChannelsSaving = false;
const idleChannelPattern = /^[a-z0-9_]{1,25}$/;
const idleChannelLimit = 20;
for (const id of ['idle-import-btn', 'idle-import-status'])
    root.appendChild(Object.assign(new Node(id === 'idle-import-btn' ? 'button' : 'p'), {id}));
Object.assign(state.translations.gui.settings, {idle_channels_save_error: 'Idle save failed',
    idle_channels_remove: 'Remove {channel}', idle_channels_up: 'Up {channel}', idle_channels_down: 'Down {channel}'});
state.settings.idle_channels = ['alpha', 'beta', 'gamma'];
const names = () => document.getElementById('idle-channels-list').children.map(row => row.dataset.channel);
const rowOf = name => document.getElementById('idle-channels-list').children.find(row => row.dataset.channel === name);
const press = (name, label) => rowOf(name).children.find(b => b.attrs['aria-label'] === label)
    .events.click();
const error = () => document.getElementById('idle-channels-error');
const input = () => document.getElementById('idle-channel-input');
function updateSettingsUI(settings) { state.settings = {...state.settings, ...settings}; renderIdleChannels(); }
const tick = () => new Promise(resolve => setTimeout(resolve, 0));
"""


def run_idle(body: str) -> None:
    run_javascript(IDLE_FUNCTIONS, IDLE_DOM + "(async () => {\n" + body
                   + "\n})().catch(error => { console.error(error); process.exit(1); });")


def test_idle_list_renders_in_order_with_escaped_names():
    run_idle(r"""
state.settings.idle_channels = ['alpha', '<b>x</b>', 'gamma'];
renderIdleChannels();
assert.deepEqual(names(), ['alpha', '<b>x</b>', 'gamma']);
const row = rowOf('<b>x</b>');
assert.ok(row.textContent.includes('<b>x</b>'), 'name is text');
assert.ok(row.children.every(child => child.tag !== 'b'), 'no element created from the name');
""")


def test_rows_use_the_sortable_pattern_and_disable_arrows_at_the_ends():
    run_idle(r"""
renderIdleChannels();
const row = rowOf('beta');
assert.ok(row.classes.has('sortable-item'));
assert.equal(row.draggable, true);
const [handle, number, name, toggle, up, down, remove] = row.children;
assert.ok(toggle.attrs['aria-label'] === 'Options of beta' && toggle.attrs['aria-expanded'] === 'false');
assert.ok(handle.classes.has('drag-handle') && handle.attrs['aria-hidden'] === 'true');
assert.ok(number.classes.has('priority-number'));
assert.equal(number.textContent, '2');
assert.ok(name.classes.has('game-name'));
assert.equal(name.textContent, 'beta');
assert.deepEqual([up, down].map(b => b.attrs.class), ['small-btn', 'small-btn']);
assert.equal(remove.attrs.class, 'remove-btn');
assert.deepEqual(['alpha', 'beta', 'gamma'].map(n => rowOf(n).children[1].textContent), ['1', '2', '3']);
const arrows = n => rowOf(n).children.filter(b => ['up', 'down'].includes(b.dataset.action)).map(b => b.disabled);
assert.deepEqual(arrows('alpha'), [true, false]);
assert.deepEqual(arrows('beta'), [false, false]);
assert.deepEqual(arrows('gamma'), [false, true]);
""")


def test_drag_reorders_and_saves_the_new_order_but_not_during_a_save():
    run_idle(r"""
const calls = [];
global.fetch = async (url, options) => {
    const body = JSON.parse(options.body); calls.push(body);
    return {ok: true, json: async () => ({success: true, settings: {idle_channels: body.idle_channels}})};
};
renderIdleChannels();
const event = target => ({target, preventDefault() {}, dataTransfer: {setData() {}, effectAllowed: '', dropEffect: ''}});
const dragged = rowOf('gamma');
handleDragStart(event(dragged));
handleDragOver(event(rowOf('alpha')));
assert.deepEqual(names(), ['gamma', 'alpha', 'beta']);
dragged.events.dragend(event(dragged)); await tick();
assert.deepEqual(calls.pop(), {idle_channels: ['gamma', 'alpha', 'beta']});
assert.deepEqual(names(), ['gamma', 'alpha', 'beta']);
// dropping a row back where it was sends nothing
const same = rowOf('gamma');
handleDragStart(event(same));
same.events.dragend(event(same)); await tick();
assert.equal(calls.length, 0);
// while a save is in flight the pending order shows and rows are not draggable
let finish;
global.fetch = () => new Promise(resolve => { finish = resolve; });
const pending = saveIdleChannels(['beta', 'gamma', 'alpha']);
assert.deepEqual(names(), ['beta', 'gamma', 'alpha']);
assert.ok(rowOf('gamma').draggable === false);
finish({ok: false}); await pending;
assert.deepEqual(names(), ['gamma', 'alpha', 'beta']); // the failed save shows the saved order again
assert.ok(rowOf('gamma').draggable === true);
""")


def test_add_move_remove_save_the_new_order():
    run_idle(r"""
const calls = [];
global.fetch = async (url, options) => {
    const body = JSON.parse(options.body); calls.push(body);
    return {ok: true, json: async () => ({success: true, settings: {idle_channels: body.idle_channels}})};
};
renderIdleChannels();
input().value = ' Delta ';
await addIdleChannel();
assert.deepEqual(calls.pop(), {idle_channels: ['alpha', 'beta', 'gamma', 'delta']});
assert.deepEqual(names(), ['alpha', 'beta', 'gamma', 'delta']);
assert.equal(input().value, '');
press('beta', 'Up beta'); await tick();
assert.deepEqual(calls.pop(), {idle_channels: ['beta', 'alpha', 'gamma', 'delta']});
press('alpha', 'Down alpha'); await tick();
assert.deepEqual(calls.pop(), {idle_channels: ['beta', 'gamma', 'alpha', 'delta']});
press('gamma', 'Remove gamma'); await tick();
assert.deepEqual(calls.pop(), {idle_channels: ['beta', 'alpha', 'delta']});
assert.deepEqual(names(), ['beta', 'alpha', 'delta']);

// failed save rolls back and shows the translated error
global.fetch = async () => ({ok: false});
press('delta', 'Up delta'); await tick();
assert.deepEqual(names(), ['beta', 'alpha', 'delta']);
assert.deepEqual(state.settings.idle_channels, ['beta', 'alpha', 'delta']);
assert.equal(error().hidden, false);
assert.equal(error().textContent, 'Idle save failed');
""")


@pytest.mark.parametrize("value", ["bad-name", "", "a" * 26, "alpha", "UPPER-case",
                                   "Kelvin", " foo", "café"])
def test_add_rejects_invalid_login_client_side(value):
    run_idle(f"""
let sent = 0;
global.fetch = async () => {{ sent++; return {{ok: true}}; }};
renderIdleChannels();
input().value = {json.dumps(value)};
await addIdleChannel();
assert.equal(sent, 0);
assert.equal(error().hidden, false);
assert.equal(error().textContent, 'Idle save failed');
assert.deepEqual(names(), ['alpha', 'beta', 'gamma']);
""")


def test_add_rejects_the_twenty_first_channel():
    run_idle(r"""
state.settings.idle_channels = Array.from({length: 20}, (_, i) => 'c' + i);
let sent = 0;
global.fetch = async () => { sent++; return {ok: true}; };
renderIdleChannels();
input().value = 'extra';
await addIdleChannel();
assert.equal(sent, 0);
assert.equal(error().hidden, false);
""")


CLAIM_SETUP = SETUP + r"""
state.translations.gui.settings.claim_channel_points_save_error = '<b>Claim save failed</b>';
const claim = document.getElementById('claim-channel-points');
const claimError = document.getElementById('claim-channel-points-error');
"""
CLAIM_FUNCTIONS = ["onClaimChannelPointsChange", "saveBooleanSetting", "updateSettingsUI", "toggleMiningHoursRange"]


def test_claim_toggle_saves_like_prefer_followed():
    run_javascript(CLAIM_FUNCTIONS, CLAIM_SETUP + r"""
(async () => {
    updateSettingsUI({});
    assert.equal(claim.checked, true, 'on unless the server says false');
    updateSettingsUI({claim_channel_points: false}, true);
    assert.equal(claim.checked, false);
    claim.checked = true;
    global.fetch = async (url, options) => {
        assert.equal(url, '/api/settings');
        assert.deepEqual(JSON.parse(options.body), {claim_channel_points: true});
        return {ok: true, json: async () => ({success: true, settings: {claim_channel_points: true}})};
    };
    await onClaimChannelPointsChange();
    assert.equal(claim.checked, true);
    assert.equal(claim.disabled, false);
    assert.equal(state.settings.claim_channel_points, true);
    // failed save restores the confirmed value and shows the literal error
    claim.checked = false;
    global.fetch = async () => ({ok: false});
    await onClaimChannelPointsChange();
    assert.equal(claim.checked, true);
    assert.equal(claimError.hidden, false);
    assert.equal(claimError.textContent, '<b>Claim save failed</b>');
})().catch(error => { console.error(error); process.exitCode = 1; });
""")


def test_markup_and_translations_exist():
    html = (ROOT / "web" / "index.html").read_text(encoding="utf-8")
    for fragment in ('id="claim-channel-points"', 'id="claim-channel-points-error"',
                     # "ordered" shows the drag handle, the move cursor and the hover like the other ordered lists
                     'id="idle-channels-list" class="reward-type-list ordered"',
                     'id="idle-channel-input"', 'id="idle-channel-add"', 'id="idle-channels-error"'):
        assert fragment in html, fragment
    # the static English text shown before the translations load is the English translation
    english = json.loads((ROOT / "lang" / "English.json").read_text(encoding="utf-8"))["gui"]["settings"]
    for element, key in (("idle-channels-title", "idle_channels"), ("idle-channels-help", "idle_channels_help")):
        assert re.search(rf'id="{element}"[^>]*>{re.escape(english[key])}<', html), key
    keys = ("idle_channels", "idle_channels_help", "idle_channels_add", "idle_channels_remove", "idle_channels_up",
            "idle_channels_down", "idle_channels_save_error", "claim_channel_points", "claim_channel_points_help",
            "claim_channel_points_save_error")
    for locale in (ROOT / "lang").glob("*.json"):
        settings = json.loads(locale.read_text(encoding="utf-8"))["gui"]["settings"]
        for key in keys:
            assert settings[key].strip(), (locale.name, key)


def test_keyboard_focus_stays_on_the_moved_row_control():
    run_idle(r"""
global.fetch = async (url, options) => {
    const body = JSON.parse(options.body);
    return {ok: true, json: async () => ({success: true, settings: {idle_channels: body.idle_channels}})};
};
renderIdleChannels();
const control = (name, action) => rowOf(name).children.find(b => b.dataset.action === action);
control('gamma', 'up').focus();
for (let step = 0; step < 2; step++) {
    press('gamma', 'Up gamma'); await tick();
    }
assert.deepEqual(names(), ['gamma', 'alpha', 'beta']);
assert.equal(document.activeElement, control('gamma', 'down'), 'at the top the up arrow is disabled: focus moves to down');
control('alpha', 'down').focus();
press('alpha', 'Down alpha'); await tick();
assert.deepEqual(names(), ['gamma', 'beta', 'alpha']);
assert.equal(document.activeElement, control('alpha', 'up'), 'at the bottom focus falls back to up');
// a broadcast re-render keeps focus, and focus outside the list is left alone
control('alpha', 'up').focus();
updateSettingsUI({idle_channels: ['gamma', 'beta', 'alpha']});
assert.equal(document.activeElement, control('alpha', 'up'));
const elsewhere = Object.assign(new Node('button'), {id: 'elsewhere'}); root.appendChild(elsewhere);
elsewhere.focus(); renderIdleChannels();
assert.equal(document.activeElement, elsewhere);
// a second keypress while a save is in flight is ignored, not queued
let finish; let sent = 0;
global.fetch = () => { sent++; return new Promise(resolve => { finish = resolve; }); };
control('alpha', 'up').focus();
press('alpha', 'Up alpha'); press('alpha', 'Up alpha');
assert.equal(sent, 1);
finish({ok: false}); await tick();
""")


def test_mixed_case_login_is_normalized_before_sending_without_error():
    run_idle(r"""
const calls = [];
global.fetch = async (url, options) => {
    calls.push(JSON.parse(options.body));
    return {ok: true, json: async () => ({success: true, settings: {idle_channels: calls.at(-1).idle_channels}})};
};
renderIdleChannels();
input().value = ' Foo ';
await addIdleChannel();
assert.deepEqual(calls, [{idle_channels: ['alpha', 'beta', 'gamma', 'foo']}]);
assert.equal(error().hidden, true);
assert.equal(input().value, '');
""")


def test_claim_rollback_before_load_keeps_the_default_on():
    run_javascript(CLAIM_FUNCTIONS, CLAIM_SETUP + r"""
(async () => {
    claim.checked = false;
    global.fetch = async () => ({ok: false});
    await onClaimChannelPointsChange();
    assert.equal(claim.checked, true, 'unknown server value means the default, on');
})().catch(error => { console.error(error); process.exitCode = 1; });
""")


def test_share_toggle_saves_and_keeps_the_default_off_on_rollback():
    run_javascript(CLAIM_FUNCTIONS + ["onShareWatchStreaksChange"], SETUP + r"""
state.translations.gui.settings.share_watch_streaks_save_error = '<b>Share save failed</b>';
const share = document.getElementById('share-watch-streaks');
const shareError = document.getElementById('share-watch-streaks-error');
(async () => {
    updateSettingsUI({});
    assert.equal(share.checked, false, 'off unless the server says true');
    updateSettingsUI({share_watch_streaks: true}, true);
    assert.equal(share.checked, true);
    updateSettingsUI({share_watch_streaks: false}, true);
    assert.equal(share.checked, false);
    share.checked = true;
    global.fetch = async (url, options) => {
        assert.deepEqual(JSON.parse(options.body), {share_watch_streaks: true});
        return {ok: true, json: async () => ({success: true, settings: {share_watch_streaks: true}})};
    };
    await onShareWatchStreaksChange();
    assert.equal(share.checked, true);
    assert.equal(state.settings.share_watch_streaks, true);
    // a failed save before the server value is known keeps the default (off) and shows the literal error
    state.settings = {};
    share.checked = true;
    global.fetch = async () => ({ok: false});
    await onShareWatchStreaksChange();
    assert.equal(share.checked, false);
    assert.equal(shareError.hidden, false);
    assert.equal(shareError.textContent, '<b>Share save failed</b>');
})().catch(error => { console.error(error); process.exitCode = 1; });
""")


def test_share_markup_and_translations_exist():
    html = (ROOT / "web" / "index.html").read_text(encoding="utf-8")
    for fragment in ('id="share-watch-streaks"', 'id="share-watch-streaks-error"'):
        assert fragment in html, fragment
    for locale in (ROOT / "lang").glob("*.json"):
        settings = json.loads(locale.read_text(encoding="utf-8"))["gui"]["settings"]
        for key in ("share_watch_streaks", "share_watch_streaks_help", "share_watch_streaks_save_error"):
            assert settings[key].strip(), (locale.name, key)


def test_a_reply_with_another_list_is_a_failed_save():
    run_idle(r"""
renderIdleChannels();
global.fetch = async () => ({ok: true, json: async () => ({success: true, settings: {idle_channels: ['other']}})});
assert.equal(await saveIdleChannels(['beta', 'alpha', 'gamma']), false);
assert.equal(error().hidden, false);
assert.equal(error().textContent, 'Idle save failed');
assert.deepEqual(names(), ['alpha', 'beta', 'gamma']);
assert.deepEqual(state.settings.idle_channels, ['alpha', 'beta', 'gamma']);
""")


def test_a_partial_settings_reply_keeps_the_idle_list():
    run_javascript(CLAIM_FUNCTIONS, CLAIM_SETUP + r"""
state.settings.idle_channels = ['alpha', 'beta'];
updateSettingsUI({prefer_followed_channels: true}, false, ['prefer_followed_channels']);
assert.deepEqual(state.settings.idle_channels, ['alpha', 'beta']);
updateSettingsUI({idle_channels: ['gamma']}, true);  // an authoritative reply replaces it
assert.deepEqual(state.settings.idle_channels, ['gamma']);
""")


def test_a_later_successful_queued_save_hides_the_error():
    run_idle(r"""
state.settings.idle_channel_options = {};
let calls = 0, release;
global.fetch = async (url, options) => {
    calls += 1;
    if (calls === 1) { await new Promise(resolve => { release = resolve; }); return {ok: false}; }
    const sent = JSON.parse(options.body).idle_channel_options;
    return {ok: true, json: async () => ({success: true, settings: {idle_channel_options: sent}})};
};
const first = saveChannelOptions('alpha', {favorite: true});
await tick();
await saveChannelOptions('beta', {favorite: true});  // queued behind the first
release();
assert.equal(await first, true);
assert.equal(calls, 2);
assert.equal(error().hidden, true);
""")


def test_the_change_in_flight_survives_a_rerender_until_the_reply():
    run_idle(r"""
state.settings.idle_channel_options = {};
let release, sent = [];
global.fetch = async (url, options) => {
    sent.push(JSON.parse(options.body).idle_channel_options);
    if (sent.length === 1) await new Promise(resolve => { release = resolve; });
    const body = sent[sent.length - 1];
    return {ok: true, json: async () => ({success: true, settings: {idle_channel_options: body}})};
};
const first = saveChannelOptions('alpha', {favorite: true});
await tick();
assert.deepEqual(withQueuedOptions(state.settings.idle_channel_options), {alpha: {favorite: true}});
await saveChannelOptions('beta', {favorite: true});
release();
await first;
assert.deepEqual(sent[1], {alpha: {favorite: true}, beta: {favorite: true}});
assert.deepEqual(withQueuedOptions({}), {});  // nothing stays after the replies
""")
