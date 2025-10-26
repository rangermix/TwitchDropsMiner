#!/usr/bin/env bash
set -euo pipefail

# Script: update_version.sh
# Description: Updates project version, creates release branch and tag
# Usage: update_version.sh <version> [--skip-push]

# Color codes for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[0;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Script directory
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"

# Display usage information
usage() {
    echo "Usage: $0 <version> [--skip-push]"
    echo ""
    echo "Updates the project version, creates a release branch, and creates a git tag."
    echo ""
    echo "Arguments:"
    echo "  <version>      Version string in SemVer format (e.g., 1.2.3, 2.0.0-rc.1)"
    echo ""
    echo "Options:"
    echo "  --skip-push    Skip pushing changes to remote (useful for local testing)"
    echo ""
    echo "Examples:"
    echo "  $0 1.2.3"
    echo "  $0 2.0.0-rc.1"
    echo "  $0 1.0.0-beta --skip-push"
    exit 1
}

# Check if version argument is provided
if [ $# -eq 0 ]; then
    echo -e "${RED}Error: No version specified${NC}"
    usage
fi

VERSION="$1"
SKIP_PUSH=false

# Parse optional flags
shift
while [ $# -gt 0 ]; do
    case "$1" in
        --skip-push)
            SKIP_PUSH=true
            shift
            ;;
        *)
            echo -e "${RED}Error: Unknown option: $1${NC}"
            usage
            ;;
    esac
done

# Validate version format using validate_semver.sh
echo -e "${BLUE}Validating version format...${NC}"
if ! "$SCRIPT_DIR/validate_semver.sh" "$VERSION" > /dev/null; then
    echo -e "${RED}Version validation failed${NC}"
    exit 1
fi

# Determine current branch
CURRENT_BRANCH=$(git rev-parse --abbrev-ref HEAD)
RELEASE_BRANCH="release/$VERSION"

echo -e "${BLUE}Current branch: $CURRENT_BRANCH${NC}"
echo -e "${BLUE}Target release branch: $RELEASE_BRANCH${NC}"

# Check if release branch already exists
if git show-ref --verify --quiet "refs/heads/$RELEASE_BRANCH"; then
    echo -e "${RED}Error: Branch '$RELEASE_BRANCH' already exists locally${NC}"
    exit 1
fi

# Check if remote branch exists (if we're not skipping push)
if [ "$SKIP_PUSH" = false ]; then
    if git ls-remote --heads origin "$RELEASE_BRANCH" | grep -q "$RELEASE_BRANCH"; then
        echo -e "${RED}Error: Branch '$RELEASE_BRANCH' already exists on remote${NC}"
        exit 1
    fi
fi

# Check if tag already exists
if git rev-parse "v$VERSION" >/dev/null 2>&1; then
    echo -e "${RED}Error: Tag 'v$VERSION' already exists${NC}"
    exit 1
fi

# Update version.py
echo -e "${BLUE}Updating version.py...${NC}"
VERSION_FILE="$PROJECT_ROOT/src/version.py"

if [ ! -f "$VERSION_FILE" ]; then
    echo -e "${RED}Error: Version file not found: $VERSION_FILE${NC}"
    exit 1
fi

# Backup old version
BACKUP_FILE="$PROJECT_ROOT/src/last_version.py"
mv -f "$VERSION_FILE" "$BACKUP_FILE"

# Write new version
echo "__version__ = \"$VERSION\"" > "$VERSION_FILE"

echo -e "${GREEN}✓ Updated version to $VERSION${NC}"

# Commit version change
echo -e "${BLUE}Committing version change...${NC}"
git add "$VERSION_FILE"
git commit -m "chore: bump version to $VERSION"

if [ "$SKIP_PUSH" = false ]; then
    echo -e "${BLUE}Pushing to current branch ($CURRENT_BRANCH)...${NC}"
    git push origin "$CURRENT_BRANCH"
    echo -e "${GREEN}✓ Pushed version update to $CURRENT_BRANCH${NC}"
else
    echo -e "${YELLOW}⊘ Skipping push to current branch${NC}"
fi

# Create release branch
echo -e "${BLUE}Creating release branch '$RELEASE_BRANCH'...${NC}"
git checkout -b "$RELEASE_BRANCH"
echo -e "${GREEN}✓ Created branch '$RELEASE_BRANCH'${NC}"

# Push release branch
if [ "$SKIP_PUSH" = false ]; then
    echo -e "${BLUE}Pushing release branch...${NC}"
    git push origin "$RELEASE_BRANCH"
    echo -e "${GREEN}✓ Pushed branch '$RELEASE_BRANCH'${NC}"
else
    echo -e "${YELLOW}⊘ Skipping push of release branch${NC}"
fi

# Create tag
echo -e "${BLUE}Creating tag 'v$VERSION'...${NC}"
git tag "v$VERSION"
echo -e "${GREEN}✓ Created tag 'v$VERSION'${NC}"

# Push tag
if [ "$SKIP_PUSH" = false ]; then
    echo -e "${BLUE}Pushing tag...${NC}"
    git push origin "v$VERSION"
    echo -e "${GREEN}✓ Pushed tag 'v$VERSION'${NC}"
else
    echo -e "${YELLOW}⊘ Skipping push of tag${NC}"
fi

# Summary
echo ""
echo -e "${GREEN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo -e "${GREEN}✅ Version update completed successfully!${NC}"
echo -e "${GREEN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo -e "${GREEN}  Version:        $VERSION${NC}"
echo -e "${GREEN}  Branch:         $RELEASE_BRANCH${NC}"
echo -e "${GREEN}  Tag:            v$VERSION${NC}"

if [ "$SKIP_PUSH" = false ]; then
    echo -e "${GREEN}  Remote status:  Pushed${NC}"
    echo ""
    echo -e "${BLUE}The publish workflow should now build Docker images and create the GitHub release.${NC}"
else
    echo -e "${YELLOW}  Remote status:  Not pushed (--skip-push)${NC}"
    echo ""
    echo -e "${YELLOW}Note: Changes are only local. Push manually when ready.${NC}"
fi

exit 0
