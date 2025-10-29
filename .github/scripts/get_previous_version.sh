#!/bin/bash

# get_previous_version.sh - Extract versions from git history with chronological filtering
# Usage: get_previous_version.sh [version]
#
# Arguments:
#   version: (Optional) The current semver version (e.g., 1.2.3 or 2.0.0-rc.1)
#
# Output:
#   - No argument: Lists all versions in chronological order (newest first)
#   - With argument: Prints the previous version before the specified version
#
# This script:
#   1. Extracts all versions from git history of src/version.py
#   2. Validates each version using validate_semver.sh
#   3. Filters out duplicates (keeps newest occurrence)
#   4. Filters out reverts (versions that break chronological order)
#   5. Returns a clean, monotonically decreasing version list

set -euo pipefail

# Path to validate_semver.sh
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VALIDATE_SEMVER="$SCRIPT_DIR/validate_semver.sh"

# Display usage information
usage() {
    echo "Usage: $0 [version]" >&2
    echo "" >&2
    echo "Extracts and filters versions from git history of src/version.py." >&2
    echo "Removes duplicates and reverts to provide a clean chronological list." >&2
    echo "" >&2
    echo "Arguments:" >&2
    echo "  version: (Optional) The current semver version (e.g., 1.2.3 or 2.0.0-rc.1)" >&2
    echo "" >&2
    echo "Examples:" >&2
    echo "  $0                # List all versions in chronological order" >&2
    echo "  $0 1.2.3          # Get version before 1.2.3" >&2
    echo "  $0 2.0.0-rc.1     # Get version before 2.0.0-rc.1" >&2
    exit 1
}

# Extract and filter versions from git history
# Returns filtered versions and dates via global arrays
get_filtered_versions() {
    # Arrays to store all versions
    local -a all_versions=()
    local -a all_dates=()
    local -a all_timestamps=()

    # Extract all versions from git history
    while IFS='|' read -r commit_hash timestamp date_human; do
        # Extract version from this commit
        local version
        version=$(git show "$commit_hash:src/version.py" 2>/dev/null | grep -oP '__version__ = "\K[^"]+' || echo "")

        # Skip if version extraction failed
        if [ -z "$version" ]; then
            continue
        fi

        # Validate version using validate_semver.sh
        if ! bash "$VALIDATE_SEMVER" "$version" >/dev/null 2>&1; then
            continue
        fi

        # Store this version
        all_versions+=("$version")
        all_dates+=("$date_human")
        all_timestamps+=("$timestamp")
    done < <(git log --all --format="%H|%ct|%ci" --follow -- src/version.py)

    # Sort arrays by timestamp (newest first)
    # Create indexed array for sorting
    local -a indices=()
    for i in "${!all_timestamps[@]}"; do
        indices+=("$i")
    done

    # Bubble sort by timestamp (descending)
    local n=${#indices[@]}
    for ((i = 0; i < n-1; i++)); do
        for ((j = 0; j < n-i-1; j++)); do
            local idx1=${indices[$j]}
            local idx2=${indices[$((j+1))]}
            if [ "${all_timestamps[$idx1]}" -lt "${all_timestamps[$idx2]}" ]; then
                # Swap
                local temp=${indices[$j]}
                indices[$j]=${indices[$((j+1))]}
                indices[$((j+1))]=$temp
            fi
        done
    done

    # Filter duplicates and reverts
    declare -A seen_versions
    filtered_versions=()
    filtered_dates=()
    local max_version=""

    for idx in "${indices[@]}"; do
        local version="${all_versions[$idx]}"
        local date="${all_dates[$idx]}"

        # Skip duplicates (keep only first/newest occurrence)
        if [[ -n "${seen_versions[$version]:-}" ]]; then
            continue
        fi

        # Skip reverts (versions greater than what we've already seen)
        if [[ -n "$max_version" ]]; then
            if bash "$VALIDATE_SEMVER" "$version" ">$max_version" >/dev/null 2>&1; then
                # This version is greater than max_version = revert detected, skip
                continue
            fi
        fi

        # Keep this version
        filtered_versions+=("$version")
        filtered_dates+=("$date")
        seen_versions["$version"]=1
        max_version="$version"
    done
}

# Check arguments
if [ "$#" -gt 1 ]; then
    echo "Error: Too many arguments" >&2
    usage
fi

# Get filtered versions
declare -a filtered_versions
declare -a filtered_dates
get_filtered_versions

# Handle based on number of arguments
if [ "$#" -eq 0 ]; then
    # No argument: list all versions
    echo "Recent versions:" >&2
    echo "" >&2

    for i in "${!filtered_versions[@]}"; do
        echo "${filtered_versions[$i]} | ${filtered_dates[$i]}" >&2
    done

    exit 0
else
    # One argument: find previous version
    VERSION="$1"
    LATEST_VERSION="${filtered_versions[0]}"

    # if version is larger than the latest version, return latest version
    if bash "$VALIDATE_SEMVER" "$VERSION" ">${LATEST_VERSION}" >/dev/null 2>&1; then
        echo "$LATEST_VERSION"
        exit 0
    fi

    # Find the version in the list
    for i in "${!filtered_versions[@]}"; do
        if [ "${filtered_versions[$i]}" = "$VERSION" ]; then
            # Found it, return the next version in the list
            next_idx=$((i + 1))
            if [ "$next_idx" -lt "${#filtered_versions[@]}" ]; then
                echo "${filtered_versions[$next_idx]}"
                exit 0
            else
                echo "Error: No previous version found (this is the oldest version)" >&2
                exit 1
            fi
        fi
    done

    # Version not found in list
    echo "Error: Version $VERSION not found in git history" >&2
    exit 1
fi
