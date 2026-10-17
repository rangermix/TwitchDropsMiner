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
#   5. Prepares, validates, and pushes the version rollback
#   6. Deletes the tag, branch, and GitHub release
#
# Requirements:
#   - gh CLI installed and authenticated
#   - uv installed
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

# Step 2: Check prerequisites and refresh the repository before inspecting
# release history. This prevents a stale clone from selecting the wrong
# previous version.
echo -e "${YELLOW}[2/7] Checking prerequisites and refreshing main...${NC}"
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

if ! command -v uv &> /dev/null; then
    echo -e "${RED}Error: uv is not installed${NC}"
    echo -e "${RED}Please install it from: https://docs.astral.sh/uv/${NC}"
    exit 1
fi
echo -e "${GREEN}✓ uv is ready${NC}"

if [ -n "$(git status --porcelain)" ]; then
    echo -e "${RED}Error: Working tree must be clean before reverting a release${NC}"
    exit 1
fi

CURRENT_BRANCH=$(git rev-parse --abbrev-ref HEAD)
if [ "$CURRENT_BRANCH" != "main" ]; then
    echo "  Switching to main branch..."
    git checkout main
fi
git pull --ff-only origin main
git fetch --tags origin
echo -e "${GREEN}✓ Repository history is current${NC}"
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

# Use the get_previous_version.sh script to extract the previous version
if ! PREVIOUS_VERSION=$("$SCRIPT_DIR/get_previous_version.sh" "$VERSION" 2>&1); then
    echo -e "${RED}Error: Failed to extract previous version${NC}"
    echo -e "${RED}$PREVIOUS_VERSION${NC}"
    exit 1
fi

echo -e "${GREEN}✓ Previous version: $PREVIOUS_VERSION${NC}"

# Validate current metadata and reject attempts to roll back an older release.
"$SCRIPT_DIR/extract_version.sh" >/dev/null
CURRENT_VERSION=$(grep -oP '__version__ = "\K[^"]+' src/version.py)
if [ "$CURRENT_VERSION" = "$VERSION" ]; then
    ROLLBACK_REQUIRED=true
elif [ "$CURRENT_VERSION" = "$PREVIOUS_VERSION" ]; then
    ROLLBACK_REQUIRED=false
    echo -e "${YELLOW}  Main is already at $PREVIOUS_VERSION; cleanup-only retry${NC}"
else
    echo -e "${RED}Error: main is at $CURRENT_VERSION, not $VERSION or $PREVIOUS_VERSION${NC}"
    echo -e "${RED}Refusing to revert a non-current historical release${NC}"
    exit 1
fi
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
    ACTION_COUNT=$((ACTION_COUNT + 1))
fi

if [ "$TAG_EXISTS_REMOTE" = true ]; then
    echo "  • Delete remote tag: $TAG_NAME"
    ACTION_COUNT=$((ACTION_COUNT + 1))
fi

if [ "$TAG_EXISTS_LOCAL" = true ]; then
    echo "  • Delete local tag: $TAG_NAME"
    ACTION_COUNT=$((ACTION_COUNT + 1))
fi

if [ "$BRANCH_EXISTS_REMOTE" = true ]; then
    echo "  • Delete remote branch: $BRANCH_NAME"
    ACTION_COUNT=$((ACTION_COUNT + 1))
fi

if [ "$BRANCH_EXISTS_LOCAL" = true ]; then
    echo "  • Delete local branch: $BRANCH_NAME"
    ACTION_COUNT=$((ACTION_COUNT + 1))
fi

if [ "$ROLLBACK_REQUIRED" = true ]; then
    echo "  • Update src/version.py: $VERSION → $PREVIOUS_VERSION"
    echo "  • Update pyproject.toml: $VERSION → $PREVIOUS_VERSION"
    echo "  • Regenerate uv.lock for $PREVIOUS_VERSION"
    echo "  • Commit changes to main branch with message:"
    echo "    'chore: revert version from $VERSION to $PREVIOUS_VERSION'"
    echo "  • Push changes to origin/main"
    ACTION_COUNT=$((ACTION_COUNT + 3))
else
    echo "  • Keep main at its already-restored version: $PREVIOUS_VERSION"
fi

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

# Step 6: Prepare, validate, and publish the version rollback before deleting
# release resources. If any local preparation or push fails, the published
# release remains intact and can still be recovered or retried safely.
echo -e "${YELLOW}[5/7] Preparing version rollback...${NC}"

if [ "$ROLLBACK_REQUIRED" = true ]; then
    git config user.name "github-actions[bot]" || true
    git config user.email "github-actions[bot]@users.noreply.github.com" || true

    echo "  Updating src/version.py..."
    echo "__version__ = \"$PREVIOUS_VERSION\"" > src/version.py
    echo -e "${GREEN}✓ Updated src/version.py${NC}"

    echo "  Updating pyproject.toml..."
    sed -i "s/^version = \"[^\"]*\"\(.*\)/version = \"$PREVIOUS_VERSION\"\1/" pyproject.toml
    echo -e "${GREEN}✓ Updated pyproject.toml${NC}"

    echo "  Updating uv.lock..."
    uv lock
    "$SCRIPT_DIR/extract_version.sh"
    echo -e "${GREEN}✓ Updated uv.lock${NC}"

    git add src/version.py pyproject.toml uv.lock
    echo "  Creating rollback commit..."
    git commit -m "chore: revert version from $VERSION to $PREVIOUS_VERSION"
    echo -e "${GREEN}✓ Created rollback commit${NC}"

    echo "  Pushing rollback to origin/main..."
    git push origin main
    echo -e "${GREEN}✓ Published rollback${NC}"
else
    echo -e "${GREEN}✓ Main already advertises $PREVIOUS_VERSION${NC}"
fi
echo ""

# Step 7: Delete release resources only after the rollback is published.
echo -e "${YELLOW}[6/7] Deleting release resources...${NC}"

CLEANUP_FAILED=false
RELEASE_DELETE_OK=true
REMOTE_TAG_DELETE_OK=true
LOCAL_TAG_DELETE_OK=true
REMOTE_BRANCH_DELETE_OK=true
LOCAL_BRANCH_DELETE_OK=true

# Delete GitHub release
if [ "$RELEASE_EXISTS" = true ]; then
    echo "  Deleting GitHub release $TAG_NAME..."
    if gh release delete "$TAG_NAME" --yes; then
        echo -e "${GREEN}✓ Deleted GitHub release${NC}"
    else
        echo -e "${RED}✗ Failed to delete GitHub release${NC}"
        RELEASE_DELETE_OK=false
        CLEANUP_FAILED=true
    fi
fi

# Delete remote tag
if [ "$TAG_EXISTS_REMOTE" = true ]; then
    echo "  Deleting remote tag $TAG_NAME..."
    if git push --delete origin "$TAG_NAME" 2>/dev/null; then
        echo -e "${GREEN}✓ Deleted remote tag${NC}"
    else
        echo -e "${RED}✗ Failed to delete remote tag (may have been deleted by release deletion)${NC}"
        REMOTE_TAG_DELETE_OK=false
        CLEANUP_FAILED=true
    fi
fi

# Delete local tag
if [ "$TAG_EXISTS_LOCAL" = true ]; then
    echo "  Deleting local tag $TAG_NAME..."
    if git tag -d "$TAG_NAME"; then
        echo -e "${GREEN}✓ Deleted local tag${NC}"
    else
        echo -e "${RED}✗ Failed to delete local tag${NC}"
        LOCAL_TAG_DELETE_OK=false
        CLEANUP_FAILED=true
    fi
fi

# Delete remote branch
if [ "$BRANCH_EXISTS_REMOTE" = true ]; then
    echo "  Deleting remote branch $BRANCH_NAME..."
    if git push --delete origin "$BRANCH_NAME"; then
        echo -e "${GREEN}✓ Deleted remote branch${NC}"
    else
        echo -e "${RED}✗ Failed to delete remote branch${NC}"
        REMOTE_BRANCH_DELETE_OK=false
        CLEANUP_FAILED=true
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
        LOCAL_BRANCH_DELETE_OK=false
        CLEANUP_FAILED=true
    fi
fi

echo ""

# Step 8: Report completion
echo -e "${YELLOW}[7/7] Finalizing...${NC}"
echo -e "${BLUE}=====================================${NC}"
if [ "$CLEANUP_FAILED" = true ]; then
    echo -e "${RED}Revert published with cleanup errors${NC}"
else
    echo -e "${GREEN}Revert Complete!${NC}"
fi
echo -e "${BLUE}=====================================${NC}"
echo ""
if [ "$ROLLBACK_REQUIRED" = true ]; then
    echo -e "${GREEN}Reverted version from $VERSION to $PREVIOUS_VERSION${NC}"
else
    echo -e "${GREEN}Main was already at $PREVIOUS_VERSION${NC}"
fi
echo ""
echo "Summary:"
[ "$RELEASE_EXISTS" = true ] && [ "$RELEASE_DELETE_OK" = true ] && echo "  ✓ Deleted GitHub release"
[ "$TAG_EXISTS_REMOTE" = true ] && [ "$REMOTE_TAG_DELETE_OK" = true ] && echo "  ✓ Deleted remote tag"
[ "$TAG_EXISTS_LOCAL" = true ] && [ "$LOCAL_TAG_DELETE_OK" = true ] && echo "  ✓ Deleted local tag"
[ "$BRANCH_EXISTS_REMOTE" = true ] && [ "$REMOTE_BRANCH_DELETE_OK" = true ] && echo "  ✓ Deleted remote branch"
[ "$BRANCH_EXISTS_LOCAL" = true ] && [ "$LOCAL_BRANCH_DELETE_OK" = true ] && echo "  ✓ Deleted local branch"
[ "$ROLLBACK_REQUIRED" = true ] && echo "  ✓ Reverted, committed, and pushed version files"
echo ""

if [ "$CLEANUP_FAILED" = true ]; then
    echo -e "${RED}One or more release resources could not be deleted. Retry cleanup before continuing.${NC}"
    exit 1
fi
