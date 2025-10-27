#!/usr/bin/env bash
set -euo pipefail

# Test script for validate_semver.sh
# Usage: ./test_validate_semver.sh

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

# Test helper functions
run_test() {
    local test_name="$1"
    local version="$2"
    local range="${3:-}"
    local should_pass="$4"

    TESTS_RUN=$((TESTS_RUN + 1))

    local result=0
    if [ -n "$range" ]; then
        bash "$SCRIPT" "$version" "$range" &>/dev/null || result=$?
    else
        bash "$SCRIPT" "$version" &>/dev/null || result=$?
    fi

    if [ "$should_pass" = "true" ]; then
        if [ $result -eq 0 ]; then
            echo -e "${GREEN}✓${NC} $test_name"
            TESTS_PASSED=$((TESTS_PASSED + 1))
        else
            echo -e "${RED}✗${NC} $test_name (expected pass, got fail)"
            TESTS_FAILED=$((TESTS_FAILED + 1))
        fi
    else
        if [ $result -ne 0 ]; then
            echo -e "${GREEN}✓${NC} $test_name"
            TESTS_PASSED=$((TESTS_PASSED + 1))
        else
            echo -e "${RED}✗${NC} $test_name (expected fail, got pass)"
            TESTS_FAILED=$((TESTS_FAILED + 1))
        fi
    fi
}

# Print test section header
section() {
    echo ""
    echo -e "${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
    echo -e "${BLUE}$1${NC}"
    echo -e "${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
}

# Start tests
echo -e "${YELLOW}Running validate_semver.sh tests...${NC}"

# ============================================================================
# Test 1: Basic SemVer Format Validation
# ============================================================================
section "Test 1: Basic SemVer Format Validation"

run_test "Valid stable version: 1.2.3" "1.2.3" "" "true"
run_test "Valid stable version: 0.0.0" "0.0.0" "" "true"
run_test "Valid stable version: 10.20.30" "10.20.30" "" "true"
run_test "Valid pre-release: 1.0.0-alpha" "1.0.0-alpha" "" "true"
run_test "Valid pre-release: 1.0.0-alpha.1" "1.0.0-alpha.1" "" "true"
run_test "Valid pre-release: 1.0.0-0.3.7" "1.0.0-0.3.7" "" "true"
run_test "Valid pre-release: 1.0.0-x.7.z.92" "1.0.0-x.7.z.92" "" "true"
run_test "Valid with build metadata: 1.0.0+20130313144700" "1.0.0+20130313144700" "" "true"
run_test "Valid with build metadata: 1.0.0-beta+exp.sha.5114f85" "1.0.0-beta+exp.sha.5114f85" "" "true"
run_test "Valid complex: 1.0.0-alpha.beta+build.1" "1.0.0-alpha.beta+build.1" "" "true"

run_test "Invalid: missing patch" "1.2" "" "false"
run_test "Invalid: missing minor and patch" "1" "" "false"
run_test "Invalid: non-numeric major" "v1.2.3" "" "false"
run_test "Invalid: leading zeros" "01.2.3" "" "false"
run_test "Invalid: leading zeros in minor" "1.02.3" "" "false"
run_test "Invalid: leading zeros in patch" "1.2.03" "" "false"
run_test "Invalid: empty string" "" "" "false"
run_test "Invalid: random text" "not-a-version" "" "false"

# ============================================================================
# Test 2: Comparison Operators
# ============================================================================
section "Test 2: Comparison Operators (>=, >, <=, <, =)"

# Greater than or equal (>=)
run_test "1.2.3 >= 1.0.0" "1.2.3" ">=1.0.0" "true"
run_test "1.2.3 >= 1.2.3" "1.2.3" ">=1.2.3" "true"
run_test "1.2.3 >= 2.0.0" "1.2.3" ">=2.0.0" "false"

# Greater than (>)
run_test "2.0.0 > 1.9.9" "2.0.0" ">1.9.9" "true"
run_test "1.2.3 > 1.2.3" "1.2.3" ">1.2.3" "false"
run_test "1.0.0 > 2.0.0" "1.0.0" ">2.0.0" "false"

# Less than or equal (<=)
run_test "1.0.0 <= 2.0.0" "1.0.0" "<=2.0.0" "true"
run_test "1.2.3 <= 1.2.3" "1.2.3" "<=1.2.3" "true"
run_test "3.0.0 <= 2.0.0" "3.0.0" "<=2.0.0" "false"

# Less than (<)
run_test "1.0.0 < 2.0.0" "1.0.0" "<2.0.0" "true"
run_test "1.2.3 < 1.2.3" "1.2.3" "<1.2.3" "false"
run_test "3.0.0 < 2.0.0" "3.0.0" "<2.0.0" "false"

# Equal (=)
run_test "1.2.3 = 1.2.3" "1.2.3" "=1.2.3" "true"
run_test "1.2.3 = 1.2.4" "1.2.3" "=1.2.4" "false"

# ============================================================================
# Test 3: Caret Ranges (^)
# ============================================================================
section "Test 3: Caret Ranges (^)"

# Standard caret ranges
run_test "2.5.0 satisfies ^2.0.0" "2.5.0" "^2.0.0" "true"
run_test "2.0.0 satisfies ^2.0.0" "2.0.0" "^2.0.0" "true"
run_test "2.9.9 satisfies ^2.0.0" "2.9.9" "^2.0.0" "true"
run_test "3.0.0 does not satisfy ^2.0.0" "3.0.0" "^2.0.0" "false"
run_test "1.9.9 does not satisfy ^2.0.0" "1.9.9" "^2.0.0" "false"

# Caret with 0.x versions
run_test "0.2.5 satisfies ^0.2.3" "0.2.5" "^0.2.3" "true"
run_test "0.2.3 satisfies ^0.2.3" "0.2.3" "^0.2.3" "true"
run_test "0.3.0 does not satisfy ^0.2.3" "0.3.0" "^0.2.3" "false"
run_test "0.2.2 does not satisfy ^0.2.3" "0.2.2" "^0.2.3" "false"

# Caret with 0.0.x versions
run_test "0.0.3 satisfies ^0.0.3" "0.0.3" "^0.0.3" "true"
run_test "0.0.4 does not satisfy ^0.0.3" "0.0.4" "^0.0.3" "false"

# Caret with pre-release
run_test "2.0.0-rc.1 does not satisfy ^2.0.0" "2.0.0-rc.1" "^2.0.0" "false"
run_test "2.0.1 satisfies ^2.0.0" "2.0.1" "^2.0.0" "true"

# ============================================================================
# Test 4: Tilde Ranges (~)
# ============================================================================
section "Test 4: Tilde Ranges (~)"

# Standard tilde ranges
run_test "1.2.9 satisfies ~1.2.0" "1.2.9" "~1.2.0" "true"
run_test "1.2.0 satisfies ~1.2.0" "1.2.0" "~1.2.0" "true"
run_test "1.3.0 does not satisfy ~1.2.0" "1.3.0" "~1.2.0" "false"
run_test "1.1.9 does not satisfy ~1.2.0" "1.1.9" "~1.2.0" "false"

# Tilde with different versions
run_test "2.5.8 satisfies ~2.5.0" "2.5.8" "~2.5.0" "true"
run_test "2.6.0 does not satisfy ~2.5.0" "2.6.0" "~2.5.0" "false"

run_test "0.1.5 satisfies ~0.1.0" "0.1.5" "~0.1.0" "true"
run_test "0.2.0 does not satisfy ~0.1.0" "0.2.0" "~0.1.0" "false"

# ============================================================================
# Test 5: Wildcard Ranges (x, X, *)
# ============================================================================
section "Test 5: Wildcard Ranges (x, X, *)"

# Major wildcard
run_test "1.5.2 satisfies 1.x" "1.5.2" "1.x" "true"
run_test "1.0.0 satisfies 1.x" "1.0.0" "1.x" "true"
run_test "1.9.9 satisfies 1.x" "1.9.9" "1.x" "true"
run_test "2.0.0 does not satisfy 1.x" "2.0.0" "1.x" "false"

# Major wildcard with X
run_test "2.3.4 satisfies 2.X" "2.3.4" "2.X" "true"
run_test "3.0.0 does not satisfy 2.X" "3.0.0" "2.X" "false"

# Major wildcard with *
run_test "3.1.2 satisfies 3.*" "3.1.2" "3.*" "true"
run_test "4.0.0 does not satisfy 3.*" "4.0.0" "3.*" "false"

# Minor wildcard
run_test "1.5.2 satisfies 1.5.x" "1.5.2" "1.5.x" "true"
run_test "1.5.0 satisfies 1.5.x" "1.5.0" "1.5.x" "true"
run_test "1.6.0 does not satisfy 1.5.x" "1.6.0" "1.5.x" "false"

# Full wildcard
run_test "1.2.3 satisfies 1.x.x" "1.2.3" "1.x.x" "true"
run_test "1.5.8 satisfies 1.x.x" "1.5.8" "1.x.x" "true"

# Wildcard with prerelease
run_test "1.5.2-beta satisfies 1.x" "1.5.2-beta" "1.x" "true"

# ============================================================================
# Test 6: Compound Ranges
# ============================================================================
section "Test 6: Compound Ranges (Multiple Constraints)"

# AND ranges
run_test "1.5.0 satisfies >=1.0.0 <2.0.0" "1.5.0" ">=1.0.0 <2.0.0" "true"
run_test "2.0.0 does not satisfy >=1.0.0 <2.0.0" "2.0.0" ">=1.0.0 <2.0.0" "false"
run_test "0.9.9 does not satisfy >=1.0.0 <2.0.0" "0.9.9" ">=1.0.0 <2.0.0" "false"

run_test "3.1.4 satisfies >=1.0.0 <4.0.0" "3.1.4" ">=1.0.0 <4.0.0" "true"
run_test "4.0.0 does not satisfy >=1.0.0 <4.0.0" "4.0.0" ">=1.0.0 <4.0.0" "false"

run_test "1.5.0 satisfies >1.0.0 <=2.0.0" "1.5.0" ">1.0.0 <=2.0.0" "true"
run_test "1.0.0 does not satisfy >1.0.0 <=2.0.0" "1.0.0" ">1.0.0 <=2.0.0" "false"
run_test "2.0.0 satisfies >1.0.0 <=2.0.0" "2.0.0" ">1.0.0 <=2.0.0" "true"

# Multiple constraints
run_test "2.5.0 satisfies >=2.0.0 <=3.0.0" "2.5.0" ">=2.0.0 <=3.0.0" "true"
run_test "2.5.0 satisfies >2.0.0 <3.0.0" "2.5.0" ">2.0.0 <3.0.0" "true"

# ============================================================================
# Test 7: Pre-release Version Comparisons
# ============================================================================
section "Test 7: Pre-release Version Comparisons"

# Pre-release comparisons
run_test "2.0.0-alpha satisfies >=2.0.0-alpha" "2.0.0-alpha" ">=2.0.0-alpha" "true"
run_test "2.0.0-beta satisfies >2.0.0-alpha" "2.0.0-beta" ">2.0.0-alpha" "true"
run_test "2.0.0-alpha satisfies <2.0.0" "2.0.0-alpha" "<2.0.0" "true"
run_test "2.0.0 satisfies >2.0.0-alpha" "2.0.0" ">2.0.0-alpha" "true"

run_test "1.0.0-rc.1 satisfies >=1.0.0-rc.1" "1.0.0-rc.1" ">=1.0.0-rc.1" "true"
run_test "1.0.0-rc.2 satisfies >1.0.0-rc.1" "1.0.0-rc.2" ">1.0.0-rc.1" "true"

# Pre-release with ranges
run_test "1.2.3-beta.1 satisfies >=1.0.0" "1.2.3-beta.1" ">=1.0.0" "true"
run_test "1.2.3-beta.1 satisfies >=1.2.3-alpha" "1.2.3-beta.1" ">=1.2.3-alpha" "true"

# ============================================================================
# Test 8: Build Metadata
# ============================================================================
section "Test 8: Build Metadata (should be ignored in comparisons)"

run_test "1.2.3+build.1 = 1.2.3" "1.2.3+build.1" "=1.2.3" "true"
run_test "1.2.3+build.1 = 1.2.3+build.2" "1.2.3+build.1" "=1.2.3+build.2" "true"
run_test "1.2.3+build satisfies >=1.2.0" "1.2.3+build" ">=1.2.0" "true"
run_test "1.0.0-beta+exp.sha.5114f85 satisfies <1.0.0" "1.0.0-beta+exp.sha.5114f85" "<1.0.0" "true"

# ============================================================================
# Test 9: Edge Cases
# ============================================================================
section "Test 9: Edge Cases"

# Boundary conditions
run_test "1.0.0 satisfies >=1.0.0" "1.0.0" ">=1.0.0" "true"
run_test "1.0.0 does not satisfy >1.0.0" "1.0.0" ">1.0.0" "false"
run_test "1.0.0 satisfies <=1.0.0" "1.0.0" "<=1.0.0" "true"
run_test "1.0.0 does not satisfy <1.0.0" "1.0.0" "<1.0.0" "false"

# Large version numbers
run_test "100.200.300 satisfies >=1.0.0" "100.200.300" ">=1.0.0" "true"
run_test "999.999.999 satisfies ^999.0.0" "999.999.999" "^999.0.0" "true"

# Zero versions
run_test "0.0.0 satisfies >=0.0.0" "0.0.0" ">=0.0.0" "true"
run_test "0.1.0 satisfies >0.0.0" "0.1.0" ">0.0.0" "true"
run_test "0.0.1 satisfies ^0.0.1" "0.0.1" "^0.0.1" "true"

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
    echo -e "${GREEN}✓ All tests passed!${NC}"
    exit 0
else
    echo -e "${RED}✗ Some tests failed${NC}"
    exit 1
fi
