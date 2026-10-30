"""Now Watching: thumbnail by default, Twitch player only after an explicit click."""

import json
import subprocess

import pytest

from tests.javascript_helpers import APP_JS, NODE, extract_javascript_function


HARNESS = r"""
const assert = require('node:assert/strict');
const elements = new Map();
function element(id) {
    const classes = new Set();
    return {id, dataset: {}, style: {}, hidden: false, textContent: '', disabled: false, children: [],
        attributes: {}, classList: {
            add(name) { classes.add(name); }, remove(name) { classes.delete(name); },
            contains(name) { return classes.has(name); }
        }, setAttribute(name, value) { this.attributes[name] = value; },
        appendChild(child) { child.parent = this; this.children.push(child); return child; },
        remove() { this.parent.children = this.parent.children.filter(c => c !== this); },
        querySelector(tag) { return this.children.find(c => c.tagName === tag) || null; },
        focus() { focused = this.id; },
        replaceChildren(...nodes) {
            this.children = nodes;
            this.textContent = nodes.map(node => node.textContent ?? String(node)).join('');
        }};
}
let focused = null;
const document = {getElementById(id) {
    if (!elements.has(id)) elements.set(id, element(id));
    return elements.get(id);
}, get activeElement() { return focused && elements.get(focused); },
createElement(tag) { return Object.assign(element(), {tagName: tag}); },
createTextNode(text) { return {textContent: text}; }};
const location = {protocol: 'https:', hostname: 'miner.example'};
const opened = [];
const window = {open(...args) { opened.push(args); }};
let now = 120000;
Date.now = () => now;
const watching = {id: 7, name: '配信者', login: 'Some_Streamer',
    url: 'https://www.twitch.tv/some_streamer', online: true, watching: true, viewers: 123};
const $ = id => document.getElementById(id);
const player = () => $('now-watching-preview').querySelector('iframe');
"""


def run_preview_script(assertions: str):
    source = APP_JS.read_text()
    functions = "\n".join(extract_javascript_function(source, name)
                          for name in ("updateNowWatching", "setNowWatchingPlayer", "makeElement",
                                       "twitchChannelUrl", "canEmbedTwitchPlayer"))
    script = HARNESS + source.split("// ==================== UI Utilities")[0] + functions
    script += r"""
state.channels = {7: watching};
state.translations = {gui: {channels: {now_watching: 'Watching', online: 'Online',
    play_stream: 'Play', watch_on_twitch: 'Watch', close_player: 'Close', viewers: 'viewers'}}};
""" + assertions
    result = subprocess.run([NODE, "-"], input=script, capture_output=True, text=True, encoding="utf-8")
    assert result.returncode == 0, result.stderr


pytestmark = pytest.mark.skipif(NODE is None, reason="Node required for DOM behavior tests")


def test_thumbnail_shows_by_default_and_refreshes_at_most_once_a_minute():
    run_preview_script(r"""
updateNowWatching();
const img = $('now-watching-img');
assert.match(img.style.backgroundImage, /live_user_some_streamer-440x248.jpg/);
assert.equal($('now-watching-play').hidden, false);
assert.equal($('now-watching-play').textContent, '▶ Play');
assert.equal($('now-watching-close').hidden, true);
assert.equal(player(), null, 'no player and no video until the user clicks Play');
const first = img.dataset.src;
now += 1000; updateNowWatching(); assert.equal(img.dataset.src, first);
now += 60000; updateNowWatching(); assert.notEqual(img.dataset.src, first);
assert.match($('now-watching-info').textContent, /配信者.*123/);
state.channels = {};
updateNowWatching();
assert.equal(img.style.backgroundImage, '');
assert.ok($('now-watching').classList.contains('hidden'));
""")


def test_play_embeds_twitch_player_for_this_host_and_close_restores_thumbnail():
    run_preview_script(r"""
updateNowWatching();
setNowWatchingPlayer(true);
const frame = player();
assert.equal(frame.attributes.src,
    'https://player.twitch.tv/?channel=some_streamer&parent=miner.example&autoplay=true');
assert.equal(frame.attributes.title, 'Twitch: 配信者');
assert.equal($('now-watching-play').children[0].attributes['aria-hidden'], 'true');
assert.equal($('now-watching-img').hidden, true);
assert.equal($('now-watching-play').hidden, true);
assert.equal($('now-watching-close').hidden, false);
assert.equal(focused, 'now-watching-close');
// Channel events while playing keep the same player.
now += 60000; updateNowWatching();
assert.equal(player(), frame);
setNowWatchingPlayer(false);
assert.equal(player(), null);
assert.equal($('now-watching-img').hidden, false);
assert.equal(focused, 'now-watching-play');
""")


def test_channel_switch_or_missing_login_closes_the_player():
    run_preview_script(r"""
setNowWatchingPlayer(true);
assert.ok(player());
state.channels = {8: {...watching, id: 8, login: 'other'}};
updateNowWatching();
assert.equal(player(), null, 'never start a new channel without a click');
assert.equal(state.playerLogin, null);
assert.match($('now-watching-img').dataset.src, /live_user_other-/);
setNowWatchingPlayer(true);
assert.equal(focused, 'now-watching-close');
state.channels = {8: {...watching, id: 8, login: ''}};
updateNowWatching();
assert.equal(player(), null);
assert.equal($('now-watching-play').hidden, true, 'nothing to play without a canonical login');
assert.equal($('now-watching-img').style.backgroundImage, '', 'no stale thumbnail from the previous channel');
assert.ok(!$('now-watching-img').dataset.src);
// Focus that was on Close moves to Play when a switch closes the player.
state.channels = {7: watching}; setNowWatchingPlayer(true);
state.channels = {8: {...watching, id: 8, login: 'other'}}; updateNowWatching();
assert.equal(focused, 'now-watching-play');
""")


def test_labels_are_translated_as_text():
    run_preview_script(r"""
state.translations.gui.channels.play_stream = '<b>Lire</b>';
state.translations.gui.channels.close_player = '<i>Fermer</i>';
updateNowWatching();
assert.equal($('now-watching-play').textContent, '▶ <b>Lire</b>');
assert.equal($('now-watching-close').textContent, '<i>Fermer</i>');
""")


def test_watching_channel_links_only_to_twitch():
    run_preview_script(r"""
updateNowWatching();
let link = $('now-watching-info').children[0];
assert.equal(link.tagName, 'a');
assert.equal(link.attributes.href, 'https://www.twitch.tv/some_streamer');
assert.equal(link.attributes.rel, 'noopener noreferrer');
watching.url = 'javascript:alert(1)';
updateNowWatching();
assert.equal($('now-watching-info').children[0].tagName, undefined);
""")


def test_player_controls_exist_and_are_translated_everywhere():
    root = APP_JS.parents[2]
    html = (root / 'web/index.html').read_text()
    assert 'id="now-watching-play"' in html and 'id="now-watching-close"' in html
    for locale in (root / 'lang').glob('*.json'):
        channels = json.loads(locale.read_text())['gui']['channels']
        for key in ('play_stream', 'watch_on_twitch', 'close_player'):
            assert channels[key].strip(), (locale.name, key)


def test_plain_http_lan_dashboard_opens_twitch_instead_of_a_refused_embed():
    run_preview_script(r"""
location.protocol = 'http:'; location.hostname = '192.168.1.50';
updateNowWatching();
assert.equal($('now-watching-play').hidden, false);
assert.equal($('now-watching-play').textContent, '↗ Watch', 'say it opens Twitch, not an inline player');
setNowWatchingPlayer(true);
assert.equal(player(), null, 'Twitch shows "misconfigured" for embeds on plain-HTTP hosts');
assert.deepEqual(opened, [['https://www.twitch.tv/some_streamer', '_blank', 'noopener,noreferrer']]);
assert.equal(state.playerLogin, null);
watching.url = 'javascript:alert(1)';
updateNowWatching();
assert.equal($('now-watching-play').hidden, true, 'no player and no safe link: nothing to play');
// Loopback HTTP is accepted by Twitch.
for (const host of ['localhost', '127.0.0.1']) {
    location.hostname = host; watching.url = 'https://www.twitch.tv/some_streamer';
    setNowWatchingPlayer(true); assert.ok(player(), host); setNowWatchingPlayer(false);
}
""")
