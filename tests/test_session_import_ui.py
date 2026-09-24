"""Rendered-state and submission behavior of the import panel."""

import subprocess
from pathlib import Path

import pytest

from tests.javascript_helpers import NODE


SCRIPT = Path(__file__).resolve().parents[1] / "web/static/session-import.js"


@pytest.mark.skipif(NODE is None, reason="Node required for DOM behavior tests")
def test_import_panel_auth_gate_upload_and_failure_are_safe():
    program = r"""
const assert = require('node:assert/strict');
const vm = require('node:vm');
const fs = require('node:fs');
const elements = new Map();
const doc = {getElementById(id) {
  if (!elements.has(id)) elements.set(id, {hidden:true, disabled:false, textContent:'', value:'', files:[], addEventListener(){}});
  return elements.get(id);
}, addEventListener(){}};
const context = {window:{}, document:doc, Date, setInterval(){}, console};
vm.createContext(context);
vm.runInContext(fs.readFileSync(process.argv[1], 'utf8'), context);
const t = {title:'<img onerror=bad()>', prompt:'Export locally', file:'File', button:'Import',
 auth_required:'Enable password', checking:'Checking', ready:'Valid until {expiry}',
 waiting:'Waiting', expired:'Expired', error:'Failed ({code})'};
let calls=[];
let reply={ok:true, json:async()=>({enabled:true, authentication_required:true})};
const fetcher=async(url,options={})=>{calls.push({url,options});return reply;};
(async()=>{
 const panel=new context.window.SessionImportPanel(doc, fetcher, ()=>t);
 await panel.load();
 assert.equal(doc.getElementById('session-import-button').disabled, true);
 assert.equal(doc.getElementById('session-import-status').textContent,'Enable password');
 assert.equal(doc.getElementById('session-import-title').textContent,t.title);
 reply={ok:true,json:async()=>({enabled:true,authentication_required:false,session:{state:'waiting'}})};
 await panel.load();
 assert.equal(doc.getElementById('session-import-button').disabled,false);
 const input=doc.getElementById('session-import-file');
 input.files=[{size:100,text:async()=>'{"headers":"private-test"}'}];
 input.value='chosen.json';
 reply={ok:false,json:async()=>({detail:'session_catalog'})};
 await panel.submit();
 const sent=calls.at(-1);
 assert.equal(sent.url,'/api/session/import');
 assert.equal(sent.options.headers['X-TDM-Request'],'1');
 assert.equal(sent.options.body,'{"headers":"private-test"}');
 assert.equal(input.value,'');
 assert.equal(doc.getElementById('session-import-status').textContent,'Failed (session_catalog)');
 assert.equal(doc.getElementById('session-import-button').disabled,false);
 input.files=[{size:65537,text:async()=>{throw Error('must not read')}}];
 const before=calls.length;
 await panel.submit();
 assert.equal(calls.length,before);
 assert.equal(doc.getElementById('session-import-status').textContent,'Failed (invalid_file)');
 reply={ok:true,json:async()=>({enabled:false,authentication_required:false})};
 await panel.load();
 assert.equal(doc.getElementById('session-import-panel').hidden,true);
})().catch(error=>{console.error(error);process.exit(1)});
"""
    subprocess.run([NODE, "-e", program, str(SCRIPT)], check=True, capture_output=True, text=True)
