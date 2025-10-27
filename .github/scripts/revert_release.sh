#!/bin/bash

# revert_release.sh - Revert a failed version release
# Usage: revert_release.sh <version>
#
# Arguments:
#   version: The semver version to revert (e.g., 1.2.3 or 2.0.0-rc.1)
#
# This script:
#   1. Validates the version format
#   2. Checks what resources exist (tag, branch, GitHub release)
#   3. Extracts the previous version from git history
#   4. Shows a summary and asks for confirmation
#   5. Deletes the tag, branch, and GitHub release
#   6. Reverts version.py and pyproject.toml to the previous version
#   7. Commits and pushes the revert to main branch
#
# Requirements:
#   - gh CLI installed and authenticated
#   - Git repository with proper remotes configured
#   - Write access to the repository

set -euo pipefail

# Color codes for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Script directory for accessing sibling scripts
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Display usage information
usage() {
    echo "Usage: $0 <version>"
    echo ""
    echo "Reverts a failed version release by:"
    echo "  - Deleting the GitHub release"
    echo "  - Deleting the git tag (local and remote)"
    echo "  - Deleting the release branch (local and remote)"
    echo "  - Reverting version files to the previous version"
    echo "  - Committing and pushing the revert to main branch"
    echo ""
    echo "Arguments:"
    echo "  version: The semver version to revert (e.g., 1.2.3 or 2.0.0-rc.1)"
    echo ""
    echo "Examples:"
    echo "  $0 1.2.3          # Revert version 1.2.3"
    echo "  $0 2.0.0-rc.1     # Revert pre-release 2.0.0-rc.1"
    exit 1
}

# Check if version argument is provided
if [ "$#" -ne 1 ]; then
    echo -e "${RED}Error: Version argument required${NC}"
    usage
fi

VERSION="$1"
TAG_NAME="v$VERSION"
BRANCH_NAME="release/$VERSION"

echo -e "${BLUE}=====================================${NC}"
echo -e "${BLUE}Version Release Revert Tool${NC}"
echo -e "${BLUE}=====================================${NC}"
echo ""

# Step 1: Validate version format
echo -e "${YELLOW}[1/7] Validating version format...${NC}"
if ! "$SCRIPT_DIR/validate_semver.sh" "$VERSION" &>/dev/null; then
    echo -e "${RED}Error: Invalid version format: $VERSION${NC}"
    echo -e "${RED}Expected semver format (e.g., 1.2.3 or 2.0.0-rc.1)${NC}"
    exit 1
fi
echo -e "${GREEN}✓ Version format is valid${NC}"
echo ""

# Step 2: Check if gh CLI is installed and authenticated
echo -e "${YELLOW}[2/7] Checking GitHub CLI...${NC}"
if ! command -v gh &> /dev/null; then
    echo -e "${RED}Error: gh CLI is not installed${NC}"
    echo -e "${RED}Please install it from: https://cli.github.com/${NC}"
    exit 1
fi

if ! gh auth status &>/dev/null; then
    echo -e "${RED}Error: Not authenticated with GitHub${NC}"
    echo -e "${RED}Please run: gh auth login${NC}"
    exit 1
fi
echo -e "${GREEN}✓ GitHub CLI is ready${NC}"
echo ""

# Step 3: Check what resources exist
echo -e "${YELLOW}[3/7] Checking existing resources...${NC}"

# Check if tag exists locally
TAG_EXISTS_LOCAL=false
if git rev-parse "$TAG_NAME" &>/dev/null; then
    TAG_EXISTS_LOCAL=true
    echo -e "${GREEN}✓ Found local tag: $TAG_NAME${NC}"
else
    echo -e "${YELLOW}  Local tag not found: $TAG_NAME${NC}"
fi

# Check if tag exists remotely
TAG_EXISTS_REMOTE=false
if git ls-remote --tags origin | grep -q "refs/tags/$TAG_NAME$"; then
    TAG_EXISTS_REMOTE=true
    echo -e "${GREEN}✓ Found remote tag: $TAG_NAME${NC}"
else
    echo -e "${YELLOW}  Remote tag not found: $TAG_NAME${NC}"
fi

# Check if branch exists locally
BRANCH_EXISTS_LOCAL=false
if git rev-parse --verify "$BRANCH_NAME" &>/dev/null; then
    BRANCH_EXISTS_LOCAL=true
    echo -e "${GREEN}✓ Found local branch: $BRANCH_NAME${NC}"
else
    echo -e "${YELLOW}  Local branch not found: $BRANCH_NAME${NC}"
fi

# Check if branch exists remotely
BRANCH_EXISTS_REMOTE=false
if git ls-remote --heads origin | grep -q "refs/heads/$BRANCH_NAME$"; then
    BRANCH_EXISTS_REMOTE=true
    echo -e "${GREEN}✓ Found remote branch: $BRANCH_NAME${NC}"
else
    echo -e "${YELLOW}  Remote branch not found: $BRANCH_NAME${NC}"
fi

# Check if GitHub release exists
RELEASE_EXISTS=false
if gh release view "$TAG_NAME" &>/dev/null; then
    RELEASE_EXISTS=true
    echo -e "${GREEN}✓ Found GitHub release: $TAG_NAME${NC}"
else
    echo -e "${YELLOW}  GitHub release not found: $TAG_NAME${NC}"
fi

echo ""

# If nothing exists, exit
if [ "$TAG_EXISTS_LOCAL" = false ] && [ "$TAG_EXISTS_REMOTE" = false ] && \
   [ "$BRANCH_EXISTS_LOCAL" = false ] && [ "$BRANCH_EXISTS_REMOTE" = false ] && \
   [ "$RELEASE_EXISTS" = false ]; then
    echo -e "${RED}Error: No resources found for version $VERSION${NC}"
    echo -e "${RED}Nothing to revert.${NC}"
    exit 1
fi

# Step 4: Extract previous version from git history
echo -e "${YELLOW}[4/7] Extracting previous version from git history...${NC}"

# Get the git history of version.py, find the commit before the version we're reverting
# We need to find the commit that changed version to $VERSION, then get the version from the commit before that
CURRENT_VERSION_COMMIT=$(git log --all --format="%H" --follow -S "__version__ = \"$VERSION\"" -- src/version.py | head -1)

if [ -z "$CURRENT_VERSION_COMMIT" ]; then
    echo -e "${RED}Error: Could not find commit that set version to $VERSION${NC}"
    exit 1
fi

echo "  Found version $VERSION in commit: ${CURRENT_VERSION_COMMIT:0:7}"

# Get the previous commit for version.py
PREVIOUS_VERSION_COMMIT=$(git log --all --format="%H" --follow "$CURRENT_VERSION_COMMIT^..HEAD" -- src/version.py | grep -A1 "$CURRENT_VERSION_COMMIT" | tail -1 || true)

# If we can't find a previous commit in that way, try getting the parent commit and extracting version from there
if [ -z "$PREVIOUS_VERSION_COMMIT" ] || [ "$PREVIOUS_VERSION_COMMIT" = "$CURRENT_VERSION_COMMIT" ]; then
    PREVIOUS_VERSION_COMMIT=$(git rev-parse "$CURRENT_VERSION_COMMIT^" 2>/dev/null || true)
fi

if [ -z "$PREVIOUS_VERSION_COMMIT" ]; then
    echo -e "${RED}Error: Could not find previous commit for version.py${NC}"
    exit 1
fi

echo "  Found previous commit: ${PREVIOUS_VERSION_COMMIT:0:7}"

# Extract version from that commit
PREVIOUS_VERSION=$(git show "$PREVIOUS_VERSION_COMMIT:src/version.py" | grep -oP '__version__ = "\K[^"]+' || true)

if [ -z "$PREVIOUS_VERSION" ]; then
    echo -e "${RED}Error: Could not extract previous version from commit ${PREVIOUS_VERSION_COMMIT:0:7}${NC}"
    exit 1
fi

echo -e "${GREEN}✓ Previous version: $PREVIOUS_VERSION${NC}"
echo ""

# Step 5: Show summary and ask for confirmation
echo -e "${BLUE}=====================================${NC}"
echo -e "${BLUE}Revert Summary${NC}"
echo -e "${BLUE}=====================================${NC}"
echo ""
echo -e "${YELLOW}Version to revert:${NC} $VERSION"
echo -e "${YELLOW}Previous version:${NC}  $PREVIOUS_VERSION"
echo ""
echo -e "${YELLOW}The following actions will be performed:${NC}"
echo ""

ACTION_COUNT=0

if [ "$RELEASE_EXISTS" = true ]; then
    echo "  • Delete GitHub release: $TAG_NAME"
    ((ACTION_COUNT++))
fi

if [ "$TAG_EXISTS_REMOTE" = true ]; then
    echo "  • Delete remote tag: $TAG_NAME"
    ((ACTION_COUNT++))
fi

if [ "$TAG_EXISTS_LOCAL" = true ]; then
    echo "  • Delete local tag: $TAG_NAME"
    ((ACTION_COUNT++))
fi

if [ "$BRANCH_EXISTS_REMOTE" = true ]; then
    echo "  • Delete remote branch: $BRANCH_NAME"
    ((ACTION_COUNT++))
fi

if [ "$BRANCH_EXISTS_LOCAL" = true ]; then
    echo "  • Delete local branch: $BRANCH_NAME"
    ((ACTION_COUNT++))
fi

echo "  • Update src/version.py: $VERSION → $PREVIOUS_VERSION"
echo "  • Update pyproject.toml: $VERSION → $PREVIOUS_VERSION"
echo "  • Commit changes to main branch with message:"
echo "    'chore: revert version from $VERSION to $PREVIOUS_VERSION'"
echo "  • Push changes to origin/main"
((ACTION_COUNT+=3))

echo ""
echo -e "${RED}Total actions: $ACTION_COUNT${NC}"
echo ""
echo -e "${YELLOW}This operation cannot be easily undone.${NC}"
echo -n -e "${YELLOW}Type 'yes' to confirm: ${NC}"

read -r CONFIRMATION

if [ "$CONFIRMATION" != "yes" ]; then
    echo -e "${RED}Aborted.${NC}"
    exit 0
fi

echo ""
echo -e "${GREEN}Confirmed. Proceeding with revert...${NC}"
echo ""

# Step 6: Perform revert operations
echo -e "${YELLOW}[5/7] Deleting release resources...${NC}"

# Delete GitHub release
if [ "$RELEASE_EXISTS" = true ]; then
    echo "  Deleting GitHub release $TAG_NAME..."
    if gh release delete "$TAG_NAME" --yes; then
        echo -e "${GREEN}✓ Deleted GitHub release${NC}"
    else
        echo -e "${RED}✗ Failed to delete GitHub release${NC}"
    fi
fi

# Delete remote tag
if [ "$TAG_EXISTS_REMOTE" = true ]; then
    echo "  Deleting remote tag $TAG_NAME..."
    if git push --delete origin "$TAG_NAME" 2>/dev/null; then
        echo -e "${GREEN}✓ Deleted remote tag${NC}"
    else
        echo -e "${RED}✗ Failed to delete remote tag (may have been deleted by release deletion)${NC}"
    fi
fi

# Delete local tag
if [ "$TAG_EXISTS_LOCAL" = true ]; then
    echo "  Deleting local tag $TAG_NAME..."
    if git tag -d "$TAG_NAME"; then
        echo -e "${GREEN}✓ Deleted local tag${NC}"
    else
        echo -e "${RED}✗ Failed to delete local tag${NC}"
    fi
fi

# Delete remote branch
if [ "$BRANCH_EXISTS_REMOTE" = true ]; then
    echo "  Deleting remote branch $BRANCH_NAME..."
    if git push --delete origin "$BRANCH_NAME"; then
        echo -e "${GREEN}✓ Deleted remote branch${NC}"
    else
        echo -e "${RED}✗ Failed to delete remote branch${NC}"
    fi
fi

# Delete local branch (only if we're not on it)
if [ "$BRANCH_EXISTS_LOCAL" = true ]; then
    CURRENT_BRANCH=$(git rev-parse --abbrev-ref HEAD)
    if [ "$CURRENT_BRANCH" = "$BRANCH_NAME" ]; then
        echo "  Switching from $BRANCH_NAME to main..."
        git checkout main
    fi
    echo "  Deleting local branch $BRANCH_NAME..."
    if git branch -D "$BRANCH_NAME"; then
        echo -e "${GREEN}✓ Deleted local branch${NC}"
    else
        echo -e "${RED}✗ Failed to delete local branch${NC}"
    fi
fi

echo ""

# Step 7: Update version files and commit
echo -e "${YELLOW}[6/7] Updating version files...${NC}"

# Make sure we're on main branch
CURRENT_BRANCH=$(git rev-parse --abbrev-ref HEAD)
if [ "$CURRENT_BRANCH" != "main" ]; then
    echo "  Switching to main branch..."
    git checkout main
    git pull origin main
fi

# Configure git
git config user.name "github-actions[bot]" || true
git config user.email "github-actions[bot]@users.noreply.github.com" || true

# Update version.py
echo "  Updating src/version.py..."
echo "__version__ = \"$PREVIOUS_VERSION\"" > src/version.py
echo -e "${GREEN}✓ Updated src/version.py${NC}"

# Update pyproject.toml
echo "  Updating pyproject.toml..."
sed -i "s/^version = \"[^\"]*\"\(.*\)/version = \"$PREVIOUS_VERSION\"\1/" pyproject.toml
echo -e "${GREEN}✓ Updated pyproject.toml${NC}"

echo ""

# Step 8: Commit and push
echo -e "${YELLOW}[7/7] Committing and pushing changes...${NC}"

git add src/version.py pyproject.toml

if git diff --cached --quiet; then
    echo -e "${YELLOW}  No changes to commit (version may already be reverted)${NC}"
else
    echo "  Creating commit..."
    git commit -m "chore: revert version from $VERSION to $PREVIOUS_VERSION"
    echo -e "${GREEN}✓ Created commit${NC}"

    echo "  Pushing to origin/main..."
    git push origin main
    echo -e "${GREEN}✓ Pushed changes${NC}"
fi

echo ""
echo -e "${BLUE}=====================================${NC}"
echo -e "${GREEN}✅ Revert Complete!${NC}"
echo -e "${BLUE}=====================================${NC}"
echo ""
echo -e "${GREEN}Successfully reverted version from $VERSION to $PREVIOUS_VERSION${NC}"
echo ""
echo "Summary:"
[ "$RELEASE_EXISTS" = true ] && echo "  ✓ Deleted GitHub release"
[ "$TAG_EXISTS_REMOTE" = true ] && echo "  ✓ Deleted remote tag"
[ "$TAG_EXISTS_LOCAL" = true ] && echo "  ✓ Deleted local tag"
[ "$BRANCH_EXISTS_REMOTE" = true ] && echo "  ✓ Deleted remote branch"
[ "$BRANCH_EXISTS_LOCAL" = true ] && echo "  ✓ Deleted local branch"
echo "  ✓ Reverted version files"
echo "  ✓ Committed and pushed to main"
echo ""
