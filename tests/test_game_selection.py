import json
import subprocess

import pytest

from tests.javascript_helpers import APP_JS, NODE, extract_javascript_function


def run_javascript(names, body):
    source = APP_JS.read_text(encoding="utf-8")
    functions = "\n".join(extract_javascript_function(source, name) for name in names)
    result = subprocess.run(
        [NODE, "-"], input=functions + body, text=True, capture_output=True, check=True
    )
    return json.loads(result.stdout)


pytestmark = pytest.mark.skipif(NODE is None, reason="Node.js is required for frontend tests")


def test_select_all_preserves_order_manual_games_and_case_insensitive_uniqueness():
    result = run_javascript(["selectAllGames"], """
const state = {settings: {games_to_watch: ['Rust', 'Manual']}};
const availableGames = new Set(['rust', 'Dota 2', 'Valorant', 'dota 2']);
let saves = 0;
function renderGamesToWatch() {}
function renderChannels() {}
function saveSettings() { saves++; }
selectAllGames();
process.stdout.write(JSON.stringify({games: state.settings.games_to_watch, saves}));
""")
    assert result == {"games": ["Rust", "Manual", "Dota 2", "Valorant"], "saves": 1}


def test_search_resolves_matches_and_confirmation_uses_current_settings():
    result = run_javascript(["addGameFromSearch", "deselectAllGames"], """
const state = {settings: {games_to_watch: []}, translations: {}};
const availableGames = new Set(['Rust', 'Dota 2', 'Dota Underlords']);
const input = {value: ''};
const document = {getElementById: () => input};
let saves = 0;
const warnings = [];
let confirm = null;
function renderGamesToWatch() {}
function renderChannels() {}
function saveSettings() { saves++; }
function showToast(message) { warnings.push(message); }
function showConfirmModal(message, callback) { confirm = callback; }
input.value = ' rUsT ';
addGameFromSearch();
input.value = 'under';
addGameFromSearch();
input.value = 'dota';
addGameFromSearch();
const resolved = [...state.settings.games_to_watch];
input.value = 'Manual';
addGameFromSearch();
const beforeConfirm = [...state.settings.games_to_watch];
// A websocket/settings update arrives while the dialog is open.
state.settings.games_to_watch = ['New priority', 'Rust'];
confirm();
const afterConfirm = [...state.settings.games_to_watch];
input.value = 'MANUAL';
addGameFromSearch();
const duplicateSaves = saves;
deselectAllGames();
const beforeClear = [...state.settings.games_to_watch];
confirm();
process.stdout.write(JSON.stringify({resolved, warnings: warnings.length, beforeConfirm,
 afterConfirm, duplicateSaves, beforeClear, cleared: state.settings.games_to_watch, saves}));
""")
    assert result["resolved"] == ["Rust", "Dota Underlords"]
    assert result["warnings"] == 1
    assert result["beforeConfirm"] == result["resolved"]
    assert result["afterConfirm"] == ["New priority", "Rust", "Manual"]
    assert result["beforeClear"] == result["afterConfirm"]
    assert result["duplicateSaves"] == 3
    assert result["cleared"] == []
    assert result["saves"] == 4


def test_confirmation_focus_escape_and_single_submission():
    result = run_javascript(["showConfirmModal"], """
class Element {
    constructor(tag) { this.tag = tag; this.children = []; this.attrs = {}; this.style = {}; this.events = {}; this.isConnected = true; }
    appendChild(child) { this.children.push(child); }
    setAttribute(key, value) { this.attrs[key] = value; }
    addEventListener(key, callback) { this.events[key] = callback; }
    focus() { document.activeElement = this; }
    remove() { this.isConnected = false; document.body.children = document.body.children.filter(el => el !== this); }
}
const opener = new Element('button');
const document = {activeElement: opener, body: new Element('body'),
 createElement: tag => new Element(tag), querySelector: () => document.body.children[0]};
const state = {translations: {gui: {settings: {cancel_btn: '取消', confirm_btn: '确认'}}}};
function requestAnimationFrame(callback) { callback(); }
let confirmed = 0;
showConfirmModal('<img src=x>', () => confirmed++);
const overlay = document.body.children[0];
const modal = overlay.children[0];
const [cancel, accept] = modal.children[1].children;
const focusedCancel = document.activeElement === cancel;
showConfirmModal('another', () => confirmed++);
const count = document.body.children.length;
const key = key => overlay.events.keydown({key, preventDefault() {}});
key('Tab');
const focusedConfirm = document.activeElement === accept;
key('Escape');
const restored = document.activeElement === opener;
accept.onclick(); // A late/double click must not invoke the callback after cancellation.
showConfirmModal('second', () => confirmed++);
const nextAccept = document.body.children[0].children[0].children[1].children[1];
nextAccept.onclick();
nextAccept.onclick();
process.stdout.write(JSON.stringify({focusedCancel, focusedConfirm, restored, count, confirmed,
 role: modal.attrs.role, modal: modal.attrs['aria-modal'], text: modal.children[0].textContent,
 labels: [cancel.textContent, accept.textContent], remaining: document.body.children.length}));
""")
    assert result == {
        "focusedCancel": True, "focusedConfirm": True, "restored": True,
        "count": 1, "confirmed": 1, "role": "dialog", "modal": "true",
        "text": "<img src=x>", "labels": ["取消", "确认"], "remaining": 0,
    }
