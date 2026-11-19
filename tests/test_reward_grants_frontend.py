"""Inventory shows how often an Open Reward Campaign goal was earned."""

import pytest

from tests.javascript_helpers import NODE
from tests.test_inventory_watch_controls import run_inventory


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
