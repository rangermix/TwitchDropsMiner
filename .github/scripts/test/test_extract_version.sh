#!/usr/bin/env bash
set -euo pipefail

TEST_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SCRIPT_DIR="$(dirname "$TEST_DIR")"
SCRIPT="$SCRIPT_DIR/extract_version.sh"
TEMP_DIR=$(mktemp -d)
FIXTURE_DIR="$TEMP_DIR/repository"
trap 'rm -rf "$TEMP_DIR"' EXIT

reset_fixture() {
    rm -rf "$FIXTURE_DIR"
    mkdir -p "$FIXTURE_DIR/src"
    printf '__version__ = "1.2.5"\n' > "$FIXTURE_DIR/src/version.py"
    printf '[project]\nname = "twitch-drops-miner"\nversion = "1.2.5"\n' \
        > "$FIXTURE_DIR/pyproject.toml"
    printf 'version = 1\n\n[[package]]\nname = "twitch-drops-miner"\nversion = "1.2.5"\nsource = { editable = "." }\n' \
        > "$FIXTURE_DIR/uv.lock"
}

expect_success() {
    local description="$1"
    shift
    if (cd "$FIXTURE_DIR" && bash "$SCRIPT" "$@" >/dev/null); then
        echo "PASS: $description"
    else
        echo "FAIL: $description" >&2
        exit 1
    fi
}

expect_failure() {
    local description="$1"
    shift
    if (cd "$FIXTURE_DIR" && bash "$SCRIPT" "$@" >/dev/null 2>&1); then
        echo "FAIL: $description unexpectedly passed" >&2
        exit 1
    else
        echo "PASS: $description"
    fi
}

reset_fixture
expect_success "matching project files"
expect_success "matching release branch" "release/1.2.5"

reset_fixture
sed -i 's/1.2.5/1.2.4/' "$FIXTURE_DIR/src/version.py"
expect_failure "src/version.py mismatch"

reset_fixture
sed -i 's/1.2.5/1.2.4/' "$FIXTURE_DIR/pyproject.toml"
expect_failure "pyproject.toml mismatch"

reset_fixture
sed -i 's/1.2.5/1.2.4/' "$FIXTURE_DIR/uv.lock"
expect_failure "uv.lock mismatch"

reset_fixture
rm "$FIXTURE_DIR/uv.lock"
expect_failure "missing uv.lock"

reset_fixture
expect_failure "release branch mismatch" "release/1.2.6"

reset_fixture
OUTPUT_FILE="$TEMP_DIR/github-output.txt"
(cd "$FIXTURE_DIR" && GITHUB_OUTPUT="$OUTPUT_FILE" bash "$SCRIPT" >/dev/null)
grep -qx 'version=1.2.5' "$OUTPUT_FILE"
grep -qx 'is_prerelease=false' "$OUTPUT_FILE"
echo "PASS: GitHub Actions outputs"

echo "All extract_version.sh tests passed."
