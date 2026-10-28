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
    run_javascript(["onUnlinkedMiningChange", "updateSettingsUI", "toggleMiningHoursRange"], SETUP + r"""
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
    run_javascript(["onUnlinkedMiningChange", "updateSettingsUI", "toggleMiningHoursRange"], SETUP + f"""
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
                    resolve({ok: true, json: async () => ({success: true, settings: {games_to_watch: []}})});
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


def test_game_link_status_is_per_game_and_ignores_unknown_games():
    run_javascript(
        ["getGameLinkStatus", "makeGameLinkBadge"],
        r"""
const assert = require('node:assert/strict');
const state = {
    campaigns: {
        a: {game_name: 'Rust', linked: true},
        b: {game_name: 'rust', linked: false},
        c: {game_name: 'Apex', linked: false},
        d: {linked: true},
    },
    translations: {},
};
const status = getGameLinkStatus();
assert.equal(status.get('rust'), true);
assert.equal(status.get('apex'), false);
assert.equal(status.size, 2);
assert.equal(makeGameLinkBadge(status, 'Manual Game'), null);
""",
    )


GAMES_SETUP = r"""
const assert = require('node:assert/strict');
const makeElement = (tag, attrs = {}, text = null) => ({tag, attrs, text});
let availableGames = new Set(['Zelda', 'Rust', 'Apex', 'Albion', 'Manual']);
let renderedLinkSignature = null;
const state = {
    campaigns: {
        a: {game_name: 'Rust', linked: true},
        b: {game_name: 'Zelda', linked: true},
        c: {game_name: 'Apex', linked: false},
        d: {game_name: 'Albion', linked: false},
    },
    settings: {games_to_watch: ['Apex']},
    translations: {},
};
const controls = {'games-filter': {value: ''}, 'games-link-filter': {value: 'all'}};
globalThis.document = {getElementById: (id) => controls[id] || null};
let selectedArgs = null;
let availableArgs = null;
const renderSelectedGames = (games) => { selectedArgs = games; };
const renderAvailableGames = (games, text, status, filter) => { availableArgs = {games, filter}; };
let saves = 0;
const renderChannels = () => {};
const saveSettings = () => { saves += 1; };
"""

GAMES_FUNCTIONS = [
    "getGameLinkStatus",
    "linkStatusSignature",
    "makeGameLinkBadge",
    "selectLinkedGames",
    "renderGamesToWatch",
    "refreshGamesIfLinkStatusChanged",
]


def test_game_link_badge_text_and_class():
    run_javascript(
        GAMES_FUNCTIONS,
        GAMES_SETUP + r"""
const status = getGameLinkStatus();
const linked = makeGameLinkBadge(status, 'rust');
assert.equal(linked.attrs.class, 'game-link-badge linked');
assert.equal(linked.text, 'LINKED');
const unlinked = makeGameLinkBadge(status, 'Apex');
assert.equal(unlinked.attrs.class, 'game-link-badge not-linked');
assert.equal(unlinked.text, 'NOT LINKED');
""",
    )


def test_available_games_sort_linked_first_and_filter_modes():
    run_javascript(
        GAMES_FUNCTIONS,
        GAMES_SETUP + r"""
renderGamesToWatch();
assert.deepEqual(selectedArgs, ['Apex']);
assert.deepEqual(availableArgs.games, ['Rust', 'Zelda', 'Albion', 'Manual']);
controls['games-link-filter'].value = 'linked';
renderGamesToWatch();
assert.deepEqual(availableArgs.games, ['Rust', 'Zelda']);
controls['games-link-filter'].value = 'not_linked';
renderGamesToWatch();
assert.deepEqual(availableArgs.games, ['Albion']);
assert.equal(availableArgs.filter, 'not_linked');
""",
    )


def test_select_linked_is_idempotent_and_keeps_existing_order():
    run_javascript(
        GAMES_FUNCTIONS,
        GAMES_SETUP + r"""
state.settings.games_to_watch = ['zelda', 'Apex'];
selectLinkedGames();
assert.deepEqual(state.settings.games_to_watch, ['zelda', 'Apex', 'Rust']);
assert.equal(saves, 1);
selectLinkedGames();
assert.deepEqual(state.settings.games_to_watch, ['zelda', 'Apex', 'Rust']);
assert.equal(saves, 1);
""",
    )


def test_games_rerender_only_when_link_status_changes():
    run_javascript(
        GAMES_FUNCTIONS,
        GAMES_SETUP + r"""
renderGamesToWatch();
availableArgs = null;
refreshGamesIfLinkStatusChanged();
assert.equal(availableArgs, null);
state.campaigns.e = {game_name: 'Manual', linked: true};
refreshGamesIfLinkStatusChanged();
assert.notEqual(availableArgs, null);
availableArgs = null;
state.campaigns = {};
refreshGamesIfLinkStatusChanged();
assert.notEqual(availableArgs, null);
""",
    )


def test_available_games_hide_selected_games_regardless_of_case():
    run_javascript(
        GAMES_FUNCTIONS,
        GAMES_SETUP + r"""
state.settings.games_to_watch = ['zelda', 'APEX'];
renderGamesToWatch();
assert.deepEqual(availableArgs.games, ['Rust', 'Albion', 'Manual']);
""",
    )


def test_inventory_changes_refresh_games_list():
    run_javascript(
        GAMES_FUNCTIONS + ["addCampaign", "clearInventory"],
        GAMES_SETUP + r"""
let inventoryRenders = 0;
const renderInventory = () => { inventoryRenders += 1; };
renderGamesToWatch();
availableArgs = null;
addCampaign({id: 'z', game_name: 'Manual', linked: true});
assert.equal(inventoryRenders, 1);
assert.deepEqual(availableArgs.games.slice(0, 3), ['Manual', 'Rust', 'Zelda']);
availableArgs = null;
clearInventory();
assert.equal(inventoryRenders, 2);
assert.deepEqual(availableArgs.games, ['Albion', 'Manual', 'Rust', 'Zelda']);
""",
    )
