"""Login/verification/dashboard transitions and safe translated controls in Node."""

import json
import subprocess
from pathlib import Path

import pytest

from src.i18n.translator import GUIBrowserLogin, HelperErrors, HelperMessages
from tests.javascript_helpers import NODE


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "web/static/browser-login.js"


def test_embedded_login_defaults_and_explicit_helper_has_versioned_downloads():
    html = (ROOT / "web/index.html").read_text(encoding="utf-8")
    assert 'id="twitch-vnc"' in html and 'id="dashboard"' in html
    assert 'id="twitch-login-finish"' in html
    assert html.index('id="twitch-logout"') > html.index('id="settings-actions-header"')
    assert "helper-login.js" not in html and "allow-helper-connection" not in html
    assert "/static/browser-login.js?v=__APP_VERSION__" in html
    assert 'id="twitch-helper-panel"' in html
    assert 'id="twitch-helper-url" type="text" readonly autocomplete="off"' in html
    assert "twitch-helper-code" not in html and "twitch-helper-copy" not in html
    for platform in ("windows-x64", "linux-x64", "macos-arm64", "macos-x64"):
        assert ("https://github.com/rangermix/TwitchDropsMiner/releases/download/v__APP_VERSION__/"
                f"tdm-login-helper-__APP_VERSION__-{platform}.tar.gz") in html


def test_locales_match_embedded_and_optional_helper_schemas():
    for path in (ROOT / "lang").glob("*.json"):
        locale = json.loads(path.read_text(encoding="utf-8"))
        assert set(locale["gui"]["browser_login"]) == set(GUIBrowserLogin.__annotations__), path
        assert set(locale["helper"]) == set(HelperMessages.__annotations__), path
        assert set(locale["helper"]["errors"]) == set(HelperErrors.__annotations__), path
        assert "connection_prompt" not in locale["helper"]
        assert "terminal" not in locale["helper"]["errors"]


HARNESS = r"""
const assert = require('node:assert/strict');
const vm = require('node:vm');
const fs = require('node:fs');
const elements = new Map();
const doc = {getElementById(id) {
    if (!elements.has(id)) elements.set(id, {hidden:false, disabled:false, textContent:'', value:'',
        listeners:{}, addEventListener(event,cb){this.listeners[event]=cb;}, replaceChildren(){},
        appendChild(child){child.parentElement=this;}});
    return elements.get(id);
},addEventListener(){}};
let confirmed = true;
let now = 1000;
const context = {window:{location:{href:'https://tdm.test/'},confirm:()=>confirmed},document:doc,
 URL, Number, setInterval(){}, console};
vm.createContext(context);
vm.runInContext(fs.readFileSync(process.argv[2],'utf8'),context);
const el = id=>doc.getElementById(id);
const t = {title:'<img onerror=bad()>',instructions:'Sign in below',finish:'Finish',retry:'Retry',
 starting:'Starting',sign_in:'Sign in',verifying:'Verifying',error:'Failed',unavailable:'Docker required',
 viewer_closed:'Reconnect',logout:'Log out',logout_help:'Preserve settings',logout_confirm:'Confirm',
 logout_failed:'Logout failed',action_failed:'Request failed',renewal_ready:'Renewing',renewal_retry:'Retrying',
 helper_title:'Use desktop helper',helper_help:'<img onerror=bad()>',helper_access_help:'First helper, ten minutes',
 helper_waiting:'Waiting for helper',helper_connected:'Connected',helper_verifying:'Verifying helper',
 helper_error:'Helper failed',helper_expired:'Helper access expired'};
const status = (phase, logged=false, attempt=1)=>({logged_in:logged,session:{state:logged?'ready':'waiting'},
 browser:{state:phase,attempt,error:null},renewal_available:logged,
 helper:{state:'disabled',attempt:0,expires_at:null,error:null}});
const helperStatus=(phase,attempt=1,logged=false)=>({...status('idle',logged),
 helper:{state:phase,attempt,expires_at:1600,error:null}});
let reply = {ok:true,json:async()=>status('sign_in')};
let fetcher = async(url,options={})=>{calls.push({url,options});return reply;};
const calls = [];
const viewers = [];
class RFB { constructor(element,url){this.url=url;this.events={};viewers.push(this);}
 addEventListener(event,cb){this.events[event]=cb;} disconnect(){this.disconnected=true;this.events.disconnect?.();}}
const panel = new context.window.BrowserLoginPanel(doc,(...args)=>fetcher(...args),()=>t,async()=>RFB,()=>now);
const tick=async()=>{await Promise.resolve();await Promise.resolve();};
"""


def run_panel(body):
    result = subprocess.run([NODE, "-", str(SCRIPT)], input=HARNESS + "\n(async()=>{\n" + body +
        "\n})().catch(e=>{console.error(e);process.exit(1)});", capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


@pytest.mark.skipif(NODE is None, reason="Node required")
def test_login_shows_vnc_and_returns_only_after_verification_cleanup():
    run_panel(r"""
assert.equal(el('dashboard').hidden,true);
assert.equal(el('dashboard-access-controls').parentElement,el('twitch-login-access'));
assert.equal(el('twitch-login-title').textContent,t.title);
panel.updateStatus(status('sign_in'));await tick();
assert.equal(el('twitch-vnc').hidden,false);
assert.equal(viewers[0].url,'wss://tdm.test/api/session/vnc');
assert.equal(el('twitch-login-finish').hidden,false);
panel.updateStatus(status('verifying',true));await tick();
assert.equal(el('dashboard').hidden,true);
assert.equal(viewers[0].disconnected,true);
panel.updateStatus(status('idle',true));await tick();
assert.equal(el('dashboard').hidden,false);
assert.equal(el('twitch-login-screen').hidden,true);
assert.equal(el('dashboard-access-controls').parentElement,el('dashboard-access-slot'));
""")


@pytest.mark.skipif(NODE is None, reason="Node required")
def test_logout_confirm_and_errors_do_not_replay_action():
    run_panel(r"""
panel.updateStatus(status('idle',true));confirmed=false;
await panel.logout();assert.equal(calls.length,0);
confirmed=true;reply={ok:false};
await panel.logout();
assert.equal(calls.filter(c=>c.options.method==='POST').length,1);
assert.equal(calls[0].url,'/api/session/logout');
assert.equal(calls[0].options.headers['X-TDM-Request'],'1');
assert.equal(el('twitch-logout-result').textContent,'Logout failed');
assert.equal(el('twitch-logout').disabled,false);
""")


@pytest.mark.skipif(NODE is None, reason="Node required")
def test_stale_status_cannot_restore_dashboard_after_login_event():
    run_panel(r"""
let resolve;fetcher=()=>new Promise(r=>resolve=r);
const pending=panel.load();
panel.updateStatus(status('sign_in',false,2));await tick();
resolve({ok:true,json:async()=>status('idle',true,1)});await pending;
assert.equal(el('dashboard').hidden,true);
assert.equal(panel.data.browser.attempt,2);
viewers[0].events.disconnect();
assert.equal(el('twitch-login-retry').hidden,false);
""")


@pytest.mark.skipif(NODE is None, reason="Node required")
def test_url_only_helper_enable_connect_verify_and_receipt_expiry():
    run_panel(r"""
panel.updateStatus(status('sign_in'));await tick();
assert.equal(calls.length,0,'helper mode is never enabled automatically');
assert.equal(el('twitch-helper-help').textContent,t.helper_help);
let server=helperStatus('waiting');
fetcher=async(url,options={})=>{
 calls.push({url,options});return {ok:true,json:async()=>server};
};
await panel.action('helper/enable');await tick();
assert.equal(calls[0].url,'/api/session/helper/enable');
assert.equal(calls[0].options.headers['X-TDM-Request'],'1');
assert.equal(viewers[0].disconnected,true);
assert.equal(el('twitch-vnc').hidden,true);
assert.equal(el('twitch-helper-panel').open,true);
assert.equal(el('twitch-helper-url').value,'https://tdm.test');
assert.equal(el('twitch-helper-connection').hidden,false);
assert.equal(el('twitch-helper-access-help').textContent,'First helper, ten minutes');
assert.equal(calls[0].options.body,undefined,'enable does not require an extra code or password');
panel.updateStatus(helperStatus('connected'));
assert.equal(el('twitch-helper-connection').hidden,true);
assert.equal(el('twitch-helper-status').textContent,'Connected');
panel.updateStatus(helperStatus('verifying',1,true));
assert.equal(el('dashboard').hidden,true);
panel.updateStatus(helperStatus('complete',1,true));
assert.equal(el('dashboard').hidden,false);
panel.updateStatus({...status('idle',true),helper:{state:'disabled',attempt:2,expires_at:null,error:null}});
assert.equal(el('dashboard').hidden,false,'receipt expiry must not reopen login');
""")


@pytest.mark.skipif(NODE is None, reason="Node required")
@pytest.mark.parametrize("phase", ["connected", "verifying", "complete", "error", "disabled"])
def test_late_enable_response_never_overwrites_new_helper_state(phase):
    run_panel(r"""
panel.updateStatus(status('sign_in'));await tick();
let resolve;
const pendingReply=new Promise(r=>resolve=r);
const phase=PHASE;
const newer=helperStatus(phase,phase==='disabled'?2:1,phase==='complete');
fetcher=async(url,options={})=>options.method==='POST'?pendingReply:{ok:true,json:async()=>newer};
const pending=panel.action('helper/enable');
panel.updateStatus(newer);
resolve({ok:true,json:async()=>helperStatus('waiting')});
await pending;
assert.equal(panel.data.helper.state,phase);
assert.equal(el('twitch-helper-connection').hidden,true);
""".replace("PHASE", json.dumps(phase)))


@pytest.mark.skipif(NODE is None, reason="Node required")
def test_waiting_broadcast_before_enable_response_preserves_current_attempt():
    run_panel(r"""
panel.updateStatus(status('sign_in'));await tick();
let resolve;
const pendingReply=new Promise(r=>resolve=r);
const waiting=helperStatus('waiting',3);
fetcher=async(url,options={})=>options.method==='POST'?pendingReply:{ok:true,json:async()=>waiting};
const pending=panel.action('helper/enable');
panel.updateStatus(waiting);
resolve({ok:true,json:async()=>waiting});
await pending;
assert.equal(el('twitch-helper-connection').hidden,false);
assert.equal(panel.data.helper.attempt,3);
""")


@pytest.mark.skipif(NODE is None, reason="Node required")
def test_helper_cancel_waits_for_server_and_resumes_embedded_viewer():
    run_panel(r"""
const waiting=helperStatus('waiting');
panel.updateStatus(waiting);
let resolve;
const pendingReply=new Promise(r=>resolve=r);
const cancelled={...status('sign_in',false,2),helper:{state:'disabled',attempt:2,expires_at:null,error:null}};
fetcher=async(url,options={})=>{
 calls.push({url,options});return options.method==='POST'?pendingReply:{ok:true,json:async()=>cancelled};
};
const pending=panel.action('helper/cancel');
assert.equal(el('twitch-helper-cancel').disabled,true);
assert.equal(el('twitch-vnc').hidden,true,'embedded browser must wait for cancellation');
resolve({ok:true,json:async()=>cancelled});await pending;await tick();
assert.equal(calls[0].url,'/api/session/helper/cancel');
assert.equal(el('twitch-helper-connection').hidden,true);
assert.equal(el('twitch-vnc').hidden,false);
assert.equal(el('twitch-helper-enable').hidden,false);
""")


@pytest.mark.skipif(NODE is None, reason="Node required")
def test_expired_access_hides_connection_instructions_without_replaying_enable():
    run_panel(r"""
const waiting=helperStatus('waiting');
panel.updateStatus(waiting);
now=1601;panel.updateStatus(waiting);
assert.equal(el('twitch-helper-status').textContent,'Helper access expired');
assert.equal(el('twitch-helper-connection').hidden,true);
assert.equal(calls.length,0);
""")


@pytest.mark.skipif(NODE is None, reason="Node required")
def test_missing_and_malformed_helper_status_disable_enable_and_show_errors():
    run_panel(r"""
const waiting=helperStatus('waiting');
panel.updateStatus(waiting);
panel.updateStatus({...waiting,helper:{...waiting.helper,error:'<img onerror=bad()>'}});
assert.equal(el('twitch-login-status').textContent,'Request failed');
assert.equal(el('twitch-helper-enable').disabled,true);
assert.equal(panel.data.helper.error,null,'unvalidated status is never retained');
const missing=status('sign_in');delete missing.helper;panel.updateStatus(missing);
assert.equal(el('twitch-helper-enable').disabled,true);
""")


@pytest.mark.skipif(NODE is None, reason="Node required")
def test_failed_helper_enable_is_visible_and_never_replays_post():
    run_panel(r"""
panel.updateStatus(status('sign_in'));await tick();
fetcher=async(url,options={})=>{
 calls.push({url,options});return options.method==='POST'?{ok:false}:{ok:true,json:async()=>status('sign_in')};
};
await panel.action('helper/enable');
assert.equal(calls.filter(c=>c.options.method==='POST').length,1);
assert.equal(el('twitch-login-error').textContent,'Request failed');
assert.equal(el('twitch-helper-enable').disabled,false);
assert.equal(el('twitch-helper-connection').hidden,true);
""")
