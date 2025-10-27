# Script Tests

This directory contains test suites for the scripts in `.github/scripts/`.

## Running Tests

### Run All Tests

Run all test suites:

```bash
# Run semver validation tests
.github/scripts/test/test_validate_semver.sh

# Run GitHub Actions output tests
.github/scripts/test/test_github_output.sh
```

### Test validate_semver.sh

Run the comprehensive test suite for the SemVer validation script:

```bash
.github/scripts/test/test_validate_semver.sh
```

The test suite includes **98 test cases** covering:

1. **Basic SemVer Format Validation** (18 tests)
   - Valid stable versions (1.2.3, 0.0.0, etc.)
   - Valid pre-release versions (1.0.0-alpha, 1.0.0-rc.1, etc.)
   - Valid build metadata (1.0.0+build, 1.0.0-beta+exp.sha.5114f85)
   - Invalid formats (missing components, leading zeros, non-numeric)

2. **Comparison Operators** (14 tests)
   - Greater than or equal (`>=`)
   - Greater than (`>`)
   - Less than or equal (`<=`)
   - Less than (`<`)
   - Equal (`=`)

3. **Caret Ranges** (13 tests)
   - Standard caret ranges (`^2.0.0`)
   - Caret with 0.x versions (`^0.2.3`)
   - Caret with 0.0.x versions (`^0.0.3`)
   - Pre-release handling

4. **Tilde Ranges** (8 tests)
   - Standard tilde ranges (`~1.2.0`)
   - Tilde with different versions

5. **Wildcard Ranges** (14 tests)
   - Major wildcards (`1.x`, `2.X`, `3.*`)
   - Minor wildcards (`1.5.x`)
   - Full wildcards (`1.x.x`)
   - Pre-release with wildcards

6. **Compound Ranges** (10 tests)
   - AND ranges (`>=1.0.0 <2.0.0`)
   - Multiple constraints (`>1.0.0 <=2.0.0`)

7. **Pre-release Version Comparisons** (8 tests)
   - Pre-release ordering
   - Pre-release with ranges

8. **Build Metadata** (4 tests)
   - Build metadata is ignored in comparisons

9. **Edge Cases** (9 tests)
   - Boundary conditions
   - Large version numbers
   - Zero versions

## Test Output

The test script provides colorized output:

- ✓ (green) - Test passed
- ✗ (red) - Test failed
- Blue section headers
- Summary statistics at the end

Example output:

```text
Running validate_semver.sh tests...

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Test 1: Basic SemVer Format Validation
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
✓ Valid stable version: 1.2.3
✓ Valid stable version: 0.0.0
...

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Test Results Summary
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Total tests run:    98
Tests passed:       98
Tests failed:       0

✓ All tests passed!
```

## Exit Codes

- `0` - All tests passed
- `1` - One or more tests failed

## Adding New Tests

To add new test cases, edit [test_validate_semver.sh](test_validate_semver.sh) and use the `run_test` helper function:

```bash
run_test "test description" "version" "range" "should_pass"
```

Parameters:

- `test_description`: Human-readable test name
- `version`: The semver version to test
- `range`: The range to validate against (optional, use `""` for none)
- `should_pass`: `"true"` if test should pass, `"false"` if it should fail

Example:

```bash
run_test "1.2.3 >= 1.0.0" "1.2.3" ">=1.0.0" "true"
run_test "1.2.3 >= 2.0.0" "1.2.3" ">=2.0.0" "false"
```

### Test GITHUB_OUTPUT Functionality

Run tests specifically for GitHub Actions output integration:

```bash
.github/scripts/test/test_github_output.sh
```

The GITHUB_OUTPUT test suite includes **25 test cases** covering:

1. **Basic Output Without Range** (4 tests)
   - Stable version outputs
   - Large version numbers

2. **Pre-release Version Output** (4 tests)
   - Alpha, beta, rc versions
   - Complex pre-release identifiers

3. **Build Metadata in Output** (3 tests)
   - Verifies build metadata is preserved in output
   - Stable and pre-release with metadata

4. **Range Validation Output** (5 tests)
   - Output when version satisfies ranges
   - Tests `range_satisfied` field

5. **Pre-release with Range Output** (2 tests)
   - Pre-release versions with range constraints

6. **Edge Cases** (3 tests)
   - Boundary conditions
   - Complex compound ranges

7. **Output File Format Validation** (3 tests)
   - Validates key=value format
   - Correct number of output lines (2 without range, 3 with range)

8. **No Output When GITHUB_OUTPUT Not Set** (1 test)
   - Ensures no file created when env var not set

**Output Fields:**

- `version` - The validated semver version
- `is_prerelease` - Boolean (`true`/`false`) indicating if version is pre-release
- `range_satisfied` - Boolean (`true`/`false`) indicating if version satisfies range (only when range provided)

## CI Integration

These tests can be integrated into CI/CD pipelines:

```yaml
- name: Run script tests
  run: |
    .github/scripts/test/test_validate_semver.sh
    .github/scripts/test/test_github_output.sh
```
