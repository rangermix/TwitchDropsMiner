"""Settings > Mining checkbox for preferring followed channels (issue #206)."""

import json
from pathlib import Path

import pytest

from tests.javascript_helpers import NODE
from tests.test_account_link_frontend import SETUP
from tests.test_telegram_frontend import run_javascript


ROOT = Path(__file__).resolve().parents[1]
pytestmark = pytest.mark.skipif(NODE is None, reason="Node.js is required for frontend tests")
FUNCTIONS = ["onPreferFollowedChange", "saveBooleanSetting", "updateSettingsUI", "toggleMiningHoursRange"]
FOLLOW_SETUP = SETUP + r"""
state.translations.gui.settings.prefer_followed_channels_save_error = '<b>Follow save failed</b>';
const follow = document.getElementById('prefer-followed-channels');
const followError = document.getElementById('prefer-followed-error');
"""


def test_defaults_off_and_saves_only_its_own_field():
    run_javascript(FUNCTIONS, FOLLOW_SETUP + r"""
(async () => {
    updateSettingsUI({});
    assert.equal(follow.checked, false, 'off unless the server says true');
    updateSettingsUI({prefer_followed_channels: true}, true);
    assert.equal(follow.checked, true);
    follow.checked = false;
    let finish;
    global.fetch = async (url, options) => {
        assert.equal(url, '/api/settings');
        assert.deepEqual(JSON.parse(options.body), {prefer_followed_channels: false});
        return new Promise(resolve => { finish = resolve; });
    };
    const pending = onPreferFollowedChange();
    assert.equal(follow.disabled, true, 'no duplicate submissions while saving');
    finish({ok: true, json: async () => ({success: true, settings: {prefer_followed_channels: false}})});
    await pending;
    assert.equal(follow.checked, false);
    assert.equal(follow.disabled, false);
    assert.equal(followError.hidden, true);
})().catch(error => { console.error(error); process.exitCode = 1; });
""")


def test_out_of_order_opt_in_responses_keep_both_confirmed_choices():
    run_javascript(FUNCTIONS + ["onUnlinkedMiningChange"], FOLLOW_SETUP + r"""
(async () => {
    updateSettingsUI({prefer_followed_channels: false, allow_unlinked_campaigns: false}, true);
    const requests = [];
    global.fetch = () => new Promise(resolve => requests.push(resolve));
    const result = settings => ({ok: true, json: async () => ({success: true, settings})});
    input.checked = true;
    const savingUnlinked = onUnlinkedMiningChange();
    follow.checked = true;
    const savingFollowed = onPreferFollowedChange();
    updateSettingsUI({prefer_followed_channels: false, allow_unlinked_campaigns: true}, true);
    assert.equal(follow.checked, true, 'pending choice stays visible');
    requests[1](result({prefer_followed_channels: true, allow_unlinked_campaigns: true}));
    await savingFollowed;
    requests[0](result({prefer_followed_channels: false, allow_unlinked_campaigns: true}));
    await savingUnlinked;
    assert.equal(follow.checked, true);
    assert.equal(input.checked, true);
    updateSettingsUI({prefer_followed_channels: false, allow_unlinked_campaigns: false});
    assert.equal(follow.checked, true, 'unrelated partial save cannot roll back the choice');
    assert.equal(input.checked, true);
})().catch(error => { console.error(error); process.exitCode = 1; });
""")


@pytest.mark.parametrize("socket_first", [False, True])
@pytest.mark.parametrize("key", ["prefer_followed_channels", "allow_unlinked_campaigns"])
def test_both_opt_ins_follow_latest_complete_server_revision(socket_first, key):
    run_javascript(FUNCTIONS + ["onUnlinkedMiningChange"], FOLLOW_SETUP + f"""
(async () => {{
    const key = {json.dumps(key)};
    const current = key === 'prefer_followed_channels' ? follow : input;
    const other = current === follow ? input : follow;
    const save = current === follow ? onPreferFollowedChange : onUnlinkedMiningChange;
    const sibling = current === follow ? 'allow_unlinked_campaigns' : 'prefer_followed_channels';
    const snapshot = (revision, main, second) => ({{
        mining_opt_ins_revision: revision, [key]: main, [sibling]: second
    }});
    updateSettingsUI(snapshot(0, false, false), true);
    let finish;
    global.fetch = () => new Promise(resolve => {{ finish = resolve; }});
    current.checked = true;
    const pending = save();
    if ({json.dumps(socket_first)}) {{
        updateSettingsUI(snapshot(2, false, true), true);
        assert.equal(current.checked, true, 'optimistic choice stays visible while pending');
    }}
    finish({{ok: true, json: async () => ({{success: true, settings: snapshot(1, true, true)}})}});
    await pending;
    assert.equal(current.checked, !{json.dumps(socket_first)});
    assert.equal(other.checked, true, 'POST confirms both opt-ins together');
    if (!{json.dumps(socket_first)}) updateSettingsUI(snapshot(2, false, true), true);
    updateSettingsUI(snapshot(1, true, false), true);
    updateSettingsUI({{mining_opt_ins_revision: 3, [key]: true}}, true);
    assert.equal(current.checked, false);
    assert.equal(other.checked, true);
    assert.equal(state.settings.mining_opt_ins_revision, 2);
}})().catch(error => {{ console.error(error); process.exitCode = 1; }});
""")


@pytest.mark.parametrize("failure", ["http", "network", "application", "old_server"])
def test_failed_save_restores_confirmed_value_and_shows_literal_error(failure):
    run_javascript(FUNCTIONS, FOLLOW_SETUP + f"""
state.settings.prefer_followed_channels = false;
follow.checked = true;
global.fetch = async () => {{
    if ('{failure}' === 'network') throw new Error('network');
    return {{ok: '{failure}' !== 'http', json: async () => ({{success: '{failure}' !== 'application', settings: {{}}}})}};
}};
onPreferFollowedChange().then(() => {{
    assert.equal(follow.checked, false);
    assert.equal(follow.disabled, false);
    assert.equal(followError.hidden, false);
    assert.equal(followError.textContent, '<b>Follow save failed</b>');
}}).catch(error => {{ console.error(error); process.exitCode = 1; }});
""")


def test_checkbox_is_labelled_described_and_off_by_default():
    html = (ROOT / "web" / "index.html").read_text(encoding="utf-8")
    assert 'id="prefer-followed-channels" aria-describedby="prefer-followed-help" />' in html
    assert 'id="prefer-followed-error" class="help-text" role="alert" hidden' in html
    for locale in (ROOT / "lang").glob("*.json"):
        settings = json.loads(locale.read_text(encoding="utf-8"))["gui"]["settings"]
        for key in ("prefer_followed_channels", "prefer_followed_channels_help",
                    "prefer_followed_channels_save_error"):
            assert settings[key].strip(), (locale.name, key)
