import json
import subprocess

import pytest

from tests.javascript_helpers import APP_JS, NODE, extract_javascript_function


@pytest.mark.skipif(NODE is None, reason="Node.js is required for frontend tests")
def test_priority_edits_preserve_games_and_save_valid_changes():
    source = extract_javascript_function(APP_JS.read_text(encoding="utf-8"), "changeGamePriority")
    script = source + """
const state = {settings: {games_to_watch: []}};
let saves = 0;
function renderGamesToWatch() {}
function renderChannels() {}
function saveSettings() { saves++; }
const cases = [
    ['C', 0], ['A', 2], ['B', -10], ['A', 999],
    ['missing', 0], ['B', NaN], ['B', Infinity], ['B', 1.5]
];
const results = cases.map(([game, rank]) => {
    const original = ['A', 'B', 'C'];
    state.settings.games_to_watch = original;
    saves = 0;
    changeGamePriority(game, rank);
    return {games: state.settings.games_to_watch, original, saves};
});
process.stdout.write(JSON.stringify(results));
"""
    result = subprocess.run([NODE, "-"], input=script, text=True, capture_output=True, check=True)
    cases = json.loads(result.stdout)
    assert [case["games"] for case in cases] == [
        ["C", "A", "B"], ["B", "C", "A"], ["B", "A", "C"], ["B", "C", "A"],
        *([["A", "B", "C"]] * 4),
    ]
    assert [case["saves"] for case in cases] == [1, 1, 1, 1, 0, 0, 0, 0]
    assert all(case["original"] == ["A", "B", "C"] for case in cases)


@pytest.mark.skipif(NODE is None, reason="Node.js is required for frontend tests")
def test_priority_input_validation_and_translated_accessible_labels():
    source = extract_javascript_function(APP_JS.read_text(encoding="utf-8"), "renderSelectedGames")
    script = source + """
class Element {
    constructor(tag, attrs = {}) { this.tag = tag; this.attrs = attrs; this.children = []; this.dataset = {}; this.events = {}; }
    replaceChildren(...children) { this.children = children; }
    appendChild(child) { this.children.push(child); }
    addEventListener(name, handler) { this.events[name] = handler; }
    querySelector(selector) { return this.children.find(el => el.attrs.class === selector.slice(1)); }
}
const container = new Element('div');
const document = {getElementById: () => container, createElement: tag => new Element(tag)};
function makeElement(tag, attrs, text) { return new Element(tag, attrs); }
const state = {translations: {gui: {settings: {game_priority: '优先级：{game}', remove_game: '移除 {game}'}}}};
function handleDragStart() {}
function handleDragOver() {}
function handleDrop() {}
function handleDragEnd() {}
function removeGameFromWatch() {}
const changes = [];
function changeGamePriority(...args) { changes.push(args); }
renderSelectedGames(['Rust']);
const row = container.children[0];
const input = row.children[1];
const values = ['','1.5','oops','2'];
const resetValues = values.map(value => {
    const target = {value};
    input.events.change({target});
    return target.value;
});
process.stdout.write(JSON.stringify({changes, resetValues, input: input.attrs, button: row.children[3].attrs}));
"""
    result = subprocess.run([NODE, "-"], input=script, text=True, capture_output=True, check=True)
    data = json.loads(result.stdout)
    assert data["changes"] == [["Rust", 1]]
    assert data["resetValues"] == ["1", "1", "1", "2"]
    assert data["input"]["aria-label"] == "优先级：Rust"
    assert data["button"]["aria-label"] == data["button"]["title"] == "移除 Rust"
