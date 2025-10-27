#!/usr/bin/env bash
set -euo pipefail

# Script: validate_semver.sh
# Description: Validates version strings against SemVer specification
# Usage: validate_semver.sh <version>

# Color codes for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[0;33m'
NC='\033[0m' # No Color

# Display usage information
usage() {
    echo "Usage: $0 <version> [range]"
    echo ""
    echo "Validates a version string against SemVer specification."
    echo "Optionally validates that the version satisfies a semver range."
    echo ""
    echo "Examples:"
    echo "  $0 1.2.3"
    echo "  $0 2.0.0-rc.1"
    echo "  $0 1.0.0-beta.2+build.123"
    echo ""
    echo "Range validation examples:"
    echo "  $0 1.2.3 '>=1.0.0'          # Greater than or equal"
    echo "  $0 2.5.0 '^2.0.0'           # Caret range (compatible with 2.x.x)"
    echo "  $0 1.2.5 '~1.2.0'           # Tilde range (compatible with 1.2.x)"
    echo "  $0 3.1.4 '>=1.0.0 <4.0.0'   # Compound range"
    echo "  $0 1.5.2 '1.x'              # Wildcard range"
    exit 1
}

# Parse version string into components
# Usage: parse_version <version>
# Outputs: major minor patch prerelease
parse_version() {
    local version="$1"
    # Remove build metadata
    version="${version%%+*}"

    # Extract prerelease if present
    local prerelease=""
    if [[ "$version" =~ - ]]; then
        prerelease="${version#*-}"
        version="${version%%-*}"
    fi

    # Parse major.minor.patch
    local major="${version%%.*}"
    local rest="${version#*.}"
    local minor="${rest%%.*}"
    local patch="${rest#*.}"

    echo "$major $minor $patch $prerelease"
}

# Compare two versions
# Returns: 0 if v1 == v2, 1 if v1 > v2, -1 if v1 < v2
compare_versions() {
    local v1="$1"
    local v2="$2"

    read -r maj1 min1 pat1 pre1 <<< "$(parse_version "$v1")"
    read -r maj2 min2 pat2 pre2 <<< "$(parse_version "$v2")"

    # Compare major
    if [ "$maj1" -gt "$maj2" ]; then echo 1; return; fi
    if [ "$maj1" -lt "$maj2" ]; then echo -1; return; fi

    # Compare minor
    if [ "$min1" -gt "$min2" ]; then echo 1; return; fi
    if [ "$min1" -lt "$min2" ]; then echo -1; return; fi

    # Compare patch
    if [ "$pat1" -gt "$pat2" ]; then echo 1; return; fi
    if [ "$pat1" -lt "$pat2" ]; then echo -1; return; fi

    # Compare prerelease (version with prerelease < version without)
    if [ -z "$pre1" ] && [ -n "$pre2" ]; then echo 1; return; fi
    if [ -n "$pre1" ] && [ -z "$pre2" ]; then echo -1; return; fi

    # Both have prerelease or both don't - lexicographic comparison
    if [ "$pre1" \> "$pre2" ]; then echo 1; return; fi
    if [ "$pre1" \< "$pre2" ]; then echo -1; return; fi

    echo 0
}

# Check if version satisfies a single range constraint
# Usage: check_constraint <version> <operator> <target>
check_constraint() {
    local version="$1"
    local operator="$2"
    local target="$3"
    local cmp

    cmp=$(compare_versions "$version" "$target")

    case "$operator" in
        "="|"")
            [ "$cmp" -eq 0 ]
            ;;
        ">")
            [ "$cmp" -eq 1 ]
            ;;
        ">=")
            [ "$cmp" -eq 1 ] || [ "$cmp" -eq 0 ]
            ;;
        "<")
            [ "$cmp" -eq -1 ]
            ;;
        "<=")
            [ "$cmp" -eq -1 ] || [ "$cmp" -eq 0 ]
            ;;
        *)
            return 1
            ;;
    esac
}

# Check if version satisfies caret range (^)
# ^1.2.3 := >=1.2.3 <2.0.0
# ^0.2.3 := >=0.2.3 <0.3.0
# ^0.0.3 := >=0.0.3 <0.0.4
check_caret_range() {
    local version="$1"
    local target="$2"

    read -r maj min pat pre <<< "$(parse_version "$target")"

    # Must be >= target
    if ! check_constraint "$version" ">=" "$target"; then
        return 1
    fi

    # Determine upper bound based on leftmost non-zero component
    local upper
    if [ "$maj" != "0" ]; then
        upper="$((maj + 1)).0.0"
    elif [ "$min" != "0" ]; then
        upper="0.$((min + 1)).0"
    else
        upper="0.0.$((pat + 1))"
    fi

    check_constraint "$version" "<" "$upper"
}

# Check if version satisfies tilde range (~)
# ~1.2.3 := >=1.2.3 <1.3.0
# ~1.2 := >=1.2.0 <1.3.0
check_tilde_range() {
    local version="$1"
    local target="$2"

    read -r maj min pat pre <<< "$(parse_version "$target")"

    # Must be >= target
    if ! check_constraint "$version" ">=" "$target"; then
        return 1
    fi

    # Upper bound is next minor version
    local upper="$maj.$((min + 1)).0"

    check_constraint "$version" "<" "$upper"
}

# Check if version matches wildcard pattern
# 1.x, 1.X, 1.*, 1.2.x, etc.
check_wildcard() {
    local version="$1"
    local pattern="$2"

    read -r v_maj v_min v_pat v_pre <<< "$(parse_version "$version")"

    # Normalize shorthand patterns: "1.x" -> "1.x.x", "1" -> "1.x.x"
    local dot_count
    dot_count=$(echo "$pattern" | tr -cd '.' | wc -c)

    if [ "$dot_count" -eq 0 ]; then
        # Just "1" or "1x" -> "1.x.x"
        pattern="${pattern}.x.x"
    elif [ "$dot_count" -eq 1 ]; then
        # "1.2" or "1.x" -> "1.2.x" or "1.x.x"
        pattern="${pattern}.x"
    fi

    # Replace wildcards with a placeholder that won't conflict
    pattern="${pattern//x/__WILDCARD__}"
    pattern="${pattern//X/__WILDCARD__}"
    pattern="${pattern//\*/__WILDCARD__}"

    # Escape dots
    pattern="${pattern//./\\.}"

    # Replace placeholders with regex pattern for any number
    pattern="${pattern//__WILDCARD__/[0-9]+}"

    # Add start/end anchors and optional prerelease/build metadata
    pattern="^${pattern}(-.*)?(\+.*)?$"

    echo "$version" | grep -Pq "$pattern"
}

# Check if version satisfies a range expression
satisfies_range() {
    local version="$1"
    local range="$2"

    # Handle caret range
    if [[ "$range" =~ ^\^(.+)$ ]]; then
        check_caret_range "$version" "${BASH_REMATCH[1]}"
        return $?
    fi

    # Handle tilde range
    if [[ "$range" =~ ^~(.+)$ ]]; then
        check_tilde_range "$version" "${BASH_REMATCH[1]}"
        return $?
    fi

    # Handle wildcard
    if [[ "$range" =~ [xX*] ]]; then
        check_wildcard "$version" "$range"
        return $?
    fi

    # Handle compound ranges (space-separated)
    if [[ "$range" =~ [[:space:]] ]]; then
        local all_satisfied=true
        local constraint

        # Split on spaces and process each constraint
        while read -r constraint; do
            [ -z "$constraint" ] && continue

            if ! satisfies_range "$version" "$constraint"; then
                all_satisfied=false
                break
            fi
        done <<< "$(echo "$range" | tr ' ' '\n')"

        [ "$all_satisfied" = true ]
        return $?
    fi

    # Handle simple operator ranges (>=, >, <=, <, =)
    if [[ "$range" =~ ^(>=|>|<=|<|=)?(.+)$ ]]; then
        local operator="${BASH_REMATCH[1]}"
        local target="${BASH_REMATCH[2]}"

        # Default to = if no operator
        [ -z "$operator" ] && operator="="

        check_constraint "$version" "$operator" "$target"
        return $?
    fi

    return 1
}

# Check if version argument is provided
if [ $# -eq 0 ]; then
    echo -e "${RED}Error: No version specified${NC}"
    usage
fi

VERSION="$1"
RANGE="${2:-}"

# SemVer regex from https://semver.org/#is-there-a-suggested-regular-expression-regex-to-check-a-semver-string
SEMVER_REGEX='^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)(-((0|[1-9][0-9]*|[0-9]*[a-zA-Z-][0-9a-zA-Z-]*)(\.(0|[1-9][0-9]*|[0-9]*[a-zA-Z-][0-9a-zA-Z-]*))*))?(\+([0-9a-zA-Z-]+(\.[0-9a-zA-Z-]+)*))?$'

# Validate version format
if ! echo "$VERSION" | grep -Pq "$SEMVER_REGEX"; then
    echo -e "${RED}Error: Version '$VERSION' is not valid SemVer format${NC}"
    echo ""
    echo "Valid SemVer format examples:"
    echo "  - Stable releases: 1.2.3, 2.0.0, 10.5.8"
    echo "  - Pre-releases: 2.0.0-rc.1, 1.0.0-alpha, 1.0.0-beta.2"
    echo "  - With build metadata: 1.0.0-beta.2+build.123, 1.2.3+20130313144700"
    echo ""
    echo "Format: MAJOR.MINOR.PATCH[-PRERELEASE][+BUILD]"
    exit 1
fi

# Validate range if provided
if [ -n "$RANGE" ]; then
    if satisfies_range "$VERSION" "$RANGE"; then
        echo -e "${GREEN}✓ Version '$VERSION' satisfies range '$RANGE'${NC}"
        RANGE_SATISFIED=true
    else
        echo -e "${RED}Error: Version '$VERSION' does not satisfy range '$RANGE'${NC}"
        exit 1
    fi
else
    RANGE_SATISFIED=false
fi

# Determine release type
if echo "$VERSION" | grep -q '-'; then
    echo -e "${YELLOW}✓ Detected pre-release version: $VERSION${NC}"
    echo "type=prerelease"
else
    echo -e "${GREEN}✓ Detected stable release version: $VERSION${NC}"
    echo "type=stable"
fi

# Output version for GitHub Actions if GITHUB_OUTPUT is set
if [ -n "${GITHUB_OUTPUT:-}" ]; then
    echo "version=$VERSION" >> "$GITHUB_OUTPUT"

    if echo "$VERSION" | grep -q '-'; then
        echo "is_prerelease=true" >> "$GITHUB_OUTPUT"
    else
        echo "is_prerelease=false" >> "$GITHUB_OUTPUT"
    fi

    # Output range validation result if range was provided
    if [ -n "$RANGE" ]; then
        echo "range_satisfied=$RANGE_SATISFIED" >> "$GITHUB_OUTPUT"
    fi
fi

exit 0
