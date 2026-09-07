import json
import subprocess

import pytest

from tests.javascript_helpers import APP_JS, NODE, extract_javascript_function


@pytest.mark.skipif(NODE is None, reason="Node.js is required for frontend tests")
def test_watching_channel_remains_visible_outside_game_filter():
    app_source = APP_JS.read_text(encoding="utf-8")
    function_source = extract_javascript_function(
        app_source, "channelMatchesGameFilter"
    )
    cases = [
        {
            "channel": {"game": "Game A", "watching": False},
            "games": ["Game A"],
            "expected": True,
        },
        {
            "channel": {"game": "Game B", "watching": False},
            "games": ["Game A"],
            "expected": False,
        },
        {
            "channel": {"game": "Game B", "watching": True},
            "games": ["Game A"],
            "expected": True,
        },
        {
            "channel": {"game": None, "watching": True},
            "games": ["Game A"],
            "expected": True,
        },
        {
            "channel": {"game": "Game B", "watching": False},
            "games": [],
            "expected": True,
        },
        {
            "channel": {"game": "Rust", "watching": False},
            "games": ["rust"],
            "expected": True,
        },
    ]

    script = f"""
{function_source}
const cases = {json.dumps(cases)};
const results = cases.map(testCase => channelMatchesGameFilter(
    testCase.channel,
    new Set(testCase.games.map(game => game.toLowerCase())),
));
process.stdout.write(JSON.stringify(results));
    """
    completed = subprocess.run(
        [NODE, "-"],
        check=True,
        capture_output=True,
        input=script,
        text=True,
    )

    assert json.loads(completed.stdout) == [case["expected"] for case in cases]
    render_source = extract_javascript_function(app_source, "renderChannels")
    assert "new Set(gamesToWatch.map(g => g.toLowerCase()))" in render_source
    assert "channelMatchesGameFilter(channel, gamesToWatchSet)" in render_source
