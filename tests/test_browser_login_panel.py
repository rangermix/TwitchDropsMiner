"""Login/verification/dashboard transitions and safe translated controls in Node."""

import json
import subprocess
from pathlib import Path

import pytest

from src.i18n.translator import GUIBrowserLogin
from tests.javascript_helpers import NODE


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "web/static/browser-login.js"


def test_login_screen_and_bottom_logout_replace_helper():
    html = (ROOT / "web/index.html").read_text(encoding="utf-8")
    assert 'id="twitch-vnc"' in html and 'id="dashboard"' in html
    assert 'id="twitch-login-finish"' in html
    assert html.index('id="twitch-logout"') > html.index('id="settings-actions-header"')
    assert "helper-login.js" not in html and "allow-helper-connection" not in html
    assert "/static/browser-login.js?v=__APP_VERSION__" in html


def test_locales_match_new_schema_without_helper_downloads():
    for path in (ROOT / "lang").glob("*.json"):
        locale = json.loads(path.read_text(encoding="utf-8"))
        assert "helper" not in locale and "helper_login" not in locale["gui"]
        assert set(locale["gui"]["browser_login"]) == set(GUIBrowserLogin.__annotations__), path


HARNESS = r"""
const assert = require('node:assert/strict');
const vm = require('node:vm');
const fs = require('node:fs');
const elements = new Map();
const doc = {getElementById(id) {
    if (!elements.has(id)) elements.set(id, {hidden:false, disabled:false, textContent:'',
        listeners:{}, addEventListener(event,cb){this.listeners[event]=cb;}, replaceChildren(){},
        appendChild(child){child.parentElement=this;}});
    return elements.get(id);
},addEventListener(){}};
let confirmed = true;
const context = {window:{location:{href:'https://tdm.test/'},confirm:()=>confirmed},document:doc,
 URL, Number, setInterval(){}, console};
vm.createContext(context);
vm.runInContext(fs.readFileSync(process.argv[2],'utf8'),context);
const el = id=>doc.getElementById(id);
const t = {title:'<img onerror=bad()>',instructions:'Sign in below',finish:'Finish',retry:'Retry',
 starting:'Starting',sign_in:'Sign in',verifying:'Verifying',error:'Failed',unavailable:'Docker required',
 viewer_closed:'Reconnect',logout:'Log out',logout_help:'Preserve settings',logout_confirm:'Confirm',
 logout_failed:'Logout failed',action_failed:'Request failed',renewal_ready:'Renewing',renewal_retry:'Retrying'};
const status = (phase, logged=false, attempt=1)=>({logged_in:logged,session:{state:logged?'ready':'waiting'},
 browser:{state:phase,attempt,error:null},renewal_available:logged});
let reply = {ok:true,json:async()=>status('sign_in')};
let fetcher = async(url,options={})=>{calls.push({url,options});return reply;};
const calls = [];
const viewers = [];
class RFB { constructor(element,url){this.url=url;this.events={};viewers.push(this);}
 addEventListener(event,cb){this.events[event]=cb;} disconnect(){this.disconnected=true;this.events.disconnect?.();}}
const panel = new context.window.BrowserLoginPanel(doc,(...args)=>fetcher(...args),()=>t,async()=>RFB);
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
