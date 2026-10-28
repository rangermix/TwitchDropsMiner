"""Exercise priority-only settings and fallback channel rendering without Twitch."""

import json
from pathlib import Path

import pytest

from tests.javascript_helpers import NODE
from tests.test_telegram_frontend import run_javascript


ROOT = Path(__file__).resolve().parents[1]
pytestmark = pytest.mark.skipif(NODE is None, reason="Node.js is required for frontend tests")


DOM = r"""
const assert = require('node:assert/strict');
class Element {
    constructor(tag) {
        this.tag = tag; this.children = []; this.attrs = {}; this.style = {};
        this.events = {}; this._text = '';
        this.classList = {add() {}};
    }
    set textContent(value) { this.children = []; this._text = String(value); }
    get textContent() { return this._text + this.children.map(child => child.textContent).join(''); }
    set innerHTML(value) { assert.equal(value, '', 'translated HTML must never be injected'); this.replaceChildren(); }
    replaceChildren(...children) { this._text = ''; this.children = children; }
    appendChild(child) { this.children.push(child); return child; }
    setAttribute(name, value) { this.attrs[name] = value; }
    addEventListener(name, handler) { this.events[name] = handler; }
    querySelector(selector) { return selector === 'input' ? this.children.find(child => child.tag === 'input') : null; }
}
"""


def test_strict_default_and_fallback_rendering_preserve_watched_channel_and_saved_games():
    run_javascript(
        ["makeElement", "channelMatchesGameFilter", "renderChannels"],
        DOM + r"""
const container = new Element('div');
const document = {
    getElementById: id => id === 'channels-list' ? container : null,
    createElement: tag => new Element(tag),
    createTextNode: text => ({textContent: String(text)}),
};
function updateNowWatching() {}
function selectChannel() {}
const games = Object.freeze(['rust']);
const state = {
    settings: {games_to_watch: games}, translations: {},
    channels: {
        selected: {id: 'selected', name: 'Selected', game: 'Rust', online: true, viewers: 10},
        watched: {id: 'watched', name: 'Watching', game: 'Another game', watching: true, online: true},
        other: {id: 'other', name: 'Fallback', game: 'Another game', online: true, viewers: 100},
        unknown: {id: 'unknown', name: 'No category', game: null, online: true},
    },
};
function names() { return container.children.map(row => row.children[1].children[0].textContent); }
for (const value of [undefined, true, null, 0]) {
    state.settings.priority_list_only = value;
    renderChannels();
    assert.deepEqual(names(), ['Watching', 'Selected'], 'strict mode must preserve case-insensitive matches and the watched channel');
    assert.equal(state.settings.games_to_watch, games);
}
state.settings.priority_list_only = false;
renderChannels();
assert.deepEqual(names(), ['Watching', 'Fallback', 'Selected', 'No category']);
assert.equal(state.settings.games_to_watch, games, 'fallback must not extend the saved list');
state.settings.priority_list_only = true;
renderChannels();
assert.deepEqual(names(), ['Watching', 'Selected']);
state.settings.games_to_watch = [];
renderChannels();
assert.equal(container.children.length, 4, 'an empty selected list keeps the existing all-games behavior');
""",
    )


def test_settings_restore_strict_default_and_both_explicit_modes_without_changing_games():
    run_javascript(
        ["updateSettingsUI", "toggleMiningHoursRange"],
        r"""
const assert = require('node:assert/strict');
const elements = {};
const document = {
    getElementById(id) { return elements[id] ??= {checked: false, value: '', style: {}}; },
    body: {classList: {add() {}, remove() {}}},
};
const state = {settings: {}};
let availableGames = new Set(), selectedInventoryGames = [];
function applyInventoryViewMode() {}
function renderGamesToWatch() {}
function renderChannels() {}
function renderInventory() {}
function updateGameTagsDisplay() {}
const games = Object.freeze(['Rust', 'Game B']);
for (const [settings, expected] of [
    [{games_to_watch: games}, true],
    [{games_to_watch: games, priority_list_only: false}, false],
    [{games_to_watch: games, priority_list_only: true}, true],
]) {
    updateSettingsUI(settings);
    assert.equal(document.getElementById('priority-list-only').checked, expected);
    assert.equal(state.settings.games_to_watch, games);
}
""",
    )


PRIORITY_SETUP = r"""
const assert = require('node:assert/strict');
const games = Object.freeze(['Game B', 'Rust']);
const elements = {};
const document = {
    getElementById(id) { return elements[id] ??= {checked: false, value: '1', style: {}, disabled: false}; },
    body: {classList: {add() {}, remove() {}}},
};
const state = {
    settings: {games_to_watch: games},
    translations: {gui: {settings: {priority_list_only_save_error: '<img> Could not save priority'}}},
};
const input = document.getElementById('priority-list-only');
let availableGames = new Set(), selectedInventoryGames = [];
let renderedModes = [], toasts = [];
function applyInventoryViewMode() {}
function renderGamesToWatch() {}
function renderChannels() { renderedModes.push(state.settings.priority_list_only !== false); }
function renderInventory() {}
function updateGameTagsDisplay() {}
function getInventoryFilters() { return {}; }
function parseDropNameBlacklist() { return []; }
function showToast(...args) { toasts.push(args); }
"""


@pytest.mark.parametrize("strict", [False, True])
def test_priority_save_confirms_explicit_mode_once_and_preserves_selected_order(strict):
    run_javascript(
        ["onPriorityModeChange", "updateSettingsUI", "toggleMiningHoursRange"],
        PRIORITY_SETUP + "\nconst strict = " + json.dumps(strict) + ";\n" + r"""
(async () => {
    state.settings.priority_list_only = !strict;
    input.checked = strict;
    let calls = 0, complete;
    global.fetch = async (url, options) => {
        calls++;
        assert.equal(url, '/api/settings');
        assert.equal(options.method, 'POST');
        assert.deepEqual(JSON.parse(options.body), {priority_list_only: strict});
        return new Promise(resolve => { complete = resolve; });
    };
    const pending = onPriorityModeChange();
    assert.equal(input.disabled, true);
    assert.equal(state.settings.priority_list_only, !strict, 'pending save must not change confirmed mode');
    assert.deepEqual(renderedModes, [], 'pending save must not apply the requested channel filter');
    await onPriorityModeChange();
    assert.equal(calls, 1, 'duplicate saves must be blocked while persistence is pending');
    complete({ok: true, json: async () => ({success: true, settings: {...state.settings, priority_list_only: strict}})});
    await pending;
    assert.equal(calls, 1);
    assert.equal(state.settings.priority_list_only, strict);
    assert.equal(input.checked, strict);
    assert.equal(input.disabled, false);
    assert.deepEqual(renderedModes, [strict]);
    assert.deepEqual(toasts, []);
    assert.deepEqual(state.settings.games_to_watch, ['Game B', 'Rust']);
    assert.equal(state.settings.games_to_watch, games);
})().catch(error => { console.error(error); process.exitCode = 1; });
""",
    )


@pytest.mark.parametrize("saved", [False, True, None])
@pytest.mark.parametrize("failure", ["http", "application", "network", "mismatch", "invalid_json"])
def test_failed_priority_save_restores_confirmed_mode_and_shows_translated_error(saved, failure):
    run_javascript(
        ["onPriorityModeChange"],
        PRIORITY_SETUP + "\nconst saved = " + json.dumps(saved) + ";\n"
        + "const failure = " + json.dumps(failure) + ";\n" + r"""
(async () => {
    if (saved !== null) state.settings.priority_list_only = saved;
    const confirmed = saved !== false;
    input.checked = !confirmed;
    let calls = 0;
    global.fetch = async (url, options) => {
        calls++;
        assert.equal(input.disabled, true);
        assert.equal(state.settings.priority_list_only !== false, confirmed);
        assert.deepEqual(JSON.parse(options.body), {priority_list_only: !confirmed});
        if (failure === 'network') throw new Error('offline');
        return {ok: failure !== 'http', json: async () => {
            if (failure === 'invalid_json') throw new Error('invalid response');
            return {success: failure !== 'application', settings: {
                ...state.settings,
                priority_list_only: failure === 'mismatch' ? confirmed : !confirmed,
            }};
        }};
    };
    await onPriorityModeChange();
    assert.equal(calls, 1);
    assert.equal(input.checked, confirmed);
    assert.equal(input.disabled, false);
    assert.equal(state.settings.priority_list_only !== false, confirmed);
    assert.deepEqual(renderedModes, [confirmed]);
    assert.deepEqual(toasts, [['<img> Could not save priority', 'error']]);
    assert.equal(state.settings.games_to_watch, games);
    assert.deepEqual(games, ['Game B', 'Rust']);
})().catch(error => { console.error(error); process.exitCode = 1; });
""",
    )


def test_unrelated_autosave_cannot_revert_priority_mode_save_in_flight():
    run_javascript(
        ["saveSettings", "onPriorityModeChange", "updateSettingsUI", "toggleMiningHoursRange"],
        PRIORITY_SETUP + r"""
(async () => {
    state.settings.priority_list_only = true;
    let completeAutosave, serverMode = true;
    global.fetch = async (url, options) => {
        const update = JSON.parse(options.body);
        if (!Object.hasOwn(update, 'priority_list_only')) {
            assert.deepEqual(update.games_to_watch, ['Game B', 'Rust']);
            return new Promise(resolve => { completeAutosave = () => resolve({ok: true,
                json: async () => ({success: true, settings: {games_to_watch: [...games]}})}); });
        }
        assert.deepEqual(update, {priority_list_only: false});
        serverMode = update.priority_list_only;
        return {ok: true, json: async () => ({success: true, settings: {
            games_to_watch: games, priority_list_only: serverMode,
        }})};
    };
    const autosave = saveSettings();
    input.checked = false;
    await onPriorityModeChange();
    completeAutosave();
    await autosave;
    assert.equal(serverMode, false);
    assert.equal(state.settings.priority_list_only, false);
    assert.equal(input.checked, false);
    assert.equal(state.settings.games_to_watch, games);
})().catch(error => { console.error(error); process.exitCode = 1; });
""",
    )


def test_priority_label_translates_safely_in_every_locale_and_preserves_checkbox():
    translations = [
        json.loads(path.read_text(encoding="utf-8"))
        for path in sorted((ROOT / "lang").glob("*.json"))
    ]
    run_javascript(
        ["applyTranslations"],
        DOM + "\nconst translations = " + json.dumps(translations) + ";\n" + r"""
const checkbox = new Element('input');
checkbox.checked = true;
checkbox.addEventListener('change', () => {});
const listener = checkbox.events.change;
const label = new Element('label');
label.appendChild(checkbox);
const settingsTab = {
    querySelector: selector => selector === 'label:has(#priority-list-only)' ? label : null,
};
const document = {
    getElementById: id => id === 'settings-tab' ? settingsTab : null,
    querySelector: () => null,
    createTextNode: text => ({textContent: String(text)}),
};
const state = {};
function translateHistory() {}
function renderGamesToWatch() {}
for (const translation of [...translations, {gui: {settings: {priority_list_only: '<img src=x onerror=alert(1)>'}}}]) {
    const text = translation.gui.settings.priority_list_only;
    assert.equal(typeof text, 'string', translation.language_name);
    applyTranslations(translation);
    assert.equal(label.textContent, ' ' + text);
    assert.equal(label.children.length, 2, 'language switching must not duplicate the label');
    assert.equal(label.children[0], checkbox);
    assert.equal(checkbox.checked, true);
    assert.equal(checkbox.events.change, listener);
}
""",
    )


def test_priority_checkbox_defaults_checked_before_settings_load():
    html = (ROOT / "web" / "index.html").read_text(encoding="utf-8")
    assert 'type="checkbox" id="priority-list-only" checked' in html


def test_late_partial_save_reply_preserves_newer_watch_list():
    run_javascript(
        ['onPriorityModeChange', 'saveSettings', 'updateSettingsUI', 'toggleMiningHoursRange'],
        PRIORITY_SETUP + r"""
(async()=>{
 updateSettingsUI({games_to_watch:['A','B'],priority_list_only:true},true);
 let finish;let saved;
 global.fetch=async(url,options)=>{
  const body=JSON.parse(options.body);
  if(Object.hasOwn(body,'priority_list_only'))return new Promise(resolve=>{finish=resolve;});
  saved=body.games_to_watch;
  return {ok:true,json:async()=>({success:true,settings:{games_to_watch:saved}})};
 };
 input.checked=false;const pending=onPriorityModeChange();
 // A confirmed Inventory write/socket update arrives before the old partial response.
 updateSettingsUI({games_to_watch:['A','B','C'],priority_list_only:true},true);
 finish({ok:true,json:async()=>({success:true,settings:{games_to_watch:['A','B'],priority_list_only:false}})});
 await pending;assert.deepEqual(state.settings.games_to_watch,['A','B','C']);
 await saveSettings();assert.deepEqual(saved,['A','B','C']);
})().catch(error=>{console.error(error);process.exitCode=1;});
""",
    )
