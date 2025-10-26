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
    echo "Usage: $0 <version>"
    echo ""
    echo "Validates a version string against SemVer specification."
    echo ""
    echo "Examples:"
    echo "  $0 1.2.3"
    echo "  $0 2.0.0-rc.1"
    echo "  $0 1.0.0-beta.2+build.123"
    exit 1
}

# Check if version argument is provided
if [ $# -eq 0 ]; then
    echo -e "${RED}Error: No version specified${NC}"
    usage
fi

VERSION="$1"

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
        echo "release_type=prerelease" >> "$GITHUB_OUTPUT"
    else
        echo "release_type=stable" >> "$GITHUB_OUTPUT"
    fi
fi

exit 0
