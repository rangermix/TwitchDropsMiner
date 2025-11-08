#!/usr/bin/env bash
set -euo pipefail

# Script: extract_version.sh
# Description: Extracts and validates version from branch name and/or project files
# Usage: extract_version.sh [branch_name]

# Color codes for output
RED='\033[0;31m'
GREEN='\033[0;32m'
NC='\033[0m' # No Color

# Display usage information
usage() {
    echo "Usage: $0 [branch_name]"
    echo ""
    echo "Mode 1 (with branch name): Validates version from branch matches src/version.py and pyproject.toml"
    echo "Mode 2 (no branch name): Validates src/version.py and pyproject.toml versions match"
    echo ""
    echo "Examples:"
    echo "  $0 release/1.2.3          # Validate branch version matches files"
    echo "  $0 release/2.0.0-rc.1     # Validate pre-release branch version"
    echo "  $0                        # Validate version.py and pyproject.toml match"
    exit 1
}

# Read version from version.py
if [ ! -f "src/version.py" ]; then
    echo -e "${RED}Error: src/version.py not found${NC}"
    exit 1
fi

VERSION_PY=$(grep -oP '__version__ = "\K[^"]+' src/version.py)
echo "src/version.py version: $VERSION_PY"

# Read version from pyproject.toml
if [ ! -f "pyproject.toml" ]; then
    echo -e "${RED}Error: pyproject.toml not found${NC}"
    exit 1
fi

VERSION_TOML=$(grep -oP '^version = "\K[^"]+' pyproject.toml)
echo "pyproject.toml version: $VERSION_TOML"

# Check if branch name argument is provided
if [ $# -eq 0 ]; then
    # Mode 2: No branch name - just validate files match
    echo ""
    echo "No branch name provided - validating file versions match..."

    if [ "$VERSION_PY" != "$VERSION_TOML" ]; then
        echo -e "${RED}Error: Version mismatch between files:${NC}"
        echo -e "${RED}  src/version.py:   $VERSION_PY${NC}"
        echo -e "${RED}  pyproject.toml:   $VERSION_TOML${NC}"
        exit 1
    fi

    echo -e "${GREEN}✓ Version files match: $VERSION_PY${NC}"
    VERSION="$VERSION_PY"
else
    # Mode 1: Branch name provided - validate all three
    BRANCH_NAME="$1"

    # Extract version from branch name (release/1.2.3 -> 1.2.3)
    # Also strip refs/heads/ if present (from github.ref)
    BRANCH_VERSION="${BRANCH_NAME#refs/heads/}"
    BRANCH_VERSION="${BRANCH_VERSION#release/}"
    echo "Branch version: $BRANCH_VERSION"
    echo ""

    # Check all three versions match
    if [ "$BRANCH_VERSION" != "$VERSION_PY" ] || [ "$BRANCH_VERSION" != "$VERSION_TOML" ]; then
        echo -e "${RED}Error: Version mismatch detected:${NC}"
        echo -e "${RED}  Branch:           $BRANCH_VERSION${NC}"
        echo -e "${RED}  src/version.py:   $VERSION_PY${NC}"
        echo -e "${RED}  pyproject.toml:   $VERSION_TOML${NC}"
        exit 1
    fi

    echo -e "${GREEN}✓ All versions match: $BRANCH_VERSION${NC}"
    VERSION="$BRANCH_VERSION"
fi

# Output to GITHUB_OUTPUT if available
if [ -n "${GITHUB_OUTPUT:-}" ]; then
    echo "version=$VERSION" >> "$GITHUB_OUTPUT"
    if echo "$VERSION" | grep -q '-'; then
        echo "is_prerelease=true" >> "$GITHUB_OUTPUT"
    else
        echo "is_prerelease=false" >> "$GITHUB_OUTPUT"
    fi
fi

exit 0
