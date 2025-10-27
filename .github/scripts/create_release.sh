#!/bin/bash

# create_release.sh - Create release branch and tag for version release
# Usage: create_release.sh <version> <current_branch>
#
# Arguments:
#   version: The validated semver version (e.g., 1.2.3 or 2.0.0-rc.1)
#   current_branch: The current branch name to push changes to
#
# This script:
#   1. Updates src/version.py with the new version
#   2. Updates pyproject.toml with the new version
#   3. Commits and pushes changes to current branch
#   4. Creates a new release branch
#   5. Creates and pushes a version tag
#
# Expected to be called from GitHub Actions after version validation

set -euo pipefail

# Check arguments
if [ "$#" -ne 2 ]; then
  echo "Usage: $0 <version> <current_branch>" >&2
  exit 1
fi

VERSION="$1"
CURRENT_BRANCH="$2"

echo "Creating release for version: $VERSION"
echo "Current branch: $CURRENT_BRANCH"

# Configure git
git config user.name "github-actions[bot]"
git config user.email "github-actions[bot]@users.noreply.github.com"

# Update version.py
echo "Updating src/version.py..."
mv -f src/version.py src/last_version.py
echo "__version__ = \"$VERSION\"" > src/version.py

# Update pyproject.toml
echo "Updating pyproject.toml..."
sed -i "s/^version = \"[^\"]*\"\(.*\)/version = \"$VERSION\"\1/" pyproject.toml

# Commit changes
echo "Committing version changes..."
git add src/version.py pyproject.toml
git commit -m "chore: bump version to $VERSION"
git push origin "$CURRENT_BRANCH"

NEW_BRANCH_NAME="release/$VERSION"

# Create and checkout new branch
echo "Creating release branch: $NEW_BRANCH_NAME"
git checkout -b "$NEW_BRANCH_NAME"

# Push branch (this will trigger the publish workflow)
git push origin "$NEW_BRANCH_NAME"

# Create tag
echo "Creating and pushing tag: v$VERSION"
git tag "v$VERSION"
git push origin "v$VERSION"

# Success messages
echo ""
echo "✅ Updated version to $VERSION"
echo "✅ Created branch '$NEW_BRANCH_NAME'"
echo "✅ Created and pushed tag v$VERSION"
echo "✅ The publish workflow will now build Docker images and create the GitHub release"
