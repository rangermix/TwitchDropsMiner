"""Native noVNC UI policy, literal clipboard handling, and bounded template reads."""

from pathlib import Path

import pytest
from fastapi import HTTPException

from src.web.session_viewer import SessionViewer


def test_viewer_replaces_upstream_connection_configuration(tmp_path):
    (tmp_path / "vnc.html").write_text('<html><head><script type="module">arbitrary upstream config</script></head></html>')
    response = SessionViewer(tmp_path).page()
    body = response.body.decode()
    assert 'base href="/static/novnc-login-v2/"' in body
    assert 'src="/static/session-viewer.js?v=2"' in body
    assert 'arbitrary' not in body
    assert response.headers['cache-control'] == 'no-store'
    (tmp_path / "vnc.html").write_text('x' * 262145)
    with pytest.raises(HTTPException):
        SessionViewer(tmp_path).page()


def test_native_viewer_has_fixed_destination_and_no_clipboard_logging():
    script = Path('web/static/session-viewer.js').read_text()
    assert "path: 'api/session/vnc'" in script and 'reconnect: false' in script
    assert 'location.hostname' in script and "logging: 'none'" in script
    assert '65536' in script and "classList.contains('noVNC_open')" in script
    assert 'Log.' not in script and 'console.' not in script
    adapter = Path('web/static/browser-login.js').read_text()
    assert 'event.source !== this.frame.contentWindow' in adapter
    assert 'event.origin !== window.location.origin' in adapter
    assert "this.frame.src = 'about:blank'" in adapter


def test_native_clipboard_behavior_in_node():
    import subprocess

    from tests.javascript_helpers import NODE
    if NODE is None:
        pytest.skip('Node required')
    script = Path('web/static/session-viewer.js').read_text().split('\n', 1)[1]
    harness = r"""
const assert=require('node:assert/strict');
let open=false;const input={value:''};const sent=[];
const UI={connected:true,rfb:{clipboardPasteFrom:text=>sent.push(text)},
 closeClipboardPanel(){open=false},connectFinished(){},disconnectFinished(){},securityFailed(){},
 openControlbar(){},async start(options){assert.equal(options.settings.mandatory.reconnect,false)}};
const document={getElementById:id=>id==='noVNC_clipboard_text'?input:{classList:{contains:()=>open}}};
const location={hostname:'tdm.test',port:'',protocol:'https:',origin:'https://tdm.test'};
const parent={postMessage(){}};const window={addEventListener(){}};
(async()=>{
"""
    checks = r"""
UI.clipboardReceive({detail:{text:'private'}});assert.equal(input.value,'');
open=true;UI.clipboardReceive({detail:{text:'literal'}});assert.equal(input.value,'literal');
UI.clipboardSend();assert.deepEqual(sent,['literal']);assert.equal(input.value,'');
input.value='x'.repeat(65537);UI.clipboardSend();assert.equal(sent.length,1);assert.equal(input.value,'');
input.value='private';UI.closeClipboardPanel();assert.equal(input.value,'');
input.value='private';UI.disconnectFinished();assert.equal(input.value,'');
})().catch(e=>{console.error(e);process.exit(1)});
"""
    result = subprocess.run([NODE, '-'], input=harness + script + checks, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
