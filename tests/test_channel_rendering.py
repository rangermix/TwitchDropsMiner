import json
import shutil
import subprocess
from pathlib import Path

import pytest


APP_JS = Path(__file__).resolve().parents[1] / "web" / "static" / "app.js"
NODE = shutil.which("node")


def _extract_javascript_function(source: str, name: str) -> str:
    signature = f"function {name}("
    start = source.index(signature)
    brace_start = source.index("{", start)
    depth = 0

    for index in range(brace_start, len(source)):
        if source[index] == "{":
            depth += 1
        elif source[index] == "}":
            depth -= 1
            if depth == 0:
                return source[start : index + 1]

    raise AssertionError(f"Could not find the end of {name}()")


@pytest.mark.skipif(NODE is None, reason="Node.js is required for frontend tests")
def test_watching_channel_remains_visible_outside_game_filter():
    app_source = APP_JS.read_text(encoding="utf-8")
    function_source = _extract_javascript_function(
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
    ]

    script = f"""
{function_source}
const cases = {json.dumps(cases)};
const results = cases.map(testCase => channelMatchesGameFilter(
    testCase.channel,
    new Set(testCase.games),
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
    assert "channelMatchesGameFilter(channel, gamesToWatchSet)" in _extract_javascript_function(
        app_source, "renderChannels"
    )
