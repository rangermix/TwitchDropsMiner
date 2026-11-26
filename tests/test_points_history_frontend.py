"""Points tab History section: the SVG balance chart, the channel select, the gains and bets tables."""

import json
import re
from pathlib import Path

import pytest

from tests.javascript_helpers import NODE
from tests.test_reward_priority_frontend import REWARD_DOM
from tests.test_telegram_frontend import run_javascript


ROOT = Path(__file__).resolve().parents[1]
HTML = (ROOT / "web" / "index.html").read_text(encoding="utf-8")
CSS = (ROOT / "web" / "static" / "styles.css").read_text(encoding="utf-8")
APP_JS = (ROOT / "web" / "static" / "app.js").read_text(encoding="utf-8")
ENGLISH = json.loads((ROOT / "lang" / "English.json").read_text(encoding="utf-8"))["gui"]["points"]
pytestmark = pytest.mark.skipif(NODE is None, reason="Node.js is required for frontend tests")

CONSTANTS = APP_JS[APP_JS.index("const HISTORY_CATEGORY_LABELS"):APP_JS.index("let pointsHistoryChannel = '';") + len("let pointsHistoryChannel = '';")]
FUNCTIONS = ["makeElement", "updatePointsHistory", "formatHistoryTime", "buildHistoryChart", "renderPointsHistory",
             "onPointsHistoryChannelChange"]

DOM = REWARD_DOM + CONSTANTS + r"""
document.createElementNS = (ns, tag) => { const node = new Node(tag); node.ns = ns; return node; };
for (const [id, tag] of [['points-history-channel', 'select'], ['points-history-select', 'label'],
        ['points-history-empty', 'p'], ['points-history-body', 'div'], ['points-history-chart', 'div'],
        ['points-history-gains', 'tbody'], ['points-history-bets', 'tbody']])
    root.appendChild(Object.assign(new Node(tag), {id}));
Object.assign(state.translations.gui, {points: {reasons: {watch: 'Watching', claim: 'Bonus claim',
    watch_streak: 'Watch streak', raid: 'Raid', prediction: 'Prediction', other: 'Other'},
    history_chart: 'Balance of {channel} from {from} to {to}, between {min} and {max} points'}});
const el = id => document.getElementById(id);
const all = node => walk(node);
const localeCalls = [];
Date.prototype.toLocaleString = function (...args) { localeCalls.push(args); return 'T' + this.getTime(); };
const sample = (a, b) => ({
    '1': {login: 'alpha', samples: [[1000, 100], [2000, 400], [3000, 250]],
          gains: {watch: 30, bonus: 50, raid: 0, streak: 0, bet: 120, other: 1}, bets: {won: 1, lost: 2, refunded: 3, points_won: 120}},
    '2': {login: 'beta', samples: [[5000, 7]], gains: {}, bets: {}},
    '3': {login: 'nodata', samples: [], gains: {}, bets: {}},
});
"""


def run(body: str) -> None:
    run_javascript(FUNCTIONS, DOM + "(async () => {\n" + body + "\n})().catch(e => { console.error(e); process.exit(1); });")


def test_markup_strings_and_no_inner_html():
    for element in ("points-history-channel", "points-history-chart", "points-history-gains", "points-history-bets"):
        assert f'id="{element}"' in HTML
    for key in ("history_header", "history_empty", "history_chart", "history_bet_points_won"):
        assert key in ENGLISH
    assert "points-history-chart" in CSS and "var(--accent-color)" in CSS
    section = APP_JS[APP_JS.index("function buildHistoryChart"):APP_JS.index("function onPointsHistoryChannelChange")]
    assert "innerHTML" not in section and "createElementNS" in section
    assert re.search(r"points_history", APP_JS)


def test_chart_renders_the_samples_with_a_text_range_and_locale_labels():
    run(r"""
updatePointsHistory({channels: sample()});
const svg = el('points-history-chart').children[0];
assert.equal(svg.tag, 'svg');
assert.equal(svg.ns, 'http://www.w3.org/2000/svg');
assert.equal(svg.attrs.role, 'img');
const expected = 'Balance of alpha from T1000000 to T3000000, between 100 and 400 points';
assert.equal(svg.attrs['aria-label'], expected);
const title = svg.children.find(c => c.tag === 'title');
assert.equal(title.textContent, expected);
const line = svg.children.find(c => c.tag === 'polyline');
assert.equal(line.attrs.points.split(' ').length, 3);
const texts = svg.children.filter(c => c.tag === 'text').map(c => c.textContent);
assert.deepEqual(texts, ['400', '100', 'T1000000', 'T3000000']);
// axis times go through the viewer's locale formatter
assert.ok(localeCalls.length > 0 && localeCalls.every(args => args[0] === undefined && args[1].hour === '2-digit'));
assert.equal(el('points-history-empty').hidden, true);
assert.equal(el('points-history-body').hidden, false);
""")


def test_gains_and_bets_tables_show_the_labels_and_numbers():
    run(r"""
updatePointsHistory({channels: sample()});
const rows = el('points-history-gains').children.map(r => r.children.map(c => c.textContent));
assert.deepEqual(rows, [['Watching', '30'], ['Bonus claim', '50'], ['Raid', '0'], ['Watch streak', '0'],
    ['Prediction', '120'], ['Other', '1']]);
assert.deepEqual(el('points-history-bets').children[0].children.map(c => c.textContent), ['1', '2', '3', '120']);
""")


def test_the_select_lists_channels_with_data_and_switches_the_chart():
    run(r"""
updatePointsHistory({channels: sample()});
const select = el('points-history-channel');
assert.deepEqual(select.children.map(o => o.textContent), ['alpha', 'beta']);
select.value = '2';
select.events.change = undefined;
onPointsHistoryChannelChange({target: select});
const svg = el('points-history-chart').children[0];
assert.ok(svg.attrs['aria-label'].startsWith('Balance of beta'));
// one sample: a dot and one time label, no crash on a flat range
assert.ok(svg.children.some(c => c.tag === 'circle'));
assert.equal(svg.children.filter(c => c.tag === 'text').length, 3);
assert.deepEqual(el('points-history-bets').children[0].children.map(c => c.textContent), ['0', '0', '0', '0']);
// a channel that disappears falls back to the first one with data
updatePointsHistory({channels: {'1': sample()['1']}});
assert.equal(select.value, '1');
""")


def test_empty_state_and_bad_messages():
    run(r"""
updatePointsHistory({channels: {}});
assert.equal(el('points-history-empty').hidden, false);
assert.equal(el('points-history-body').hidden, true);
assert.equal(el('points-history-select').hidden, true);
updatePointsHistory({channels: {'3': {login: 'x', samples: [], gains: {}, bets: {}}}});
assert.equal(el('points-history-body').hidden, true);
updatePointsHistory({channels: {'1': sample()['1']}});
assert.equal(el('points-history-body').hidden, false);
// a malformed message changes nothing
updatePointsHistory(null);
updatePointsHistory({channels: []});
assert.equal(el('points-history-body').hidden, false);
// malformed sample pairs are ignored
updatePointsHistory({channels: {'1': {login: 'a', samples: [[1, 2], 'x', [3, null]], gains: {}, bets: {}}}});
assert.equal(el('points-history-chart').children[0].children.find(c => c.tag === 'polyline').attrs.points.split(' ').length, 1);
""")


def test_a_negative_bet_net_is_shown_with_its_sign():
    run(r"""
const channels = sample();
channels['1'].gains.bet = -250;
updatePointsHistory({channels});
const rows = el('points-history-gains').children.map(r => r.children.map(c => c.textContent));
assert.deepEqual(rows.find(r => r[0] === 'Prediction'), ['Prediction', (-250).toLocaleString()]);
assert.ok(rows.find(r => r[0] === 'Prediction')[1].includes('250') && /^[-−]/.test((-250).toLocaleString()));
""")
