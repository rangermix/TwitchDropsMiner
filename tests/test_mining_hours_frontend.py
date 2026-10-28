"""Exercise schedule saves without contacting Twitch or using real settings."""

import subprocess

import pytest

from tests.javascript_helpers import APP_JS, NODE, extract_javascript_function


pytestmark = pytest.mark.skipif(NODE is None, reason="Node.js is required for frontend tests")


def test_schedule_saves_validate_times_and_recover_from_failed_persistence():
    source = APP_JS.read_text(encoding="utf-8")
    script = "\n".join([
        extract_javascript_function(source, "reportSettingsError"),
        extract_javascript_function(source, "toggleMiningHoursRange"),
        "async " + extract_javascript_function(source, "onMiningHoursChange"),
    ]) + r"""
const assert = require('node:assert/strict');
const elements = Object.fromEntries([
    'always', 'range', 'start', 'end', 'error', 'range-inputs'
].map(name => ['mining-hours-' + name, {
    checked: false, value: '', style: {}, hidden: true, disabled: false,
    reportValidity() { return /^([01][0-9]|2[0-3]):[0-5][0-9]$/.test(this.value); }
}]));
const document = { getElementById: id => elements[id] };
const state = {
    settings: { mining_hours: {mode: 'always', start: '08:00', end: '22:00'} },
    translations: {gui: {settings: {mining_hours_save_error: '<img> Save failed'}}}
};
const [always, range, start, end, error] = ['always','range','start','end','error']
    .map(id => elements['mining-hours-' + id]);
let mode = 'success', calls = [];
async function fetch(url, options) {
    assert.ok(always.disabled && range.disabled && start.disabled && end.disabled);
    const request = JSON.parse(options.body);
    assert.deepEqual(Object.keys(request), ['mining_hours']);
    calls.push(request);
    if (mode === 'network') throw new Error('offline');
    return {
        ok: mode !== 'http',
        async json() { return {success: mode !== 'application', settings: {
            mining_hours: mode === 'mismatch' ? state.settings.mining_hours : request.mining_hours
        }}; }
    };
}
function updateSettingsUI(settings) { state.settings = settings; }
(async () => {
    for (const [from, until] of [['10:00','18:00'], ['22:00','06:00'], ['12:00','12:00']]) {
        range.checked = true; always.checked = false; start.value = from; end.value = until;
        await onMiningHoursChange();
        assert.deepEqual(state.settings.mining_hours, {mode: 'range', start: from, end: until});
        assert.equal(error.hidden, true);
        assert.equal(elements['mining-hours-range-inputs'].style.display, '');
    }
    const total = calls.length;
    start.value = '';
    await onMiningHoursChange();
    assert.equal(calls.length, total, 'blank time must not silently become 08:00');
    for (const failure of ['network','http','application','mismatch']) {
        mode = failure;
        start.value = '03:00'; end.value = '04:00';
        await onMiningHoursChange();
        assert.equal(error.hidden, false);
        assert.equal(error.textContent, '<img> Save failed');
        assert.equal(start.value, '12:00'); assert.equal(end.value, '12:00');
        assert.equal(range.checked, true);
        assert.ok(!always.disabled && !range.disabled && !start.disabled && !end.disabled);
    }
    mode = 'success'; always.checked = true; range.checked = false; start.value = '';
    await onMiningHoursChange();
    assert.deepEqual(state.settings.mining_hours, {mode: 'always', start: '12:00', end: '12:00'});
    assert.equal(elements['mining-hours-range-inputs'].style.display, 'none');
    assert.equal(error.hidden, true);
})().catch(error => { console.error(error); process.exitCode = 1; });
"""
    result = subprocess.run([NODE, "-"], input=script, text=True, capture_output=True)
    assert result.returncode == 0, result.stderr


def test_other_settings_saves_do_not_overwrite_a_schedule_save_in_flight():
    source = APP_JS.read_text(encoding="utf-8")
    script = '\n'.join(extract_javascript_function(source, name) for name in (
        'applyGameWatchEdit', 'reconcileGameWatchSettings', 'queueSettingsWrite'))
    script += "\nasync " + extract_javascript_function(source, "saveSettings") + r"""
const assert = require('node:assert/strict');
const gameWatchWrites={confirmed:null,draft:null,pending:[],tail:Promise.resolve(),revision:0};
const state = {settings: {mining_hours: {mode:'always',start:'08:00',end:'22:00'}}};
const document = { getElementById: id => ({checked:false,value:'1'}) };
function parseDropNameBlacklist() { return []; }
function getInventoryFilters() { return {}; }
function renderGamesToWatch(){} function renderChannels(){} function renderInventory(){}
let calls=0;
async function fetch(url, options) {
    calls++;
    const settings = JSON.parse(options.body);
    assert.equal(Object.hasOwn(settings, 'mining_hours'), false);
    return {ok:true,json:async()=>({success:true,settings:{games_to_watch:[]}})};
}
saveSettings().then(()=>assert.equal(calls,1)).catch(error => { console.error(error); process.exitCode = 1; });
"""
    result = subprocess.run([NODE, "-"], input=script, text=True, capture_output=True)
    assert result.returncode == 0, result.stderr
