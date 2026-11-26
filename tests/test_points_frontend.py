"""Points tab: markup and order, state rendering, points log, pause button, moved and new settings."""

import json
import re
from html import unescape
from pathlib import Path

import pytest

from src.config.settings import PointsSettings, default_settings
from tests.javascript_helpers import NODE
from tests.test_reward_priority_frontend import REWARD_DOM
from tests.test_telegram_frontend import run_javascript


ROOT = Path(__file__).resolve().parents[1]
HTML = (ROOT / "web" / "index.html").read_text(encoding="utf-8")
APP_JS = (ROOT / "web" / "static" / "app.js").read_text(encoding="utf-8")
CSS = (ROOT / "web" / "static" / "styles.css").read_text(encoding="utf-8")
ENGLISH = json.loads((ROOT / "lang" / "English.json").read_text(encoding="utf-8"))["gui"]
POINTS_SETTINGS_JS = re.search(r"const POINTS_SETTINGS = \{.*?\n\};", APP_JS, re.S).group(0)
FUNCTIONS = ["makeElement", "appendLogLine", "updatePointsState", "formatPointsGain", "renderPoints", "translatePoints",
             "togglePointsPause", "renderPointsSettings", "savePointsSetting"]
needs_node = pytest.mark.skipif(NODE is None, reason="Node.js is required for frontend tests")

POINTS_DOM = REWARD_DOM + POINTS_SETTINGS_JS + r"""
let pointsPauseBusy = false;
const add = (id, tag = 'div') => root.appendChild(Object.assign(new Node(tag), {id}));
for (const id of ['points-status', 'points-pause-error', 'points-empty', 'points-table']) add(id);
add('points-tbody', 'tbody'); add('points-pause-btn', 'button');
const log = add('points-log');
log.removeChild = child => { log.children = log.children.filter(item => item !== child); };
Object.defineProperty(log, 'firstChild', {get() { return log.children[0]; }});
for (const key of Object.keys(POINTS_SETTINGS)) {
    const kind = typeof POINTS_SETTINGS[key].default;
    add('points-setting-' + key, kind === 'boolean' ? 'input' : kind === 'string' ? 'select' : 'input');
    add('points-setting-' + key + '-error', 'p').hidden = true;
}
document.querySelectorAll = selector => walk(root).filter(node => selector === '[data-points-text]' && 'pointsText' in node.dataset);
state.points = {paused: false, active: false, channels: []};
state.translations.gui.points = {};
const tableRows = () => document.getElementById('points-tbody').children;
const cells = row => row.children.map(cell => cell.textContent);
const button = () => document.getElementById('points-pause-btn');
const control = key => document.getElementById('points-setting-' + key);
const failure = key => document.getElementById('points-setting-' + key + '-error');
function updateSettingsUI(settings) { state.settings = {...state.settings, ...settings}; renderPointsSettings(); }
"""


def socket_blocks(*names: str) -> str:
    """The source of the socket.on handlers of app.js, run against the fake DOM."""
    blocks = []
    for name in names:
        start = APP_JS.index(f"socket.on('{name}'")
        blocks.append(APP_JS[start:APP_JS.index("\n});", start) + 4])
    return "\n".join(blocks)


SOCKET_BLOCKS = socket_blocks("points_update", "points_paused_update", "points_output")


def run_points(body: str) -> None:
    run_javascript(FUNCTIONS, POINTS_DOM + "(async () => {\n" + body
                   + "\n})().catch(error => { console.error(error); process.exit(1); });")


def block(start: str, end: str) -> str:
    return HTML[HTML.index(start):HTML.index(end)]


def test_tab_sits_between_history_and_settings_with_its_icon_and_panel():
    tabs = re.findall(r'<button class="tab-button[^"]*" data-tab="(\w+)"', HTML)
    assert tabs == ["main", "inventory", "history", "points", "settings", "help"]
    assert 'id="points-tab" class="tab-content"' in HTML
    icon = ('.tab-button[data-tab="points"] { --tab-icon: url("data:image/svg+xml,%3Csvg%20xmlns=\'http://www.w3.org/2000/svg\''
            '%20viewBox=\'0%200%2024%2024\'%20fill=\'none\'%20stroke=\'black\'%20stroke-width=\'2\'%20stroke-linecap=\'round\''
            '%20stroke-linejoin=\'round\'%3E%3Ccircle%20cx=\'12\'%20cy=\'12\'%20r=\'9\'/%3E%3Cpath%20d=\'M12%207a5%205%200%200%201'
            '%205%205\'/%3E%3C/svg%3E"); }')
    assert icon in CSS


def test_idle_settings_moved_from_the_settings_tab_to_the_points_tab():
    points = block('<div id="points-tab"', '<!-- Settings Tab -->')
    settings = block('<!-- Settings Tab -->', '<!-- Help Tab -->')
    for fragment in ('id="claim-channel-points"', 'id="share-watch-streaks"', 'id="idle-channels-list"',
                     'id="idle-channel-input"', 'id="idle-channel-add"', 'id="idle-channels-error"'):
        assert fragment in points, fragment
        assert fragment not in settings, fragment
    assert points.count('id="idle-channels-list"') == 1


def test_every_points_option_has_a_control_matching_the_server_keys():
    keys = set(re.findall(r"^    (\w+): \{default", POINTS_SETTINGS_JS, re.M))
    assert keys == set(PointsSettings.KEYS)
    for key in keys:
        assert f'id="points-setting-{key}"' in HTML and f'id="points-setting-{key}-error"' in HTML, key
        assert f'data-points-setting="{key}"' in HTML, key
    # bounds in the markup, the script and the server agree
    for key, (low, high) in PointsSettings.RANGES.items():
        if key == "watch_slots":
            # a select: one option per allowed count
            start = HTML.index('id="points-setting-watch_slots"')
            select = HTML[start:HTML.index('</select>', start)]
            assert re.findall(r'<option value="(\d+)"', select) == [str(n) for n in range(low, high + 1)]
        else:
            assert f'data-points-setting="{key}" min="{low}" max="{high}"' in HTML, key
        assert re.search(rf"{key}: \{{default: \d+, min: {low}, max: {high}\}}", POINTS_SETTINGS_JS), key
    for key, (low, high) in PointsSettings.NUMBERS.items():
        assert f'data-points-setting="{key}" min="{low:g}" max="{high:g}" step="0.01"' in HTML, key
        assert re.search(rf"{key}: \{{default: {default_settings[key]}, min: {low:g}, max: {high:g}, decimals: 2\}}", POINTS_SETTINGS_JS), key
    for key in keys:
        default = default_settings[key]
        assert re.search(rf"{key}: \{{default: {re.escape(json.dumps(default))}", POINTS_SETTINGS_JS.replace("'", '"')), key


def test_markup_text_is_the_english_translation_and_every_locale_has_the_keys():
    points = ENGLISH["points"]
    for key, text in re.findall(r'data-points-text="(\w+)"[^>]*>([^<]+)<', HTML):
        assert unescape(text) == points[key], key
    used = set(re.findall(r'data-points-text="(\w+)"', HTML))
    assert used <= set(points), used - set(points)
    for locale in (ROOT / "lang").glob("*.json"):
        gui = json.loads(locale.read_text(encoding="utf-8"))["gui"]
        assert gui["tabs"]["points"].strip(), locale.name
        assert set(gui["points"]) == set(points), locale.name
    # warnings the user must see before enabling the three visible or costly actions
    assert "spend your channel points and can lose them" in points["predictions_enabled_help"]
    assert "viewer list" in points["join_chat_help"]
    assert "target channel is not watched" in points["follow_raids_help"]


@needs_node
def test_state_renders_null_balance_null_gain_offline_and_escapes_names():
    run_points(r"""
state.translations.gui.points = {reasons: {watch_streak: 'Watch streak'}};
const at = '2026-11-26T10:20:30+00:00';
updatePointsState({paused: false, active: true, channels: [
    {login: 'samplechannel', name: 'SampleChannel', live: true, balance: 1234, last_gain: {points: 450, reason: 'WATCH_STREAK', at}},
    {login: 'quietone', name: 'QuietOne', live: false, balance: null, last_gain: null},
    {login: 'odd', name: '<b>x</b>', live: true, balance: 0, last_gain: {points: 5, reason: 'NEW_REASON', at: 'bad'}}]});
assert.equal(tableRows().length, 3);
const first = cells(tableRows()[0]);
assert.equal(first[0], 'SampleChannel');
assert.equal(first[1], 'Live');
assert.equal(first[2], (1234).toLocaleString());
assert.ok(first[3].startsWith('+450 Watch streak'), first[3]);
assert.ok(tableRows()[0].children[3].children[0].classes.has('points-gain-time'), 'time under the amount');
assert.deepEqual(cells(tableRows()[1]), ['QuietOne', 'Offline', '-', '-']);
assert.ok(tableRows()[1].children[1].classes.has('points-offline'));
const odd = tableRows()[2];
assert.equal(odd.children[0].textContent, '<b>x</b>');
assert.ok(odd.children.every(cell => cell.children.every(child => child.tag !== 'b')));
assert.equal(odd.children[2].textContent, '0', 'a zero balance is shown, not a dash');
assert.equal(odd.children[3].textContent, '+5 NEW_REASON', 'unknown reason shows the code; bad time is dropped');
assert.equal(document.getElementById('points-empty').hidden, true);
assert.equal(document.getElementById('points-table').hidden, false);
assert.ok(document.getElementById('points-status').textContent.startsWith('Active'));
updatePointsState({paused: true, active: true, channels: []});
assert.ok(document.getElementById('points-status').textContent.startsWith('Paused'));
assert.equal(button().textContent, 'Resume channel points');
assert.equal(button().attrs['aria-pressed'], 'true');
assert.equal(document.getElementById('points-empty').hidden, false);
assert.equal(document.getElementById('points-table').hidden, true);
assert.equal(tableRows().length, 0);
updatePointsState({paused: false, active: false, channels: []});
assert.ok(document.getElementById('points-status').textContent.startsWith('Inactive'));
// a malformed update keeps the last state
updatePointsState({paused: true, channels: 'x'});
assert.ok(document.getElementById('points-status').textContent.startsWith('Inactive'));
""")


@needs_node
def test_translation_pass_sets_text_and_rerenders_dynamic_parts():
    run_points(r"""
const label = Object.assign(new Node('h2'), {dataset: {pointsText: 'title'}}); root.appendChild(label);
Object.defineProperty(label, 'innerHTML', {set() { throw new Error('unsafe'); }});
translatePoints({title: '<b>Punten</b>', status_inactive: 'Inactief', pause: 'Pauze'});
assert.equal(label.textContent, '<b>Punten</b>');
state.translations.gui.points = {status_inactive: 'Inactief', pause: 'Pauze'};
renderPoints();
assert.equal(document.getElementById('points-status').textContent, 'Inactief');
assert.equal(button().textContent, 'Pauze');
""")


@needs_node
def test_log_snapshot_replaces_lines_and_live_lines_append_and_are_capped():
    run_points(r"""
const lines = () => log.children.map(line => line.textContent);
appendLogLine('points-log', 'old');
updatePointsState({paused: false, active: true, channels: [], log: ['<b>a</b>', 'b']}, true);
assert.deepEqual(lines(), ['<b>a</b>', 'b'], 'initial_state replaces the lines, text only');
updatePointsState({paused: false, active: true, channels: [], log: ['x']});
assert.deepEqual(lines(), ['<b>a</b>', 'b'], 'a plain points_update leaves the log alone');
appendLogLine('points-log', 'c');
assert.deepEqual(lines(), ['<b>a</b>', 'b', 'c']);
for (let i = 0; i < 1005; i++) appendLogLine('points-log', 'line ' + i);
assert.equal(log.children.length, 1000);
assert.equal(lines()[0], 'line 5');
assert.equal(lines().at(-1), 'line 1004');
""")


@needs_node
def test_socket_events_feed_the_state_the_pause_flag_and_the_log():
    run_points(r"""
const handlers = {};
const socket = {on(name, callback) { handlers[name] = callback; }};
""" + SOCKET_BLOCKS + r"""
handlers.points_update({paused: false, active: true, channels: [{login: 'samplechannel', name: 'SampleChannel',
    live: true, balance: 10, last_gain: null}]});
assert.equal(tableRows().length, 1);
handlers.points_paused_update({paused: true});
assert.equal(state.points.paused, true);
assert.equal(state.points.channels.length, 1, 'the pause event keeps the channels');
assert.equal(button().textContent, 'Resume channel points');
handlers.points_paused_update({paused: 'yes'});
assert.equal(state.points.paused, true);
handlers.points_output({message: '[2026-11-26 10:00:00] | Claimed the bonus on SampleChannel'});
assert.equal(log.children.at(-1).textContent, '[2026-11-26 10:00:00] | Claimed the bonus on SampleChannel');
""")


@needs_node
def test_pause_button_round_trip_and_failure_rollback():
    run_points(r"""
const sent = [];
let reply;
global.fetch = async (url, options) => {
    sent.push([url, JSON.parse(options.body)]);
    assert.equal(button().disabled, true, 'disabled while the request runs');
    return reply();
};
updatePointsState({paused: false, active: true, channels: []});
reply = () => ({ok: true, json: async () => ({success: true, paused: true})});
await togglePointsPause();
assert.deepEqual(sent.pop(), ['/api/points/pause', {paused: true}]);
assert.equal(state.points.paused, true);
assert.equal(button().textContent, 'Resume channel points');
assert.equal(button().disabled, false);
// resume sends the opposite and the server's answer wins
reply = () => ({ok: true, json: async () => ({success: true, paused: false})});
await togglePointsPause();
assert.deepEqual(sent.pop(), ['/api/points/pause', {paused: false}]);
assert.equal(state.points.paused, false);
assert.equal(button().textContent, 'Pause channel points');
// failures leave the confirmed state and show the error
const error = document.getElementById('points-pause-error');
state.translations.gui.points = {pause_error: '<i>Pause failed</i>'};
for (const bad of [() => ({ok: false}), () => ({ok: true, json: async () => ({success: false})}),
                   () => ({ok: true, json: async () => ({success: true})}), () => { throw new Error('network'); }]) {
    error.hidden = true;
    reply = bad;
    await togglePointsPause();
    assert.equal(state.points.paused, false, 'rolled back');
    assert.equal(button().textContent, 'Pause channel points');
    assert.equal(button().disabled, false);
    assert.equal(error.hidden, false);
    assert.equal(error.textContent, '<i>Pause failed</i>');
}
// a second click while one request runs is ignored
let finish;
reply = () => new Promise(resolve => { finish = resolve; });
const pending = togglePointsPause();
sent.length = 0;
await togglePointsPause();
assert.equal(sent.length, 0);
finish({ok: true, json: async () => ({success: true, paused: true})});
await pending;
assert.equal(state.points.paused, true);
""")


@needs_node
def test_new_controls_show_defaults_then_the_saved_values():
    run_points(r"""
renderPointsSettings();
assert.equal(control('idle_priority').value, 'list');
assert.equal(control('idle_streak_first').checked, true);
assert.equal(control('streak_recovery').checked, true);
assert.equal(control('claim_moments').checked, true);
for (const key of ['follow_raids', 'join_chat', 'predictions_enabled']) assert.equal(control(key).checked, false, key);
assert.equal(control('prediction_strategy').value, 'smart');
assert.equal(control('prediction_percent').value, '5');
assert.equal(control('prediction_max_points').value, '1000');
for (const [key, value] of [['prediction_min_balance', '0'], ['prediction_min_voters', '0'], ['prediction_min_odds', '1'],
        ['prediction_timing', 'before_close'], ['prediction_delay_seconds', '6'], ['prediction_timer_percent', '50'],
        ['prediction_fixed_option', '1']]) assert.equal(control(key).value, value, key);
renderPointsSettings({prediction_min_odds: 2.5, prediction_timing: 'percent', prediction_fixed_option: 4});
assert.equal(control('prediction_min_odds').value, '2.5');
assert.equal(control('prediction_timing').value, 'percent');
assert.equal(control('prediction_fixed_option').value, '4');
renderPointsSettings({idle_priority: 'subscribed', follow_raids: true, claim_moments: false, prediction_percent: 40,
    prediction_max_points: 'bad'});
assert.equal(control('idle_priority').value, 'subscribed');
assert.equal(control('follow_raids').checked, true);
assert.equal(control('claim_moments').checked, false);
assert.equal(control('prediction_percent').value, '40');
assert.equal(control('prediction_max_points').value, '1000', 'a wrong type falls back to the default');
""")


@needs_node
@pytest.mark.parametrize(("key", "shown", "sent"), [
    ("prediction_percent", "250", 100), ("prediction_percent", "0", 1), ("prediction_percent", "-7", 1),
    ("prediction_percent", "12.9", 12), ("prediction_max_points", "5", 10), ("prediction_max_points", "9999999", 250000),
    ("prediction_max_points", "2500", 2500), ("prediction_max_points", "1e3", 1000 + 0),
    ("prediction_min_balance", "-5", 0), ("prediction_min_balance", "99999999", 10_000_000),
    ("prediction_min_balance", "300", 300), ("prediction_min_voters", "2000000", 1_000_000),
    ("prediction_min_voters", "7.9", 7), ("prediction_delay_seconds", "-1", 0), ("prediction_delay_seconds", "4000", 3600),
    ("prediction_delay_seconds", "12", 12), ("prediction_timer_percent", "0", 1), ("prediction_timer_percent", "100", 99),
    ("prediction_timer_percent", "75", 75), ("prediction_fixed_option", "0", 1), ("prediction_fixed_option", "11", 10),
    ("prediction_fixed_option", "3", 3),
    # the odds keep two decimals, clamp to 1..100 and a value that rounds to the default sends nothing
    ("prediction_min_odds", "2.456", 2.46), ("prediction_min_odds", "2.5", 2.5), ("prediction_min_odds", "0.5", 1),
    ("prediction_min_odds", "150", 100), ("prediction_min_odds", "1.004", 1), ("prediction_min_odds", "1e1", 10),
])
def test_number_inputs_clamp_before_saving(key, shown, sent):
    expect_request = sent != default_settings[key]
    run_points(f"""
const key = {json.dumps(key)};
const calls = [];
global.fetch = async (url, options) => {{
    calls.push([url, JSON.parse(options.body)]);
    const body = JSON.parse(options.body);
    return {{ok: true, json: async () => ({{success: true, settings: body}})}};
}};
control(key).value = {json.dumps(shown)};
await savePointsSetting(key);
if ({json.dumps(expect_request)}) {{
    assert.deepEqual(calls, [['/api/settings', {{[key]: {sent}}}]]);
    assert.equal(state.settings[key], {sent});
}} else {{
    assert.equal(calls.length, 0, 'the clamped value is the saved one: nothing to send');
}}
assert.equal(control(key).value, String({sent}), 'the field shows the clamped, saved value');
assert.equal(control(key).disabled, false);
assert.equal(failure(key).hidden, true);
""")


@needs_node
def test_empty_or_text_number_is_restored_without_a_request():
    run_points(r"""
let sent = 0;
global.fetch = async () => { sent++; return {ok: true, json: async () => ({success: true, settings: {}})}; };
state.settings.prediction_percent = 30;
for (const text of ['', '   ', 'abc']) {
    control('prediction_percent').value = text;
    await savePointsSetting('prediction_percent');
    assert.equal(control('prediction_percent').value, '30');
}
assert.equal(sent, 0);
""")


@needs_node
def test_checkboxes_and_selects_save_one_key_and_roll_back_on_failure():
    run_points(r"""
const calls = [];
let reply = body => ({ok: true, json: async () => ({success: true, settings: body})});
global.fetch = async (url, options) => { const body = JSON.parse(options.body); calls.push(body); return reply(body); };
control('follow_raids').checked = true;
await savePointsSetting('follow_raids');
control('idle_streak_first').checked = false;
await savePointsSetting('idle_streak_first');
control('prediction_strategy').value = 'favourite';
await savePointsSetting('prediction_strategy');
control('idle_priority').value = 'points_high';
await savePointsSetting('idle_priority');
assert.deepEqual(calls, [{follow_raids: true}, {idle_streak_first: false}, {prediction_strategy: 'favourite'},
                         {idle_priority: 'points_high'}]);
assert.equal(state.settings.follow_raids, true);
assert.equal(state.settings.idle_streak_first, false);
// unchanged value: no request
calls.length = 0;
await savePointsSetting('follow_raids');
assert.equal(calls.length, 0);
// failures: http, application, a reply that does not echo the value, network
state.translations.gui.points = {save_error: '<b>Save failed</b>'};
for (const bad of [() => ({ok: false}), () => ({ok: true, json: async () => ({success: false})}),
                   () => ({ok: true, json: async () => ({success: true, settings: {predictions_enabled: false}})}),
                   () => { throw new Error('network'); }]) {
    failure('predictions_enabled').hidden = true;
    control('predictions_enabled').checked = true;
    reply = bad;
    await savePointsSetting('predictions_enabled');
    assert.equal(control('predictions_enabled').checked, false, 'back to the confirmed value');
    assert.equal(control('predictions_enabled').disabled, false);
    assert.equal(failure('predictions_enabled').hidden, false);
    assert.equal(failure('predictions_enabled').textContent, '<b>Save failed</b>');
    assert.notEqual(state.settings.predictions_enabled, true);
}
""")


@needs_node
def test_a_partial_settings_reply_keeps_the_points_options():
    run_javascript(["updateSettingsUI", "toggleMiningHoursRange"], POINTS_SETTINGS_JS + r"""
const assert = require('node:assert/strict');
const elements = {};
const document = {getElementById(id) { return elements[id] ??= {checked: false, hidden: true, value: '', style: {}}; },
                  body: {classList: {add() {}, remove() {}}}};
const state = {settings: {prediction_percent: 40, follow_raids: true}, translations: {gui: {settings: {}}}};
function applyInventoryViewMode() {}
function renderGamesToWatch() {}
function renderChannels() {}
function renderInventory() {}
function updateGameTagsDisplay() {}
let availableGames = new Set();
let selectedInventoryGames = [];
const seen = [];
function renderPointsSettings(settings) { seen.push(settings.prediction_percent); }
updateSettingsUI({prediction_percent: 5, follow_raids: false, dark_mode: true}, false, ['dark_mode']);
assert.equal(state.settings.prediction_percent, 40);
assert.equal(state.settings.follow_raids, true);
assert.deepEqual(seen, [40]);
updateSettingsUI({prediction_percent: 7}, false, ['prediction_percent']);
assert.equal(state.settings.prediction_percent, 7);
updateSettingsUI({prediction_percent: 9}, true);
assert.equal(state.settings.prediction_percent, 9);
""")


@needs_node
def test_watch_slots_select_saves_an_int_and_rolls_back_on_failure():
    run_points(r"""
const calls = [];
let reply = body => ({ok: true, json: async () => ({success: true, settings: body})});
global.fetch = async (url, options) => { const body = JSON.parse(options.body); calls.push(body); return reply(body); };
renderPointsSettings();
assert.equal(control('watch_slots').value, '1');
control('watch_slots').value = '2';
await savePointsSetting('watch_slots');
assert.deepEqual(calls, [{watch_slots: 2}], 'the select value is sent as a number');
assert.equal(state.settings.watch_slots, 2);
reply = () => ({ok: false});
control('watch_slots').value = '1';
await savePointsSetting('watch_slots');
assert.equal(control('watch_slots').value, '2', 'back to the confirmed value');
assert.equal(failure('watch_slots').hidden, false);
""")


@needs_node
def test_second_slot_label_marks_only_the_side_watched_row():
    run_points(r"""
updatePointsState({paused: false, active: true, channels: [
    {login: 'one', name: 'One', live: true, balance: 1, last_gain: null, side: true},
    {login: 'two', name: 'Two', live: true, balance: 2, last_gain: null, side: false},
    {login: 'three', name: 'Three', live: true, balance: 3, last_gain: null}]});
const [first, second, third] = tableRows();
const label = first.children[0].children[0];
assert.ok(label.classes.has('points-side'));
assert.equal(label.textContent, 'Watching (second slot)');
assert.equal(second.children[0].children.length, 0);
assert.equal(third.children[0].children.length, 0);
state.translations.gui.points = {side_watching: 'Second slot'};
renderPoints();
assert.equal(tableRows()[0].children[0].children[0].textContent, 'Second slot');
""")
