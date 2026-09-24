"""Interactive browser prompt remains safe and available after dashboard reconnect."""

import subprocess
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.web.managers.login import LoginFormManager
from tests.javascript_helpers import APP_JS, NODE, extract_javascript_function


@pytest.mark.asyncio
async def test_browser_prompt_reconnect_and_cleanup():
    broadcaster = MagicMock(emit=AsyncMock())
    login = LoginFormManager(broadcaster, MagicMock())
    await login.browser_pending("http://localhost:7900/vnc.html")
    assert login.get_status()["browser_url"] == "http://localhost:7900/vnc.html"
    assert set(login.get_status()) == {"status", "user_id", "browser_url"}
    await login.browser_pending(None)
    assert login.get_status()["browser_url"] is None
    assert broadcaster.emit.await_args.args[1]["browser_url"] is None


@pytest.mark.asyncio
async def test_desktop_browser_prompt_survives_reconnect_without_a_fake_viewer_url():
    broadcaster = MagicMock(emit=AsyncMock())
    login = LoginFormManager(broadcaster, MagicMock())
    await login.browser_pending(None, desktop=True)
    assert login.get_status()["browser_desktop"] is True
    assert login.get_status()["browser_url"] is None
    await login.browser_pending(None)
    assert not login.get_status().get("browser_desktop")


@pytest.mark.skipif(NODE is None, reason="Node required for DOM behavior tests")
def test_viewer_link_rejects_scripts_and_removes_stale_login():
    function = extract_javascript_function(APP_JS.read_text(), "showBrowserLogin")
    script = (
        r"""
const assert = require('node:assert/strict');
const elements = new Map();
const document = {getElementById(id) {
    if (!elements.has(id)) elements.set(id, {style: {}, hidden: true, textContent: '',
        removeAttribute(name) { delete this[name]; }});
    return elements.get(id);
}};
const state = {translations: {gui: {login: {browser_prompt: '<img onerror=bad()>', browser_open: 'Open', browser_desktop_prompt:'Use the TDM Chrome window'}}}};
"""
        + function
        + r"""
showBrowserLogin('http://localhost:7900/vnc.html');
assert.equal(document.getElementById('browser-login').hidden, false);
assert.equal(document.getElementById('browser-login-prompt').textContent, '<img onerror=bad()>');
assert.equal(document.getElementById('browser-login-link').href, 'http://localhost:7900/vnc.html');
for (const url of [null, 'javascript:alert(1)', 'data:text/html,test', 'https://user:pass@example.test', '//example.test']) {
    showBrowserLogin(url);
    assert.equal(document.getElementById('browser-login').hidden, true);
    assert.equal(document.getElementById('browser-login-link').href, undefined);
}
showBrowserLogin(null, true);
assert.equal(document.getElementById('browser-login').hidden, false);
assert.equal(document.getElementById('browser-login-link').hidden, true);
assert.equal(document.getElementById('browser-login-link').href, undefined);
assert.equal(document.getElementById('browser-login-prompt').textContent, 'Use the TDM Chrome window');
showBrowserLogin(null);
assert.equal(document.getElementById('browser-login').hidden, true);
showBrowserLogin('http://localhost:7900/vnc.html');
assert.equal(document.getElementById('browser-login-link').hidden, false);
"""
    )
    subprocess.run([NODE, "-e", script], check=True, capture_output=True, text=True)
