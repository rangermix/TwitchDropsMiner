"""Inventory shows how often an Open Reward Campaign goal was earned."""

import json
from unittest.mock import MagicMock

import pytest

from src.web.managers.inventory import InventoryManager
from tests.javascript_helpers import APP_JS, NODE, extract_javascript_function
from tests.test_inventory_watch_controls import run_inventory
from tests.test_reward_campaigns import _build


pytestmark = pytest.mark.skipif(NODE is None, reason="Node.js required for frontend interaction tests")


def test_reward_drop_shows_times_earned_and_regular_drops_do_not():
    run_inventory(r"""
state.translations.gui.inventory.reward_grants = 'Verdient: {count}';
const drop = changes => ({...campaign().drops[0], ...changes});
state.campaigns = {
 reward: campaign('reward', {drops: [drop({reward_grants: 1})]}),
 zero: campaign('zero', {drops: [drop({reward_grants: 0})]}),
 regular: campaign('regular', {drops: [drop({reward_grants: null})]}),
 old: campaign('old', {drops: [drop({})]}),
 junk: campaign('junk', {drops: [drop({reward_grants: '<img onerror=bad()>'})]}),
};
renderInventory();
const lines = id => walk(content(id)).filter(node => node.className === 'drop-reward-grants');
assert.deepEqual(lines('reward').map(node => node.textContent), ['Verdient: 1']);
assert.deepEqual(lines('zero').map(node => node.textContent), ['Verdient: 0']);
for (const id of ['regular', 'old', 'junk']) assert.equal(lines(id).length, 0, id);
""")


def test_times_earned_falls_back_to_english_without_a_translation():
    run_inventory(r"""
state.campaigns = {one: campaign('one', {drops: [{...campaign().drops[0], reward_grants: 3}]})};
renderInventory();
assert.equal(walk(content('one')).filter(node => node.className === 'drop-reward-grants')[0].textContent, 'Times earned: 3');
""")


def test_inventory_filters_and_displays_a_reward_without_artwork():
    serialized = InventoryManager(MagicMock(), MagicMock())._serialize_campaign(_build())
    filter_source = extract_javascript_function(APP_JS.read_text(encoding="utf-8"), "campaignMatchesFilters")
    setup = f"campaignMatchesFilters = {filter_source};\nconst reward = {json.dumps(serialized)};\n"
    run_inventory(setup + r"""
state.translations.gui.inventory.filters.other = 'Other reward';
state.campaigns = {reward, regular: campaign('regular')};
getInventoryFilters = () => ({link_status: 'all', game_name_search: [], show_benefit_other: true});
renderInventory();
assert.ok(content(reward.id));
assert.equal(content('regular'), null);
const rewardNodes = walk(content(reward.id));
assert.ok(rewardNodes.some(node => node.className === 'benefit-name' && node.textContent === 'GTA DVD Chest'));
assert.ok(rewardNodes.some(node => node.className === 'benefit-type' && node.textContent === 'Other reward'));
assert.equal(rewardNodes.filter(node => node.tag === 'img' && node.className === 'benefit-icon').length, 0);

getInventoryFilters = () => ({link_status: 'all', game_name_search: [], show_benefit_item: true});
renderInventory();
assert.equal(content(reward.id), null);
const art = walk(content('regular')).find(node => node.tag === 'img' && node.className === 'benefit-icon');
assert.equal(art.getAttribute('src'), 'https://example.test/car.jpg');
""")
