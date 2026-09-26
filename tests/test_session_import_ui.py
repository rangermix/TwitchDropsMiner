"""Helper-only dashboard entry points, connection gate and session status."""

import subprocess
from pathlib import Path

import pytest

from tests.javascript_helpers import NODE


ROOT = Path(__file__).resolve().parents[1]


def test_dashboard_offers_helper_login_without_manual_credential_controls():
    html = (ROOT / "web/index.html").read_text()
    for old_id in (
        'session-import-panel', 'session-import-file', 'session-import-pair',
        'session-import-revoke', 'browser-login', 'login-form', 'oauth-code-display',
        'username', 'password', '2fa-token',
    ):
        assert f'id="{old_id}"' not in html
    assert 'id="allow-helper-connection"' in html
    assert 'id="helper-instance-url"' in html
    assert 'readonly' in html
    assert '/static/session-import.js' not in html
    assert '/static/helper-login.js?v=__APP_VERSION__' in html
    assert '/releases/download/' not in html
    assert 'id="web-auth-settings"' in html


SCRIPT = ROOT / "web/static/helper-login.js"
HARNESS = r"""
const assert = require('node:assert/strict');
const vm = require('node:vm');
const fs = require('node:fs');
const elements = new Map();
const doc = {getElementById(id) {
  if (!elements.has(id)) elements.set(id, {hidden:false, disabled:false, checked:false,
    textContent:'', value:'', listeners:{}, addEventListener(name, callback){this.listeners[name]=callback;},
    focus(){this.focused=true;}, select(){this.selected=true;}});
  return elements.get(id);
}, addEventListener(){}};
const context = {window:{location:{origin:'https://tdm.test'}}, document:doc, Date, setInterval(){}, console};
vm.createContext(context);
vm.runInContext(fs.readFileSync(process.argv[1], 'utf8'), context);
const t = {title:'<img onerror=bad()>', step_download:'Download and run', step_instance:'Enter the instance address',
 step_chrome:'Sign in in Chrome', step_finish:'Wait for the helper result', builds:'Native builds', builds_note:'Unreleased build artifacts',
 instance:'This instance', copy:'Copy address', copied:'Copied', copy_manually:'Select and copy the address', retry:'Retry status',
 allow:'Allow helper connection', settings_title:'Twitch helper', setting_help:'Allows login and account replacement; closes after success; renewal continues',
 saving:'Saving', save_error:'Could not save', open:'Connections allowed', closed:'Connections closed; enable in Settings',
 checking:'Checking status', ready:'Verified until {expiry}', waiting:'Waiting for helper login', expired:'Session expired',
 session_error:'Session unavailable', status_error:'Status unavailable', existing:'Saved Twitch login active',
 renewal:'TDM renews automatically while helper connection is off', renewal_error:'Renewal needs attention', renewal_unavailable:'Run helper again to enable renewal',renewal_retrying:'TDM is retrying renewal'};
const el=id=>doc.getElementById(id);
let calls=[];
let reply={ok:true,json:async()=>({enabled:true,allow_helper_connection:true,session:{state:'waiting'}})};
let fetcher=async(url,options={})=>{calls.push({url,options});return reply;};
const accepted=[];
const panel=new context.window.HelperLoginPanel(doc, (...args)=>fetcher(...args), ()=>t,
 'https://tdm.test', data=>accepted.push(data));
const status=(allowed,session={state:'waiting'})=>({enabled:true,allow_helper_connection:allowed,session,renewal_available:true});
"""


def run_panel(body: str) -> None:
    assert SCRIPT.exists(), "the helper panel must replace the manual import UI"
    result = subprocess.run(
        [NODE, "-e", HARNESS + "\n(async()=>{\n" + body + r"""
})().catch(error=>{console.error(error);process.exit(1)});
""", str(SCRIPT)],
        capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stderr


@pytest.mark.skipif(NODE is None, reason="Node required for DOM behavior tests")
def test_helper_panel_loads_gate_and_safe_instructions_without_password_prerequisite():
    run_panel(r"""
assert.equal(el('allow-helper-connection').disabled,true);
await panel.load();
assert.equal(calls[0].url,'/api/session');
assert.equal(calls[0].options.cache,'no-store');
assert.equal(el('allow-helper-connection').checked,true);
assert.equal(el('allow-helper-connection').disabled,false);
assert.equal(el('helper-session-status').textContent,'Waiting for helper login');
assert.equal(el('helper-settings-title').textContent,'Twitch helper');
assert.equal(el('helper-step-download').textContent,t.step_download);
assert.equal(el('helper-instance-url').value,'https://tdm.test');
assert.equal(el('helper-login-instructions').hidden,false);
assert.equal(el('helper-gate-status').textContent,t.open);
assert.equal(calls.some(call=>call.url.includes('/api/auth')),false);
""")


@pytest.mark.skipif(NODE is None, reason="Node required for DOM behavior tests")
def test_accepted_helper_login_closes_gate_and_keeps_automatic_renewal_visible():
    run_panel(r"""
await panel.load();
panel.updateStatus(status(false,{state:'ready',user_id:42,generation:1,expires_at:4600,paired:false}));
assert.equal(el('allow-helper-connection').checked,false);
assert.equal(el('helper-gate-status').textContent,t.closed);
assert.equal(el('helper-login-instructions').hidden,true);
assert.equal(el('helper-renewal-status').textContent,t.renewal);
assert.equal(accepted.at(-1).user_id,42);
assert.match(el('helper-session-status').textContent,/^Verified until /);
panel.updateSettings({allow_helper_connection:true});
assert.equal(el('allow-helper-connection').checked,true);
assert.equal(el('helper-login-instructions').hidden,false);
panel.updateStatus({...status(false,{state:'expired',user_id:42,error:'private-token'}),renewal_error:'private-token',renewal_requires_login:true});
assert.equal(el('helper-session-status').textContent,t.expired);
assert.equal(el('helper-renewal-status').textContent,t.renewal_error);
for(const value of elements.values()) assert.equal(value.textContent.includes('private-token'),false);
""")


@pytest.mark.skipif(NODE is None, reason="Node required for DOM behavior tests")
def test_saved_legacy_login_does_not_look_like_a_required_helper_login():
    run_panel(r"""
await panel.load();
panel.updateLogin({user_id:17});
assert.equal(el('helper-session-status').textContent,t.existing);
assert.equal(el('helper-renewal-status').textContent,'');
""")


@pytest.mark.skipif(NODE is None, reason="Node required for DOM behavior tests")
@pytest.mark.parametrize("allowed", [True, False])
def test_helper_setting_saves_only_gate_and_waits_for_server(allowed):
    run_panel(r"""
await panel.load();
const desired=DESIRED;
el('allow-helper-connection').checked=desired;
fetcher=async(url,options={})=>{
 calls.push({url,options});
 if(options.method==='POST') return {ok:true,json:async()=>({success:true,settings:{allow_helper_connection:desired}})};
 return {ok:true,json:async()=>status(desired)};
};
await panel.saveAllowed();
const request=calls.find(call=>call.options.method==='POST');
assert.equal(request.url,'/api/settings');
assert.equal(request.options.headers['X-TDM-Request'],'1');
assert.equal(request.options.body,JSON.stringify({allow_helper_connection:desired}));
assert.equal(el('allow-helper-connection').checked,desired);
assert.equal(el('allow-helper-connection').disabled,false);
assert.equal(el('helper-setting-result').textContent,'');
""".replace("DESIRED", "true" if allowed else "false"))


@pytest.mark.skipif(NODE is None, reason="Node required for DOM behavior tests")
@pytest.mark.parametrize("failure", [
    "({ok:false,json:async()=>({detail:'private-response'})})",
    "({ok:true,json:async()=>({success:false})})",
    "({ok:true,json:async()=>({success:true,settings:{}})})",
    "Promise.reject(new Error('private-response'))",
])
def test_failed_setting_save_restores_gate_and_shows_fixed_error(failure):
    run_panel(r"""
await panel.load();
el('allow-helper-connection').checked=false;
fetcher=async()=>FAILURE;
await panel.saveAllowed();
assert.equal(el('allow-helper-connection').checked,true);
assert.equal(el('allow-helper-connection').disabled,false);
assert.equal(el('helper-setting-result').textContent,t.save_error);
for(const value of elements.values()) assert.equal(value.textContent.includes('private-response'),false);
""".replace("FAILURE", failure))


@pytest.mark.skipif(NODE is None, reason="Node required for DOM behavior tests")
def test_status_load_failure_can_be_retried_without_reloading_dashboard():
    run_panel(r"""
fetcher=async()=>({ok:false,json:async()=>({detail:'private'})});
await panel.load();
assert.equal(el('allow-helper-connection').disabled,true);
assert.equal(el('helper-session-status').textContent,t.status_error);
assert.equal(el('helper-status-retry').hidden,false);
fetcher=async()=>reply;
await el('helper-status-retry').listeners.click();
assert.equal(el('allow-helper-connection').disabled,false);
assert.equal(el('helper-status-retry').hidden,true);
assert.equal(el('helper-session-status').textContent,t.waiting);
""")


@pytest.mark.skipif(NODE is None, reason="Node required for DOM behavior tests")
def test_old_get_response_cannot_reopen_gate_after_live_acceptance():
    run_panel(r"""
let finish;
fetcher=()=>new Promise(resolve=>{finish=resolve;});
const loading=panel.load();
panel.updateStatus(status(false,{state:'ready',user_id:42,generation:1,expires_at:4600}));
finish(reply);
await loading;
assert.equal(el('allow-helper-connection').checked,false);
assert.equal(el('helper-login-instructions').hidden,true);
""")


@pytest.mark.skipif(NODE is None, reason="Node required for DOM behavior tests")
def test_copy_address_uses_clipboard_or_selectable_fallback():
    run_panel(r"""
await panel.load();
await panel.copyInstance();
assert.equal(el('helper-instance-url').selected,true);
assert.equal(el('helper-copy-result').textContent,t.copy_manually);
let copied;
context.navigator={clipboard:{writeText:async value=>{copied=value;}}};
await panel.copyInstance();
assert.equal(copied,'https://tdm.test');
assert.equal(el('helper-copy-result').textContent,t.copied);
""")


@pytest.mark.skipif(NODE is None, reason="Node required for DOM behavior tests")
def test_migrated_session_without_seed_requests_helper_login_for_renewal():
    run_panel(r"""
panel.updateStatus({...status(false,{state:'ready',user_id:42,generation:1,expires_at:4600}),renewal_available:false});
assert.equal(el('helper-renewal-status').textContent,t.renewal_unavailable);
assert.equal(el('helper-login-instructions').hidden,false);
assert.equal(el('allow-helper-connection').checked,false);
assert.equal(accepted.at(-1).user_id,42);
""")


@pytest.mark.skipif(NODE is None, reason="Node required for DOM behavior tests")
def test_stale_save_response_cannot_reopen_gate_after_login_succeeds():
    run_panel(r"""
await panel.load();
let finish;
fetcher=async(url,options={})=>{
 if(options.method==='POST') return new Promise(resolve=>{finish=resolve;});
 return {ok:true,json:async()=>status(false,{state:'ready',user_id:42,generation:1,expires_at:4600})};
};
el('allow-helper-connection').checked=true;
const saving=panel.saveAllowed();
assert.equal(el('allow-helper-connection').disabled,true);
panel.updateStatus(status(false,{state:'ready',user_id:42,generation:1,expires_at:4600}));
finish({ok:true,json:async()=>({success:true,settings:{allow_helper_connection:true}})});
await saving;
assert.equal(el('allow-helper-connection').checked,false);
assert.equal(el('allow-helper-connection').disabled,false);
assert.equal(el('helper-login-instructions').hidden,true);
""")


def test_unrelated_settings_save_cannot_reopen_helper_gate():
    from tests.javascript_helpers import APP_JS, extract_javascript_function

    save = extract_javascript_function(APP_JS.read_text(), "saveSettings")
    assert "allow_helper_connection" not in save
    assert "session-import" not in APP_JS.read_text()
    assert "/api/login" not in APP_JS.read_text()
    assert "/api/oauth/confirm" not in APP_JS.read_text()


@pytest.mark.skipif(NODE is None, reason="Node required for DOM behavior tests")
def test_pending_setting_choice_stays_visible_until_server_result():
    run_panel(r"""
await panel.load();
let fail;
fetcher=()=>new Promise((resolve,reject)=>{fail=reject;});
el('allow-helper-connection').checked=false;
const saving=panel.saveAllowed();
assert.equal(el('allow-helper-connection').checked,false);
assert.equal(el('allow-helper-connection').disabled,true);
fail(new Error('network'));
await saving;
assert.equal(el('allow-helper-connection').checked,true);
assert.equal(el('helper-setting-result').textContent,t.save_error);
panel.updateStatus(status(false,{state:'ready',user_id:42,generation:1,expires_at:4600}));
assert.equal(el('helper-setting-result').textContent,'');
assert.equal(el('allow-helper-connection').checked,false);
""")


@pytest.mark.skipif(NODE is None, reason="Node required for DOM behavior tests")
def test_pending_login_invalidates_stale_ready_fetch_and_cached_status():
    run_panel(r"""
panel.updateStatus(status(false,{state:'ready',user_id:42,generation:1,expires_at:4600}));
let finish;
fetcher=()=>new Promise(resolve=>{finish=resolve;});
const loading=panel.load();
const before=accepted.length;
panel.updateLogin({user_id:null,import_pending:true});
assert.equal(el('helper-session-status').textContent,t.waiting);
assert.equal(el('helper-login-instructions').hidden,false);
finish({ok:true,json:async()=>status(false,{state:'ready',user_id:42,generation:1,expires_at:4600})});
await loading;
assert.equal(accepted.length,before);
assert.equal(el('helper-session-status').textContent,t.waiting);
""")


@pytest.mark.skipif(NODE is None, reason="Node required for DOM behavior tests")
def test_transient_renewal_failure_shows_automatic_retry_without_relogin_prompt():
    run_panel(r"""
panel.updateStatus({...status(false,{state:'ready',user_id:42,generation:1,expires_at:4600}),
 renewal_error:'private-transient',renewal_requires_login:false});
assert.equal(el('helper-renewal-status').textContent,t.renewal_retrying);
assert.equal(el('helper-login-instructions').hidden,true);
assert.equal(el('allow-helper-connection').checked,false);
assert.equal(accepted.at(-1).user_id,42);
""")


@pytest.mark.skipif(NODE is None, reason="Node required for DOM behavior tests")
@pytest.mark.parametrize("session_state", ["waiting", "expired"])
def test_imported_identity_clears_when_status_loses_readiness(session_state):
    run_panel(r"""
panel.updateStatus(status(false,{state:'ready',user_id:42,generation:1,expires_at:4600}));
panel.updateStatus(status(false,{state:'SESSION_STATE',user_id:42,generation:1,expires_at:4600}));
assert.equal(accepted.at(-1).user_id,null);
assert.equal(el('helper-login-instructions').hidden,false);
const count=accepted.length;
panel.updateLogin({user_id:17});
panel.updateStatus(status(true,{state:'waiting',user_id:null,generation:0,expires_at:null}));
assert.equal(accepted.length,count);
assert.equal(el('helper-session-status').textContent,t.existing);
""".replace("SESSION_STATE", session_state))


@pytest.mark.skipif(NODE is None, reason="Node required for DOM behavior tests")
def test_unrelated_settings_snapshot_cannot_override_live_helper_gate():
    from tests.javascript_helpers import APP_JS, extract_javascript_function

    function = extract_javascript_function(APP_JS.read_text(), "updateSettingsUI")
    run_panel(function + r"""
const document=doc;
document.body={classList:{add(){},remove(){}}};
for(const id of ['proxy-indicator']) el(id).style={};
const state={settings:{}};
function applyInventoryViewMode(){}
function renderGamesToWatch(){}
function renderChannels(){}
function renderInventory(){}
function updateGameTagsDisplay(){}
globalThis.helperLoginPanel=panel;
panel.updateStatus(status(false,{state:'ready',user_id:42,generation:1,expires_at:4600}));
updateSettingsUI({allow_helper_connection:true,telegram_chat_id:'42'});
assert.equal(el('allow-helper-connection').checked,false);
updateSettingsUI({allow_helper_connection:true},true);
assert.equal(el('allow-helper-connection').checked,true);
""")


def test_helper_dashboard_translation_keys_match_typed_schema_in_every_locale():
    import json

    from src.i18n.translator import GUIHelperLogin, GUILoginForm

    for path in (ROOT / "lang").glob("*.json"):
        gui = json.loads(path.read_text())["gui"]
        assert set(gui["helper_login"]) == set(GUIHelperLogin.__annotations__), path.name
        assert set(gui["login"]) == set(GUILoginForm.__annotations__), path.name
        assert "session_import" not in gui
