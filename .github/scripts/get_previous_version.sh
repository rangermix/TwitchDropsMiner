#!/bin/bash

# get_previous_version.sh - Extract the previous version from git history
# Usage: get_previous_version.sh <version>
#
# Arguments:
#   version: The current semver version (e.g., 1.2.3 or 2.0.0-rc.1)
#
# Output:
#   Prints the previous version to stdout
#   Returns exit code 0 on success, non-zero on failure
#
# This script:
#   1. Finds the commit that introduced the specified version
#   2. Gets the parent commit of that version change
#   3. Extracts the version from the parent commit
#   4. Outputs the previous version

set -euo pipefail

# Display usage information
usage() {
    echo "Usage: $0 <version>" >&2
    echo "" >&2
    echo "Extracts the previous version from git history before the specified version." >&2
    echo "" >&2
    echo "Arguments:" >&2
    echo "  version: The current semver version (e.g., 1.2.3 or 2.0.0-rc.1)" >&2
    echo "" >&2
    echo "Examples:" >&2
    echo "  $0 1.2.3          # Get version before 1.2.3" >&2
    echo "  $0 2.0.0-rc.1     # Get version before 2.0.0-rc.1" >&2
    exit 1
}

# Check if version argument is provided
if [ "$#" -ne 1 ]; then
    echo "Error: Version argument required" >&2
    usage
fi

VERSION="$1"

# Get the git history of version.py, find the commit that introduced $VERSION
CURRENT_VERSION_COMMIT=$(git log --all --format="%H" --follow -S "__version__ = \"$VERSION\"" -- src/version.py | head -1)

if [ -z "$CURRENT_VERSION_COMMIT" ]; then
    echo "Error: Could not find commit that set version to $VERSION" >&2
    exit 1
fi

# Get the previous commit for version.py
PREVIOUS_VERSION_COMMIT=$(git log --all --format="%H" --follow "$CURRENT_VERSION_COMMIT^..HEAD" -- src/version.py | grep -A1 "$CURRENT_VERSION_COMMIT" | tail -1 || true)

# If we can't find a previous commit in that way, try getting the parent commit and extracting version from there
if [ -z "$PREVIOUS_VERSION_COMMIT" ] || [ "$PREVIOUS_VERSION_COMMIT" = "$CURRENT_VERSION_COMMIT" ]; then
    PREVIOUS_VERSION_COMMIT=$(git rev-parse "$CURRENT_VERSION_COMMIT^" 2>/dev/null || true)
fi

if [ -z "$PREVIOUS_VERSION_COMMIT" ]; then
    echo "Error: Could not find previous commit for version.py" >&2
    exit 1
fi

# Extract version from that commit
PREVIOUS_VERSION=$(git show "$PREVIOUS_VERSION_COMMIT:src/version.py" | grep -oP '__version__ = "\K[^"]+' || true)

if [ -z "$PREVIOUS_VERSION" ]; then
    echo "Error: Could not extract previous version from commit ${PREVIOUS_VERSION_COMMIT:0:7}" >&2
    exit 1
fi

# Output the previous version to stdout
echo "$PREVIOUS_VERSION"
