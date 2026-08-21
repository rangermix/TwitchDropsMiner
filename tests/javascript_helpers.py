"""Shared helpers for executing isolated frontend functions in Node.js tests."""

import shutil
from pathlib import Path


APP_JS = Path(__file__).resolve().parents[1] / "web" / "static" / "app.js"
NODE = shutil.which("node")


def extract_javascript_function(source: str, name: str) -> str:
    """Extract a top-level JavaScript function declaration from source text."""
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
