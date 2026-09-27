"""The dashboard exposes helper login status without legacy login entry points."""

import asyncio
import subprocess
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.web.managers.login import LoginFormManager
from tests.javascript_helpers import APP_JS, NODE, extract_javascript_function


@pytest.mark.asyncio
async def test_pending_helper_status_survives_reconnect_without_browser_or_device_data():
    broadcaster = MagicMock(emit=AsyncMock())
    login = LoginFormManager(broadcaster, MagicMock())
    await login.browser_pending("http://localhost:7900/vnc.html", desktop=True)
    await login.import_pending(True)
    assert set(login.get_status()) == {"status", "user_id", "import_pending"}
    assert login.get_status()["import_pending"] is True
    assert broadcaster.emit.await_args.args[1] == login.get_status()
    await login.import_pending(False)
    assert set(login.get_status()) == {"status", "user_id"}


@pytest.mark.asyncio
async def test_success_clears_pending_status_in_broadcast_and_reconnect():
    broadcaster = MagicMock(emit=AsyncMock())
    login = LoginFormManager(broadcaster, MagicMock())
    await login.import_pending(True)
    login.update("Logged in", 42)
    await asyncio.sleep(0)
    assert login.get_status() == {"status": "Logged in", "user_id": 42}
    assert broadcaster.emit.await_args.args[1] == login.get_status()


@pytest.mark.skipif(NODE is None, reason="Node required for DOM behavior tests")
def test_login_updates_never_revive_browser_or_device_code_controls():
    function = extract_javascript_function(APP_JS.read_text(), "updateLoginStatus")
    script = r"""
const assert = require('node:assert/strict');
const elements = new Map();
const document = {getElementById(id) {
    if (!elements.has(id)) elements.set(id, {style: {}, textContent: '',
        setAttribute() {}, removeAttribute() {}});
    return elements.get(id);
}};
const state = {translations: {login: {status: {logged_in: 'Logged in', required: 'Login required', logged_out: 'Logged out'}},
    gui: {login: {user_id_label: 'User ID:'}}}};
let legacyPrompts = 0;
function showBrowserLogin() { legacyPrompts++; }
function showOAuthCode() { legacyPrompts++; }
""" + function + r"""
updateLoginStatus({user_id: null, import_pending: true, oauth_pending: {url: 'private-old-url', code: 'private-old-code'}});
assert.equal(legacyPrompts, 0, 'fresh login must use the helper only');
assert.equal(document.getElementById('login-status').textContent, 'Login required');
updateLoginStatus({user_id: 42, status: 'Logged in'});
assert.equal(document.getElementById('login-status').textContent, 'Logged in (User ID: 42)');
assert.equal(document.getElementById('login-status').style.color, 'var(--success-color)');
"""
    result = subprocess.run([NODE, "-e", script], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
