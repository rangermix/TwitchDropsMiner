from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

import pytest

from tests.javascript_helpers import APP_JS, NODE, extract_javascript_function


PROJECT_ROOT = Path(__file__).resolve().parents[1]
INDEX_HTML = PROJECT_ROOT / "web" / "index.html"


def _extract_async_function(source: str, name: str) -> str:
    function_source = extract_javascript_function(source, name)
    assert source[source.index(f"function {name}(") - 6 :].startswith("async ")
    return f"async {function_source}"


def test_clear_cache_button_is_next_to_reload_and_explains_preserved_state():
    html = INDEX_HTML.read_text(encoding="utf-8")
    actions = re.search(
        r'<div\s+class="settings-actions">(?P<body>.*?)</div>', html, re.DOTALL
    )

    assert actions is not None
    assert re.findall(r'<button\b[^>]*\bid="([^"]+)"', actions.group("body")) == [
        "reload-btn",
        "clear-cache-btn",
    ]
    assert re.search(
        r'<button\b[^>]*\bid="clear-cache-btn"[^>]*\btype="button"',
        actions.group("body"),
    )

    help_text = re.search(
        r'<p\b[^>]*\bid="clear-cache-help"[^>]*>(?P<body>.*?)</p>',
        html,
        re.DOTALL,
    )
    assert help_text is not None
    normalized_help = " ".join(help_text.group("body").split()).lower()
    assert "login and settings" in normalized_help
    assert "reloads from twitch" in normalized_help


@pytest.mark.skipif(NODE is None, reason="Node.js is required for frontend tests")
def test_clear_all_cache_posts_and_restores_button_after_success_or_failure():
    assert NODE is not None
    app_source = APP_JS.read_text(encoding="utf-8")
    request_source = _extract_async_function(app_source, "requestCampaignRefresh")
    clear_source = _extract_async_function(app_source, "clearAllCache")

    script = f"""
{request_source}
{clear_source}

const button = {{ disabled: false }};
const calls = [];
const errors = [];
let response = {{ ok: true, status: 200 }};

global.document = {{
    getElementById(id) {{
        if (id !== 'clear-cache-btn') throw new Error(`unexpected element ${{id}}`);
        return button;
    }},
}};
global.fetch = async (url, options) => {{
    calls.push({{ url, options, disabledDuringRequest: button.disabled }});
    return response;
}};
console.error = (...args) => errors.push(args.map(String).join(' '));

(async () => {{
    await clearAllCache();
    const enabledAfterSuccess = !button.disabled;
    response = {{ ok: false, status: 409 }};
    await clearAllCache();
    process.stdout.write(JSON.stringify({{
        calls,
        enabledAfterSuccess,
        enabledAfterFailure: !button.disabled,
        errors,
    }}));
}})().catch(error => {{
    process.stderr.write(String(error));
    process.exit(1);
}});
"""
    completed = subprocess.run(
        [NODE, "-"],
        check=True,
        capture_output=True,
        input=script,
        text=True,
    )
    result = json.loads(completed.stdout)

    assert result["calls"] == [
        {
            "url": "/api/cache/clear",
            "options": {"method": "POST"},
            "disabledDuringRequest": True,
        },
        {
            "url": "/api/cache/clear",
            "options": {"method": "POST"},
            "disabledDuringRequest": True,
        },
    ]
    assert result["enabledAfterSuccess"] is True
    assert result["enabledAfterFailure"] is True
    assert len(result["errors"]) == 1
    assert "Failed to clear cache" in result["errors"][0]
    assert "HTTP 409" in result["errors"][0]
