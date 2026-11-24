"""Compact account labels retain their complete, current translated text."""

import json
import subprocess
from pathlib import Path

import pytest

from tests.javascript_helpers import APP_JS, NODE, extract_javascript_function


@pytest.mark.skipif(NODE is None, reason="Node required for DOM behavior tests")
def test_account_tooltip_preserves_full_translation_and_updates_after_logout():
    root = Path(__file__).resolve().parents[1]
    locales = [json.loads(path.read_text()) for path in (root / "lang").glob("*.json")]
    function = extract_javascript_function(APP_JS.read_text(), "updateLoginStatus")
    script = r"""
const assert = require('node:assert/strict');
const state = {};
const status = {textContent:'', title:'', style:{}};
const document = {getElementById(id) { return id === 'login-status' ? status : null; }};
""" + function + "\nconst locales = " + json.dumps(locales) + r""";
for (const locale of locales) {
    state.translations = locale;
    updateLoginStatus({user_id:123456789});
    assert.ok(status.textContent.includes('123456789'));
    assert.ok(status.textContent.includes(locale.gui.login.user_id_label));
    assert.equal(status.title,status.textContent,locale.language_name);
    updateLoginStatus({});
    assert.equal(status.title,locale.login.status.required);
    assert.ok(!status.title.includes('123456789'));
}
"""
    result = subprocess.run(
        [NODE, "-"], input=script, capture_output=True, text=True, encoding="utf-8"
    )
    assert result.returncode == 0, result.stderr


def test_header_and_login_share_the_drop_logo():
    root = Path(__file__).resolve().parents[1] / "web"
    logo = '<img class="brand-icon" src="/static/logo.svg?v=__APP_VERSION__" alt="" width="40" height="40">'
    for page in ("index.html", "login.html"):
        html = (root / page).read_text(encoding="utf-8")
        assert html.count(logo) == 1, page
        assert '<link rel="icon" type="image/png" href="/static/favicon.png?v=__APP_VERSION__">' in html, page
    # The logo is public on the login page; keep it a passive image.
    svg = (root / "static" / "logo.svg").read_text(encoding="utf-8").lower()
    assert svg.startswith("<svg ")
    for active in ("<script", "foreignobject", "href", " on"):
        assert active not in svg, active
