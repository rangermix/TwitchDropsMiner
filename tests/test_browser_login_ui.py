"""Login status updates leave embedded/optional-helper controls to the login panel."""

import asyncio
import subprocess
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.web.managers.login import LoginFormManager
from tests.javascript_helpers import APP_JS, NODE, extract_javascript_function


@pytest.mark.asyncio
async def test_pending_container_browser_status_survives_reconnect_without_browser_or_device_data():
    broadcaster = MagicMock(emit=AsyncMock())
    login = LoginFormManager(broadcaster, MagicMock())
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


@pytest.mark.asyncio
async def test_login_status_publishes_twitch_avatar_url():
    broadcaster = MagicMock(emit=AsyncMock())
    manager = MagicMock()
    manager._twitch._gql_client.request = AsyncMock(
        return_value={"data": {"currentUser": {"id": "7", "profileImageURL": "https://static-cdn.jtvnw.net/avatar.png"}}}
    )
    login = LoginFormManager(broadcaster, manager)
    login.update("Logged in", 7)
    for _ in range(5):
        await asyncio.sleep(0)
    assert login.get_status()["avatar_url"] == "https://static-cdn.jtvnw.net/avatar.png"


@pytest.mark.asyncio
async def test_avatar_requires_https_and_resets_on_account_change():
    broadcaster = MagicMock(emit=AsyncMock())
    manager = MagicMock()
    manager._twitch._gql_client.request = AsyncMock(
        return_value={"data": {"currentUser": {"id": "7", "profileImageURL": "http://insecure.example/avatar.png"}}}
    )
    login = LoginFormManager(broadcaster, manager)
    login.update("Logged in", 7)
    for _ in range(5):
        await asyncio.sleep(0)
    assert "avatar_url" not in login.get_status()
    manager._twitch._gql_client.request.assert_awaited_once()
    manager._twitch._gql_client.request.reset_mock()
    login.update("Logged in", 7)
    for _ in range(5):
        await asyncio.sleep(0)
    manager._twitch._gql_client.request.assert_not_awaited()
    assert "avatar_url" not in login.get_status()
    login.update("Logged out", None)
    assert "avatar_url" not in login.get_status()


@pytest.mark.asyncio
@pytest.mark.parametrize("reply", [
    {"data": {"currentUser": {"id": "8", "profileImageURL": "https://cdn.example/avatar.png"}}},
    {"data": {"currentUser": {"profileImageURL": "https://cdn.example/avatar.png"}}},
    {"data": {"currentUser": None}},
    {"data": []},
])
async def test_avatar_rejects_wrong_account_or_malformed_response(reply):
    manager = MagicMock()
    manager._twitch._gql_client.request = AsyncMock(return_value=reply)
    login = LoginFormManager(MagicMock(emit=AsyncMock()), manager)
    login.update("Logged in", 7)
    for _ in range(5):
        await asyncio.sleep(0)
    assert "avatar_url" not in login.get_status()


@pytest.mark.asyncio
async def test_avatar_failure_is_optional_and_does_not_repeat_or_log_response(caplog):
    manager = MagicMock()
    manager._twitch._gql_client.request = AsyncMock(side_effect=ValueError("private-provider-response"))
    login = LoginFormManager(MagicMock(emit=AsyncMock()), manager)
    with caplog.at_level("DEBUG", logger="TwitchDrops"):
        for _ in range(3):
            login.update("Logged in", 7)
            for _ in range(5):
                await asyncio.sleep(0)
    manager._twitch._gql_client.request.assert_awaited_once()
    assert "private-provider-response" not in caplog.text
    assert login.get_status() == {"status": "Logged in", "user_id": 7}


@pytest.mark.asyncio
async def test_avatar_timeout_leaves_login_usable_and_does_not_retry(monkeypatch):
    timeouts = []

    async def timeout(awaitable, seconds):
        timeouts.append(seconds)
        awaitable.close()
        raise asyncio.TimeoutError

    monkeypatch.setattr("src.web.managers.login.asyncio.wait_for", timeout)
    manager = MagicMock()
    manager._twitch._gql_client.request = AsyncMock()
    login = LoginFormManager(MagicMock(emit=AsyncMock()), manager)
    login.update("Logged in", 7)
    await asyncio.sleep(0)
    login.update("Logged in", 7)
    await asyncio.sleep(0)
    assert timeouts == [10]
    assert login.get_status() == {"status": "Logged in", "user_id": 7}


@pytest.mark.asyncio
async def test_delayed_old_account_avatar_cannot_overwrite_new_account():
    old_entered, release_old, new_done = asyncio.Event(), asyncio.Event(), asyncio.Event()
    calls = 0

    async def request(*args):
        nonlocal calls
        calls += 1
        if calls == 1:
            old_entered.set()
            try:
                await release_old.wait()
            except asyncio.CancelledError:
                await release_old.wait()
            return {"data": {"currentUser": {"id": "7", "profileImageURL": "https://cdn.example/old.png"}}}
        new_done.set()
        return {"data": {"currentUser": {"id": "8", "profileImageURL": "https://cdn.example/new.png"}}}

    manager = MagicMock()
    manager._twitch._gql_client.request = request
    login = LoginFormManager(MagicMock(emit=AsyncMock()), manager)
    login.update("Logged in", 7)
    await old_entered.wait()
    try:
        login.update("Logged in", 8)
        await new_done.wait()
        release_old.set()
        await asyncio.gather(*tuple(login._avatar_tasks))
        assert login.get_status()["avatar_url"] == "https://cdn.example/new.png"
    finally:
        release_old.set()
        await login.stop_avatar()


@pytest.mark.asyncio
async def test_avatar_is_cancelled_when_login_is_cleared():
    entered, cleaned = asyncio.Event(), asyncio.Event()
    tasks = []

    async def request(*args):
        tasks.append(asyncio.current_task())
        entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            cleaned.set()

    manager = MagicMock()
    manager._twitch._gql_client.request = request
    login = LoginFormManager(MagicMock(emit=AsyncMock()), manager)
    login.update("Logged in", 7)
    await entered.wait()
    try:
        login.update("Logged out", None)
        await asyncio.sleep(0)
        assert cleaned.is_set()
        assert tasks[0].done()
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


@pytest.mark.skipif(NODE is None, reason="Node required for DOM behavior tests")
def test_login_updates_never_revive_browser_or_device_code_controls():
    function = extract_javascript_function(APP_JS.read_text(), "updateLoginStatus")
    script = r"""
const assert = require('node:assert/strict');
const elements = new Map();
function makeElement() {
    const classes = new Set();
    return {style: {}, textContent: '', classes,
        classList: {toggle(name, force) { force ? classes.add(name) : classes.delete(name); }},
        setAttribute() {}, removeAttribute() {}};
}
const document = {getElementById(id) {
    if (!elements.has(id)) elements.set(id, makeElement());
    return elements.get(id);
}};
const state = {translations: {login: {status: {logged_in: 'Logged in', required: 'Login required', logged_out: 'Logged out'}},
    gui: {login: {user_id_label: 'User ID:'}}}};
let legacyPrompts = 0;
function showBrowserLogin() { legacyPrompts++; }
function showOAuthCode() { legacyPrompts++; }
""" + function + r"""
updateLoginStatus({user_id: null, import_pending: true, oauth_pending: {url: 'private-old-url', code: 'private-old-code'}});
assert.equal(legacyPrompts, 0, 'login updates must not revive retired credential or device-code controls');
assert.equal(document.getElementById('login-status').textContent, 'Login required');
updateLoginStatus({user_id: 42, status: 'Logged in'});
assert.equal(document.getElementById('login-status').textContent, 'Logged in (User ID: 42)');
assert.equal(document.getElementById('login-status').style.color, 'var(--success-color)');
"""
    result = subprocess.run([NODE, "-e", script], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


@pytest.mark.skipif(NODE is None, reason="Node required for DOM behavior tests")
def test_login_updates_apply_twitch_avatar_background():
    function = extract_javascript_function(APP_JS.read_text(), "updateLoginStatus")
    script = r"""
const assert = require('node:assert/strict');
const elements = new Map();
function makeElement() {
    const classes = new Set();
    return {style: {}, textContent: '', classes,
        classList: {toggle(name, force) { force ? classes.add(name) : classes.delete(name); }},
        setAttribute() {}, removeAttribute() {}};
}
const document = {getElementById(id) {
    if (!elements.has(id)) elements.set(id, makeElement());
    return elements.get(id);
}};
const state = {translations: {login: {status: {logged_in: 'Logged in', required: 'Login required', logged_out: 'Logged out'}},
    gui: {login: {user_id_label: 'User ID:'}}}};
""" + function + r"""
updateLoginStatus({user_id: 42, status: 'Logged in', avatar_url: 'https://static-cdn.jtvnw.net/avatar.png'});
const avatar = document.getElementById('user-avatar');
assert.equal(avatar.style.backgroundImage, 'url("https://static-cdn.jtvnw.net/avatar.png")');
assert.equal(avatar.textContent, '');
assert.ok(avatar.classes.has('logged-in'));
assert.ok(avatar.classes.has('avatar-image'));
updateLoginStatus({user_id: null, status: 'Logged out'});
assert.equal(avatar.style.backgroundImage, '');
assert.ok(!avatar.classes.has('avatar-image'));
"""
    result = subprocess.run([NODE, "-e", script], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
