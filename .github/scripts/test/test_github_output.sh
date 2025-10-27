#!/usr/bin/env bash
set -euo pipefail

# Test script for validate_semver.sh GitHub Actions output functionality
# Usage: ./test_github_output.sh

# Color codes for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[0;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Get the directory of this test script
TEST_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SCRIPT_DIR="$(dirname "$TEST_DIR")"
SCRIPT="$SCRIPT_DIR/validate_semver.sh"

# Test counters
TESTS_RUN=0
TESTS_PASSED=0
TESTS_FAILED=0

# Create temp directory for test outputs
TEMP_DIR=$(mktemp -d)
trap 'rm -rf "$TEMP_DIR"' EXIT

# Test helper function
test_github_output() {
    local test_name="$1"
    local version="$2"
    local range="${3:-}"
    local expected_version="$4"
    local expected_is_prerelease="$5"
    local expected_range_satisfied="${6:-}"

    TESTS_RUN=$((TESTS_RUN + 1))

    # Create a temporary output file
    local output_file="$TEMP_DIR/github_output_${TESTS_RUN}.txt"
    export GITHUB_OUTPUT="$output_file"

    # Run the script
    local result=0
    if [ -n "$range" ]; then
        bash "$SCRIPT" "$version" "$range" &>/dev/null || result=$?
    else
        bash "$SCRIPT" "$version" &>/dev/null || result=$?
    fi

    # Check if script succeeded when it should
    if [ $result -ne 0 ]; then
        echo -e "${RED}✗${NC} $test_name (script failed with exit code $result)"
        TESTS_FAILED=$((TESTS_FAILED + 1))
        unset GITHUB_OUTPUT
        return
    fi

    # Parse the output file
    local actual_version=""
    local actual_is_prerelease=""
    local actual_range_satisfied=""

    if [ -f "$output_file" ]; then
        while IFS='=' read -r key value; do
            case "$key" in
                version)
                    actual_version="$value"
                    ;;
                is_prerelease)
                    actual_is_prerelease="$value"
                    ;;
                range_satisfied)
                    actual_range_satisfied="$value"
                    ;;
            esac
        done < "$output_file"
    fi

    # Validate outputs
    local test_passed=true
    local error_msg=""

    if [ "$actual_version" != "$expected_version" ]; then
        test_passed=false
        error_msg="${error_msg}version: expected '$expected_version', got '$actual_version'; "
    fi

    if [ "$actual_is_prerelease" != "$expected_is_prerelease" ]; then
        test_passed=false
        error_msg="${error_msg}is_prerelease: expected '$expected_is_prerelease', got '$actual_is_prerelease'; "
    fi

    if [ -n "$expected_range_satisfied" ] && [ "$actual_range_satisfied" != "$expected_range_satisfied" ]; then
        test_passed=false
        error_msg="${error_msg}range_satisfied: expected '$expected_range_satisfied', got '$actual_range_satisfied'; "
    fi

    # Report results
    if [ "$test_passed" = true ]; then
        echo -e "${GREEN}✓${NC} $test_name"
        TESTS_PASSED=$((TESTS_PASSED + 1))
    else
        echo -e "${RED}✗${NC} $test_name"
        echo -e "  ${RED}${error_msg}${NC}"
        TESTS_FAILED=$((TESTS_FAILED + 1))
    fi

    unset GITHUB_OUTPUT
}

# Print test section header
section() {
    echo ""
    echo -e "${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
    echo -e "${BLUE}$1${NC}"
    echo -e "${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
}

# Start tests
echo -e "${YELLOW}Testing GITHUB_OUTPUT functionality...${NC}"

# ============================================================================
# Test 1: Basic Output Without Range
# ============================================================================
section "Test 1: Basic Output Without Range"

test_github_output \
    "Stable version output" \
    "1.2.3" \
    "" \
    "1.2.3" \
    "false"

test_github_output \
    "Another stable version" \
    "2.5.8" \
    "" \
    "2.5.8" \
    "false"

test_github_output \
    "Zero version output" \
    "0.0.0" \
    "" \
    "0.0.0" \
    "false"

test_github_output \
    "Large version numbers" \
    "100.200.300" \
    "" \
    "100.200.300" \
    "false"

# ============================================================================
# Test 2: Pre-release Version Output
# ============================================================================
section "Test 2: Pre-release Version Output"

test_github_output \
    "Pre-release alpha" \
    "1.0.0-alpha" \
    "" \
    "1.0.0-alpha" \
    "true"

test_github_output \
    "Pre-release beta" \
    "2.0.0-beta.1" \
    "" \
    "2.0.0-beta.1" \
    "true"

test_github_output \
    "Pre-release rc" \
    "3.1.0-rc.2" \
    "" \
    "3.1.0-rc.2" \
    "true"

test_github_output \
    "Complex pre-release" \
    "1.0.0-alpha.beta.1" \
    "" \
    "1.0.0-alpha.beta.1" \
    "true"

# ============================================================================
# Test 3: Build Metadata (should be included in version output)
# ============================================================================
section "Test 3: Build Metadata in Output"

test_github_output \
    "Stable with build metadata" \
    "1.2.3+build.123" \
    "" \
    "1.2.3+build.123" \
    "false"

test_github_output \
    "Pre-release with build metadata" \
    "1.0.0-beta+exp.sha.5114f85" \
    "" \
    "1.0.0-beta+exp.sha.5114f85" \
    "true"

test_github_output \
    "Build metadata only" \
    "2.0.0+20130313144700" \
    "" \
    "2.0.0+20130313144700" \
    "false"

# ============================================================================
# Test 4: Range Validation Output
# ============================================================================
section "Test 4: Range Validation Output"

test_github_output \
    "Version satisfying >= range" \
    "1.2.3" \
    ">=1.0.0" \
    "1.2.3" \
    "false" \
    "true"

test_github_output \
    "Version satisfying caret range" \
    "2.5.0" \
    "^2.0.0" \
    "2.5.0" \
    "false" \
    "true"

test_github_output \
    "Version satisfying tilde range" \
    "1.2.5" \
    "~1.2.0" \
    "1.2.5" \
    "false" \
    "true"

test_github_output \
    "Version satisfying wildcard range" \
    "1.5.2" \
    "1.x" \
    "1.5.2" \
    "false" \
    "true"

test_github_output \
    "Version satisfying compound range" \
    "3.1.4" \
    ">=1.0.0 <4.0.0" \
    "3.1.4" \
    "false" \
    "true"

# ============================================================================
# Test 5: Pre-release with Range
# ============================================================================
section "Test 5: Pre-release with Range Output"

test_github_output \
    "Pre-release satisfying range" \
    "1.2.3-beta.1" \
    ">=1.0.0" \
    "1.2.3-beta.1" \
    "true" \
    "true"

test_github_output \
    "Pre-release with caret range" \
    "2.0.1-alpha" \
    "^2.0.0" \
    "2.0.1-alpha" \
    "true" \
    "true"

# ============================================================================
# Test 6: Edge Cases
# ============================================================================
section "Test 6: Edge Cases"

test_github_output \
    "Boundary version with >= range" \
    "1.0.0" \
    ">=1.0.0" \
    "1.0.0" \
    "false" \
    "true"

test_github_output \
    "Zero version with range" \
    "0.0.0" \
    ">=0.0.0" \
    "0.0.0" \
    "false" \
    "true"

test_github_output \
    "Complex version with complex range" \
    "1.5.0" \
    ">1.0.0 <=2.0.0" \
    "1.5.0" \
    "false" \
    "true"

# ============================================================================
# Test 7: Verify Output File Format
# ============================================================================
section "Test 7: Output File Format Validation"

# Test that the output file has the correct format
output_file="$TEMP_DIR/format_test.txt"
export GITHUB_OUTPUT="$output_file"

bash "$SCRIPT" "1.2.3" ">=1.0.0" &>/dev/null

if [ -f "$output_file" ]; then
    TESTS_RUN=$((TESTS_RUN + 1))

    # Check that file contains key=value format
    format_valid=true
    while IFS= read -r line; do
        if ! echo "$line" | grep -q '^[a-z_]*=.*$'; then
            format_valid=false
            break
        fi
    done < "$output_file"

    if [ "$format_valid" = true ]; then
        echo -e "${GREEN}✓${NC} Output file format is valid (key=value)"
        TESTS_PASSED=$((TESTS_PASSED + 1))
    else
        echo -e "${RED}✗${NC} Output file format is invalid"
        TESTS_FAILED=$((TESTS_FAILED + 1))
    fi

    # Check that file has exactly 3 lines when range is provided
    TESTS_RUN=$((TESTS_RUN + 1))
    line_count=$(wc -l < "$output_file")
    if [ "$line_count" -eq 3 ]; then
        echo -e "${GREEN}✓${NC} Output has correct number of lines (3) with range"
        TESTS_PASSED=$((TESTS_PASSED + 1))
    else
        echo -e "${RED}✗${NC} Output has $line_count lines, expected 3"
        TESTS_FAILED=$((TESTS_FAILED + 1))
    fi
else
    echo -e "${RED}✗${NC} Output file was not created"
    TESTS_FAILED=$((TESTS_FAILED + 2))
    TESTS_RUN=$((TESTS_RUN + 2))
fi

unset GITHUB_OUTPUT

# Test without range (should have 2 lines)
output_file="$TEMP_DIR/format_test_no_range.txt"
export GITHUB_OUTPUT="$output_file"

bash "$SCRIPT" "1.2.3" &>/dev/null

if [ -f "$output_file" ]; then
    TESTS_RUN=$((TESTS_RUN + 1))
    line_count=$(wc -l < "$output_file")
    if [ "$line_count" -eq 2 ]; then
        echo -e "${GREEN}✓${NC} Output has correct number of lines (2) without range"
        TESTS_PASSED=$((TESTS_PASSED + 1))
    else
        echo -e "${RED}✗${NC} Output has $line_count lines, expected 2"
        TESTS_FAILED=$((TESTS_FAILED + 1))
    fi
fi

unset GITHUB_OUTPUT

# ============================================================================
# Test 8: No Output When GITHUB_OUTPUT Not Set
# ============================================================================
section "Test 8: No Output When GITHUB_OUTPUT Not Set"

TESTS_RUN=$((TESTS_RUN + 1))

# Make sure GITHUB_OUTPUT is not set
unset GITHUB_OUTPUT

# Run script
bash "$SCRIPT" "1.2.3" &>/dev/null

# There should be no github_output file created in current directory
if [ ! -f "github_output" ] && [ ! -f "${GITHUB_OUTPUT:-}" ]; then
    echo -e "${GREEN}✓${NC} No output file created when GITHUB_OUTPUT not set"
    TESTS_PASSED=$((TESTS_PASSED + 1))
else
    echo -e "${RED}✗${NC} Output file was created when GITHUB_OUTPUT not set"
    TESTS_FAILED=$((TESTS_FAILED + 1))
fi

# ============================================================================
# Test Results Summary
# ============================================================================
echo ""
echo -e "${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo -e "${BLUE}Test Results Summary${NC}"
echo -e "${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo -e "Total tests run:    ${YELLOW}$TESTS_RUN${NC}"
echo -e "Tests passed:       ${GREEN}$TESTS_PASSED${NC}"
echo -e "Tests failed:       ${RED}$TESTS_FAILED${NC}"
echo ""

if [ $TESTS_FAILED -eq 0 ]; then
    echo -e "${GREEN}✓ All GITHUB_OUTPUT tests passed!${NC}"
    exit 0
else
    echo -e "${RED}✗ Some GITHUB_OUTPUT tests failed${NC}"
    exit 1
fi
