#!/usr/bin/env bash
set -euo pipefail

# Script: extract_version.sh
# Description: Extracts version from branch name and validates against version.py
# Usage: extract_version.sh <branch_name>

# Color codes for output
RED='\033[0;31m'
GREEN='\033[0;32m'
NC='\033[0m' # No Color

# Display usage information
usage() {
    echo "Usage: $0 <branch_name>"
    echo ""
    echo "Extracts version from release branch name and validates it matches version.py."
    echo ""
    echo "Examples:"
    echo "  $0 release/1.2.3"
    echo "  $0 release/2.0.0-rc.1"
    exit 1
}

# Check if branch name argument is provided
if [ $# -eq 0 ]; then
    echo -e "${RED}Error: No branch name specified${NC}"
    usage
fi

BRANCH_NAME="$1"

# Extract version from branch name (release/1.2.3 -> 1.2.3)
VERSION="${BRANCH_NAME#release/}"
echo "Branch version: $VERSION"

# Read version from version.py
if [ ! -f "src/version.py" ]; then
    echo -e "${RED}Error: src/version.py not found${NC}"
    exit 1
fi

FILE_VERSION=$(grep -oP '__version__ = "\K[^"]+' src/version.py)
echo "File version: $FILE_VERSION"

# Verify they match
if [ "$VERSION" != "$FILE_VERSION" ]; then
    echo -e "${RED}Error: Branch version ($VERSION) does not match version.py ($FILE_VERSION)${NC}"
    exit 1
fi

echo -e "${GREEN}✓ Version match validated: $VERSION${NC}"

# Output to GITHUB_OUTPUT if available
if [ -n "${GITHUB_OUTPUT:-}" ]; then
    echo "version=$VERSION" >> "$GITHUB_OUTPUT"
fi

exit 0
