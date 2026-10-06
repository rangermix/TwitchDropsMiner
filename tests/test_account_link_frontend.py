"""Exercise account-link UI functions with mocked network responses."""

import json
from pathlib import Path

import pytest

from tests.javascript_helpers import NODE
from tests.test_telegram_frontend import run_javascript


ROOT = Path(__file__).resolve().parents[1]
pytestmark = pytest.mark.skipif(NODE is None, reason="Node.js is required for frontend tests")


@pytest.mark.parametrize("mode", ["all", "linked", "not_linked"])
@pytest.mark.parametrize("linked", [False, True])
def test_link_filter_selects_metadata_independently_of_override(mode, linked):
    run_javascript(
        ["campaignMatchesFilters"],
        f"""
const assert = require('node:assert/strict');
const campaign = {{linked: {json.dumps(linked)}, total_drops: 1, claimed_drops: 0}};
const filters = {{link_status: '{mode}', show_benefit_item: true, show_benefit_badge: true,
                  show_benefit_emote: true, show_benefit_other: true}};
const expected = {json.dumps(mode == 'all' or (mode == 'linked') == linked)};
assert.equal(campaignMatchesFilters(campaign, filters), expected);
filters.allow_unlinked_campaigns = true;
assert.equal(campaignMatchesFilters(campaign, filters), expected);
filters.show_active = true;
assert.equal(campaignMatchesFilters(campaign, filters), false);
campaign.active = true;
assert.equal(campaignMatchesFilters(campaign, filters), expected);
""",
    )


SETUP = r"""
const assert = require('node:assert/strict');
const elements = {};
const document = { getElementById(id) { return elements[id] ??= {checked: false, hidden: true, value: '', style: {}}; },
                   body: {classList: {add() {}, remove() {}}} };
const state = {settings: {}, translations: {gui: {settings: {allow_unlinked_campaigns_save_error: '<img src=x> Saved failed'}}}};
function applyInventoryViewMode() {}
function renderGamesToWatch() {}
function renderChannels() {}
function renderInventory() {}
function updateGameTagsDisplay() {}
let availableGames = new Set();
let selectedInventoryGames = [];
const input = document.getElementById('allow-unlinked-campaigns');
const error = document.getElementById('allow-unlinked-error');
"""


def test_override_default_restore_enable_disable_and_pending_state():
    run_javascript(["onUnlinkedMiningChange", "updateSettingsUI"], SETUP + r"""
(async () => {
    updateSettingsUI({});
    assert.equal(input.checked, false);
    for (const enabled of [true, false]) {
        input.checked = enabled;
        let finish;
        global.fetch = async (url, options) => {
            assert.equal(url, '/api/settings');
            assert.equal(options.method, 'POST');
            assert.deepEqual(JSON.parse(options.body), {allow_unlinked_campaigns: enabled});
            return new Promise(resolve => { finish = resolve; });
        };
        const pending = onUnlinkedMiningChange();
        assert.equal(input.disabled, true);
        finish({ok: true, json: async () => ({success: true, settings: {allow_unlinked_campaigns: enabled}})});
        await pending;
        assert.equal(input.disabled, false);
        assert.equal(input.checked, enabled);
        assert.equal(state.settings.allow_unlinked_campaigns, enabled);
        assert.equal(error.hidden, true);
    }
})().catch(error => { console.error(error); process.exitCode = 1; });
""")


@pytest.mark.parametrize("failure", ["http", "network", "application", "old_server"])
@pytest.mark.parametrize("saved", [False, True])
def test_failed_save_restores_confirmed_setting_and_shows_literal_error(failure, saved):
    run_javascript(["onUnlinkedMiningChange", "updateSettingsUI"], SETUP + f"""
state.settings.allow_unlinked_campaigns = {json.dumps(saved)};
input.checked = !state.settings.allow_unlinked_campaigns;
global.fetch = async () => {{
    if ('{failure}' === 'network') throw new Error('network');
    return {{ok: '{failure}' !== 'http', json: async () => ({{success: '{failure}' !== 'application', settings: {{}}}})}};
}};
onUnlinkedMiningChange().then(() => {{
    assert.equal(input.checked, {json.dumps(saved)});
    assert.equal(input.disabled, false);
    assert.equal(error.hidden, false);
    assert.equal(error.textContent, '<img src=x> Saved failed');
}}).catch(error => {{ console.error(error); process.exitCode = 1; }});
""")


def test_controls_are_accessible_and_override_defaults_unchecked():
    html = (ROOT / "web" / "index.html").read_text(encoding="utf-8")
    assert 'aria-labelledby="filter-link-status-label"' in html
    assert 'aria-describedby="allow-unlinked-warning"' in html
    assert 'id="allow-unlinked-error" class="help-text" role="alert" hidden' in html
    assert 'id="allow-unlinked-campaigns" aria-describedby="allow-unlinked-warning" />' in html


def test_delayed_unrelated_autosave_cannot_undo_explicit_override():
    run_javascript(["saveSettings", "onUnlinkedMiningChange", "updateSettingsUI"], SETUP + r"""
function getInventoryFilters() { return {}; }
function parseDropNameBlacklist() { return []; }
(async () => {
    let serverValue = false;
    let completeAutosave;
    global.fetch = async (url, options) => {
        const update = JSON.parse(options.body);
        if (!Object.hasOwn(update, 'allow_unlinked_campaigns')) {
            return new Promise(resolve => {
                completeAutosave = () => {
                    assert.equal(serverValue, true);
                    resolve({ok: true});
                };
            });
        }
        serverValue = update.allow_unlinked_campaigns;
        return {ok: true, json: async () => ({success: true, settings: {allow_unlinked_campaigns: serverValue}})};
    };
    const autosave = saveSettings();
    input.checked = true;
    await onUnlinkedMiningChange();
    assert.equal(serverValue, true);
    completeAutosave();
    await autosave;
    assert.equal(serverValue, true);
    assert.equal(input.checked, true);
})().catch(error => { console.error(error); process.exitCode = 1; });
""")
