"""Exercise pause state, request races and focus without a backend."""

import json
import subprocess
from html.parser import HTMLParser

import pytest

from tests.javascript_helpers import APP_JS, NODE, extract_javascript_function


pytestmark = pytest.mark.skipif(NODE is None, reason="Node.js is required for frontend tests")

HARNESS = r"""
const assert = require('node:assert/strict');
const handlers = {};
const socket = {on(name, callback) { handlers[name] = callback; }};
const listeners = new Map();
const body = {id: 'body'};
function emit(type, event) {
    for (const callback of [...(listeners.get(type) || [])]) callback(event);
}
class Control {
    constructor(id) { this.id = id; this.attrs = {}; this.classes = new Set(); this._disabled = false; }
    setAttribute(k, v) { this.attrs[k] = v; }
    removeAttribute(k) { delete this.attrs[k]; }
    get disabled() { return this._disabled; }
    set disabled(value) {
        this._disabled = value;
        if (value && document.activeElement === this) document.activeElement = body;
    }
    focus() {
        if (!this.disabled) { document.activeElement = this; emit('focusin', {target: this}); }
    }
}
const button = new Control('pause-btn');
button.classList = {toggle(c, on) { on ? button.classes.add(c) : button.classes.delete(c); }};
const firstItem = new Control('first-item');
const items = [firstItem, new Control('second-item'), new Control('third-item'), new Control('last-item')];
const other = new Control('other-input');
const menu = {
    _hidden: true, contains: item => items.includes(item),
    querySelector: () => firstItem,
    querySelectorAll: () => items,
    get hidden() { return this._hidden; },
    set hidden(value) {
        this._hidden = value;
        if (value && this.contains(document.activeElement)) document.activeElement = body;
    }
};
const document = {
    body, activeElement: body,
    getElementById: id => id === 'pause-menu' ? menu : button,
    addEventListener(type, callback) {
        if (!listeners.has(type)) listeners.set(type, new Set());
        listeners.get(type).add(callback);
    },
    removeEventListener(type, callback) { listeners.get(type)?.delete(callback); }
};
const state = {paused: false, translations: {gui: {status: {
    pause: 'Pausa', resume: 'Riprendi', pause_error: 'Translated pause failure'
}}}};
const lines = [], toasts = [], bodies = [];
function addConsoleLine(line) { lines.push(line); }
function showToast(message, type) { toasts.push({message, type}); }
function clearDropProgress() {}
let fetchResponse = body => ({ok: true, json: async () => ({success: true, paused: body.paused, resume_at: null})});
async function fetch(url, options) {
    assert.equal(url, '/api/pause');
    assert.ok(button.disabled);
    const value = JSON.parse(options.body); bodies.push(value);
    return await fetchResponse(value);
}
const listenerCount = () => [...listeners.values()].reduce((sum, group) => sum + group.size, 0);
"""


def run_pause(body: str) -> None:
    source = APP_JS.read_text(encoding="utf-8")
    functions = "\n".join([
        extract_javascript_function(source, "isPauseRevision") if "function isPauseRevision(" in source else "",
        extract_javascript_function(source, "isPauseInstance") if "function isPauseInstance(" in source else "",
        extract_javascript_function(source, "setPauseMenuOpen"),
        extract_javascript_function(source, "updatePauseUI"),
        extract_javascript_function(source, "onPauseButton"),
        extract_javascript_function(source, "onPauseKeydown") if "function onPauseKeydown(" in source else "",
        "async " + extract_javascript_function(source, "requestPause"),
    ])
    events = []
    for name in ("initial_state", "paused_update"):
        start = source.index(f"socket.on('{name}'")
        events.append(source[start:source.index("\n});", start) + 4])
    result = subprocess.run(
        [NODE, "-"], input=functions + HARNESS + "\n".join(events) + "\n(async()=>{\n" + body
        + "\n})().catch(error=>{console.error(error);process.exitCode=1});",
        text=True, capture_output=True,
    )
    assert result.returncode == 0, result.stderr


def test_pause_menu_picks_a_duration_and_resume_skips_the_menu():
    run_pause(r"""
updatePauseUI(false);
assert.equal(button.textContent, 'Pausa ▾');
assert.equal(button.attrs['aria-haspopup'], 'menu');
button.focus(); onPauseButton();
assert.equal(menu.hidden, false);
assert.equal(button.attrs['aria-expanded'], 'true');
assert.equal(document.activeElement, firstItem);
assert.equal(bodies.length, 0);
onPauseButton(); assert.equal(menu.hidden, true);
onPauseButton();
const pending = requestPause(true, '1h');
assert.equal(menu.hidden, true);
await pending;
assert.equal(document.activeElement, button);
assert.deepEqual(bodies.at(-1), {paused: true, duration: '1h'});
assert.equal(state.paused, true);
assert.equal(button.textContent, 'Riprendi');
assert.ok(button.classes.has('paused'));
for (const name of ['aria-haspopup', 'aria-controls', 'aria-expanded']) assert.equal(button.attrs[name], undefined);
onPauseButton(); await new Promise(resolve => setImmediate(resolve));
assert.deepEqual(bodies.at(-1), {paused: false, duration: null});
assert.equal(state.paused, false);
assert.equal(listenerCount(), 0);
""")


def test_pause_menu_keyboard_navigation_and_dismissal():
    run_pause(r"""
function key(value, target = document.activeElement) {
    const event = {key: value, target, prevented: false, preventDefault() {this.prevented = true;}};
    onPauseKeydown(event);
    return event;
}
updatePauseUI(false); button.focus();
assert.equal(key('ArrowDown').prevented, true);
assert.equal(menu.hidden, false); assert.equal(document.activeElement, firstItem);
key('ArrowUp'); assert.equal(document.activeElement, items.at(-1));
key('ArrowDown'); assert.equal(document.activeElement, firstItem);
key('End'); assert.equal(document.activeElement, items.at(-1));
key('Home'); assert.equal(document.activeElement, firstItem);
key('ArrowDown'); assert.equal(document.activeElement, items[1]);
assert.equal(key('Escape').prevented, true);
assert.equal(menu.hidden, true); assert.equal(document.activeElement, button);
key('ArrowUp'); assert.equal(document.activeElement, items.at(-1));
assert.equal(key('Tab').prevented, false);
assert.equal(menu.hidden, true); assert.equal(document.activeElement, button);
updatePauseUI(true); button.focus();
assert.equal(key('ArrowDown').prevented, false);
assert.equal(menu.hidden, true); assert.equal(bodies.length, 0);
button.disabled = true; state.paused = false;
assert.equal(key('ArrowDown', button).prevented, false);
assert.equal(menu.hidden, true);
""")


def test_pause_menu_key_handler_is_wired_to_button_and_menu():
    source = APP_JS.read_text(encoding="utf-8")
    assert "document.getElementById('pause-btn').addEventListener('keydown', onPauseKeydown)" in source
    assert "document.getElementById('pause-menu').addEventListener('keydown', onPauseKeydown)" in source


def test_pause_menu_is_named_by_its_translated_button_and_items_use_arrow_focus():
    class Controls(HTMLParser):
        def __init__(self):
            super().__init__()
            self.by_id = {}
            self.items = []

        def handle_starttag(self, tag, attrs):
            attributes = dict(attrs)
            if attributes.get("id") in {"pause-btn", "pause-menu"}:
                self.by_id[attributes["id"]] = attributes
            if attributes.get("data-pause-duration") is not None:
                self.items.append(attributes)

    controls = Controls()
    controls.feed((APP_JS.parents[1] / "index.html").read_text(encoding="utf-8"))
    menu = controls.by_id["pause-menu"]
    assert menu["role"] == "menu"
    assert menu["aria-labelledby"] == controls.by_id["pause-btn"]["id"]
    assert len(controls.items) == 4
    assert all(item["role"] == "menuitem" and item["tabindex"] == "-1" for item in controls.items)


@pytest.mark.parametrize("latest", [False, True])
@pytest.mark.parametrize("fail", [False, True])
def test_late_pause_reply_never_overwrites_newer_broadcast(latest, fail):
    run_pause(f"const latest={json.dumps(latest)}, fail={json.dumps(fail)};" + r"""
updatePauseUI(latest); button.focus();
let release; fetchResponse = () => new Promise(resolve => { release = resolve; });
const pending = requestPause(!latest, '1h');
handlers.paused_update({paused: latest, resume_at: null});
release({ok: !fail, json: async () => ({success: true, paused: !latest})});
await pending;
assert.equal(state.paused, latest);
assert.equal(button.textContent, latest ? 'Riprendi' : 'Pausa ▾');
assert.equal(button.disabled, false);
assert.equal(listenerCount(), 0);
""")


@pytest.mark.parametrize("fail", [False, True])
@pytest.mark.parametrize("move", ["stay", "focus", "tab", "pointer", "external-origin"])
def test_pause_completion_keeps_intentional_focus_and_cleans_listeners(move, fail):
    run_pause(f"const move={json.dumps(move)}, fail={json.dumps(fail)};" + r"""
updatePauseUI(false);
if (move === 'external-origin') other.focus();
else { button.focus(); onPauseButton(); }
let release; fetchResponse = () => new Promise(resolve => { release = resolve; });
const pending = requestPause(true, '10m');
if (move === 'tab') emit('keydown', {key: 'Tab'});
if (move === 'pointer') emit('pointerdown', {target: other});
if (['focus', 'tab', 'pointer'].includes(move)) other.focus();
release({ok: !fail, json: async () => ({success: true, paused: true})});
await pending;
assert.equal(document.activeElement, move === 'stay' ? button : other);
assert.equal(button.disabled, false);
assert.equal(listenerCount(), 0);
if (fail) {
    assert.deepEqual(lines, ['Translated pause failure']);
    assert.deepEqual(toasts, [{message: 'Translated pause failure', type: 'error'}]);
}
""")


@pytest.mark.parametrize("payload", [None, {}, {"success": False, "paused": True},
                                    {"success": True, "paused": "false"}, {"paused": True}] + [
                                        {"success": True, "paused": True, "revision": value}
                                        for value in (-1, True, "1", 1.5, None, 2 ** 53)
                                    ])
def test_pause_rejects_application_and_malformed_responses(payload):
    run_pause("const payload=" + json.dumps(payload) + ";" + r"""
updatePauseUI(false); button.focus();
fetchResponse = () => ({ok: true, json: async () => payload});
await requestPause(true);
assert.equal(state.paused, false);
assert.deepEqual(lines, ['Translated pause failure']);
assert.deepEqual(toasts, [{message: 'Translated pause failure', type: 'error'}]);
assert.equal(button.disabled, false); assert.equal(listenerCount(), 0);
""")


@pytest.mark.parametrize("failure", ["network", "json", "http"])
def test_pause_failure_never_exposes_exception_or_response_text(failure):
    run_pause("const failure=" + json.dumps(failure) + ";" + r"""
updatePauseUI(false); button.focus();
fetchResponse = () => {
    if (failure === 'network') throw new Error('private-host-and-token');
    return {ok: failure !== 'http', status: 500, json: async () => {throw new Error('private-response');}};
};
await requestPause(true);
assert.equal(state.paused, false);
assert.deepEqual(lines, ['Translated pause failure']);
assert.deepEqual(toasts, [{message: 'Translated pause failure', type: 'error'}]);
assert.equal(listenerCount(), 0);
""")


def test_pause_does_not_submit_a_second_request_while_pending():
    run_pause(r"""
updatePauseUI(false); button.focus();
let release; fetchResponse = () => new Promise(resolve => { release = resolve; });
const pending = requestPause(true, '10m');
const duplicate = requestPause(true, '1h');
assert.equal(bodies.length, 1);
release({ok: true, json: async () => ({success: true, paused: true})});
await Promise.all([pending, duplicate]);
assert.equal(button.disabled, false); assert.equal(listenerCount(), 0);
""")


def test_pause_broadcast_during_json_read_wins_over_delayed_http_body():
    run_pause(r"""
updatePauseUI(false); button.focus();
let release, parsing;
const parsingStarted = new Promise(resolve => { parsing = resolve; });
fetchResponse = () => ({ok: true, json: () => { parsing(); return new Promise(resolve => { release = resolve; }); }});
const pending = requestPause(true, '10m');
await parsingStarted;
handlers.paused_update({paused: false, resume_at: null});
release({success: true, paused: true});
await pending;
assert.equal(state.paused, false);
assert.equal(button.textContent, 'Pausa ▾');
assert.equal(listenerCount(), 0);
""")


@pytest.mark.parametrize("first_broadcast", [False, True])
def test_newer_versioned_http_reply_wins_over_an_older_broadcast(first_broadcast):
    run_pause("const firstBroadcast=" + json.dumps(first_broadcast) + ";" + r"""
if (!firstBroadcast) handlers.initial_state({pause: {paused: false, revision: 2}});
button.focus();
let release; fetchResponse = () => new Promise(resolve => { release = resolve; });
const pending = requestPause(true, '10m');
handlers.paused_update({paused: false, revision: firstBroadcast ? 0 : 1});
release({ok: true, json: async () => ({success: true, paused: true, revision: 3})});
await pending;
assert.equal(state.paused, true);
assert.equal(button.textContent, 'Riprendi');
assert.equal(listenerCount(), 0);
""")


def test_an_older_socket_packet_cannot_undo_a_newer_http_reply():
    run_pause(r"""
handlers.initial_state({pause: {paused: false, revision: 0}}); button.focus();
fetchResponse = () => ({ok: true, json: async () => ({success: true, paused: true, revision: 2})});
await requestPause(true, '10m');
handlers.paused_update({paused: false, revision: 1});
assert.equal(state.paused, true);
assert.equal(button.textContent, 'Riprendi');
""")


def test_same_server_initial_snapshot_cannot_undo_a_newer_http_reply():
    run_pause(r"""
const instance = 'a'.repeat(32);
handlers.initial_state({pause: {paused: false, revision: 0, instance_id: instance}}); button.focus();
fetchResponse = () => ({ok: true, json: async () => ({success: true, paused: true, revision: 2, instance_id: instance})});
await requestPause(true, '10m');
handlers.initial_state({pause: {paused: false, revision: 1, instance_id: instance}});
assert.equal(state.paused, true);
assert.equal(button.textContent, 'Riprendi');
handlers.initial_state({pause: {paused: false, revision: 0, instance_id: 'b'.repeat(32)}});
assert.equal(state.paused, false);
assert.equal(button.textContent, 'Pausa ▾');
""")


@pytest.mark.parametrize("initial_first", [False, True])
def test_same_server_initial_snapshot_does_not_invalidate_pending_newer_reply(initial_first):
    run_pause("const initialFirst=" + json.dumps(initial_first) + ";" + r"""
const instance = 'a'.repeat(32);
if (initialFirst) handlers.initial_state({pause: {paused: false, revision: 0, instance_id: instance}});
button.focus();
let release; fetchResponse = () => new Promise(resolve => { release = resolve; });
const pending = requestPause(true, '10m');
handlers.initial_state({pause: {paused: false, revision: 1, instance_id: instance}});
release({ok: true, json: async () => ({success: true, paused: true, revision: 2, instance_id: instance})});
await pending;
assert.equal(state.paused, true);
assert.equal(button.textContent, 'Riprendi');
""")


@pytest.mark.parametrize("fail", [False, True])
def test_new_server_initial_snapshot_invalidates_old_instance_request(fail):
    run_pause("const fail=" + json.dumps(fail) + ";" + r"""
const instanceA = 'a'.repeat(32), instanceB = 'b'.repeat(32);
handlers.initial_state({pause: {paused: false, revision: 9, instance_id: instanceA}}); button.focus();
let release; fetchResponse = () => new Promise(resolve => { release = resolve; });
const pending = requestPause(true, '10m');
handlers.initial_state({pause: {paused: false, revision: 0, instance_id: instanceB}});
release({ok: !fail, json: async () => ({success: true, paused: true, revision: 10, instance_id: instanceA})});
await pending;
assert.equal(state.paused, false);
assert.equal(button.textContent, 'Pausa ▾');
assert.deepEqual(lines, []); assert.deepEqual(toasts, []);
handlers.paused_update({paused: true, revision: 1, instance_id: instanceB});
assert.equal(state.paused, true);
handlers.paused_update({paused: false, revision: 11, instance_id: instanceA});
assert.equal(state.paused, true);
""")


@pytest.mark.parametrize("instance", [None, "", 42, "secret-hostname", "a" * 33])
def test_malformed_instance_id_cannot_override_confirmed_pause(instance):
    run_pause("const invalid=" + json.dumps(instance) + ";" + r"""
const valid = 'a'.repeat(32);
handlers.initial_state({pause: {paused: true, revision: 1, instance_id: valid}});
handlers.initial_state({pause: {paused: false, revision: 2, instance_id: invalid}});
assert.equal(state.paused, true);
""")


@pytest.mark.parametrize("fail", [False, True])
def test_initial_state_resets_revision_and_invalidates_old_requests(fail):
    run_pause("const fail=" + json.dumps(fail) + ";" + r"""
handlers.initial_state({pause: {paused: false, revision: 9}}); button.focus();
let release; fetchResponse = () => new Promise(resolve => { release = resolve; });
const pending = requestPause(true, '10m');
handlers.initial_state({pause: {paused: false, revision: 0}});
release({ok: !fail, json: async () => ({success: true, paused: true, revision: 10})});
await pending;
assert.equal(state.paused, false);
assert.equal(button.textContent, 'Pausa ▾');
assert.deepEqual(lines, []); assert.deepEqual(toasts, []);
assert.equal(button.disabled, false); assert.equal(listenerCount(), 0);
handlers.paused_update({paused: true, revision: 1});
assert.equal(state.paused, true, 'the restarted server can advance from revision zero');
""")


@pytest.mark.parametrize("event", ["paused_update", "initial_state"])
@pytest.mark.parametrize("pause", [{"paused": False, "revision": "3"},
                                  {"paused": "false", "revision": 3},
                                  {"paused": False, "revision": -1}])
def test_malformed_socket_state_cannot_resume_mining(pause, event):
    run_pause("const event=" + json.dumps(event) + ";const invalid=" + json.dumps(pause) + ";" + r"""
handlers.initial_state({pause: {paused: true, revision: 2}});
handlers[event](event === 'initial_state' ? {pause: invalid} : invalid);
assert.equal(state.paused, true);
assert.equal(button.textContent, 'Riprendi');
""")
