"""Check the Settings sub-tabs cover every section and switch exclusively."""

import re
import subprocess
from pathlib import Path

import pytest

from tests.javascript_helpers import APP_JS, NODE, extract_javascript_function


INDEX_HTML = Path(__file__).resolve().parents[1] / "web" / "index.html"


def _settings_markup() -> str:
    html = INDEX_HTML.read_text(encoding="utf-8")
    return html[html.index('<div id="settings-tab"') : html.index("<!-- Help Tab -->")]


def test_every_settings_section_sits_in_exactly_one_panel_with_a_tab():
    markup = _settings_markup()
    tabs = re.findall(r'data-settings-tab="([a-z]+)"', markup)
    panels = re.findall(r'id="settings-panel-([a-z]+)"', markup)
    assert tabs == panels == ["general", "mining", "games", "notifications", "account"]
    chunks = markup.split('class="settings-panel"')
    assert "settings-section" not in chunks[0], "section outside any panel"
    assert all("settings-section" in chunk for chunk in chunks[1:])
    # Mining benefits used to live inside the Telegram section.
    telegram = re.search(r'<section[^>]*>(?:(?!</section>).)*telegram-bot-token.*?</section>', markup, re.S)
    assert telegram and "mining-benefit-item" not in telegram.group(0)


@pytest.mark.skipif(NODE is None, reason="Node.js is required for frontend tests")
def test_switch_settings_tab_shows_only_the_selected_panel():
    source = APP_JS.read_text(encoding="utf-8")
    script = extract_javascript_function(source, "switchSettingsTab") + r"""
const assert = require('node:assert/strict');
const names = ['general', 'mining', 'games'];
const buttons = names.map(name => ({
    dataset: { settingsTab: name }, attrs: {}, classes: new Set(),
    classList: { toggle(c, on) { on ? this.owner.classes.add(c) : this.owner.classes.delete(c); } },
    setAttribute(k, v) { this.attrs[k] = v; }
}));
buttons.forEach(b => { b.classList.owner = b; });
const panels = names.map(name => ({ id: `settings-panel-${name}`, hidden: name !== 'general' }));
const document = {
    querySelectorAll: sel => sel === '[data-settings-tab]' ? buttons : panels
};
switchSettingsTab('mining');
assert.deepEqual(panels.map(p => p.hidden), [true, false, true]);
assert.deepEqual(buttons.map(b => b.attrs['aria-selected']), ['false', 'true', 'false']);
assert.deepEqual(buttons.map(b => b.classes.has('active')), [false, true, false]);
"""
    result = subprocess.run([NODE, "-"], input=script, text=True, capture_output=True)
    assert result.returncode == 0, result.stderr
