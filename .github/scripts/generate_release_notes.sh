#!/bin/bash
set -e

# Script to auto-generate release notes using Gemini AI
# Usage: ./generate_release_notes.sh -v <version> -k <gemini_api_key> [-p]
# -p: production mode (default is dry run)

VERSION=""
GEMINI_API_KEY=""
DRY_RUN="true"

while getopts 'v:k:p' flag; do
  case "${flag}" in
    v) VERSION="$OPTARG" ;;
    k) GEMINI_API_KEY="$OPTARG" ;;
    p) DRY_RUN="false" ;;
    *) 
       echo "❌ Error: Unknown option -${flag}"
       echo "Usage: $0 -v <version> -k <gemini_api_key> [-p]"
       echo "  -p: production mode (default is dry run)"
       exit 1 ;;
  esac
done

if [ -z "$VERSION" ]; then
  echo "❌ Error: Version argument required"
  echo "Usage: $0 -v <version> -k <gemini_api_key> [-p]"
  exit 1
fi

if [ -z "$GEMINI_API_KEY" ]; then
  echo "❌ Error: GEMINI_API_KEY argument required"
  echo "Usage: $0 -v <version> -k <gemini_api_key> [-p]"
  exit 1
fi

# check if dry run
if [ "$DRY_RUN" = "true" ]; then
  echo "🔍 Running in dry run mode. No files will be modified."
# are we in CI? check CI env var
elif [ "$CI" = "true" ]; then
  echo "⚠️ Running in CI production mode. RELEASE_NOTES.md will be updated if needed."
else
  echo "🚨🚨 DANGEROUS 🚨🚨"
  echo "Running in non-CI production mode. RELEASE_NOTES.md will be updated if needed."
  echo "Make sure you know what you're doing!"
  sleep 2
  # need second confirmation
  read -r -p "Type 'CONFIRM' to proceed: " CONFIRMATION
  if [ "$CONFIRMATION" != "CONFIRM" ]; then
    echo "❌ Aborting."
    exit 1
  else
    echo "✅ Confirmation received. Proceeding..."
  fi
fi

# Check if release notes exist for this version
NEEDS_GENERATION=false

if [ ! -f "RELEASE_NOTES.md" ]; then
  echo "RELEASE_NOTES.md not found, will generate"
  NEEDS_GENERATION=true
else
  # Check if version exists in the file
  if ! grep -q "# Release Notes - v$VERSION" RELEASE_NOTES.md; then
    echo "Version $VERSION not found in RELEASE_NOTES.md, will generate"
    NEEDS_GENERATION=true
  fi
fi

if [ "$NEEDS_GENERATION" = "false" ]; then
  echo "✅ Release notes already exist for v$VERSION"
  exit 0
fi

echo "🤖 Generating release notes using Gemini AI..."

# Get previous version from history of src/version.py
LAST_VERSION=$(git show HEAD^:src/version.py | sed -n 's/^__version__ = "\([^"]*\)"/\1/p')
echo "Last version from git history of src/version.py: $LAST_VERSION"

# Get commit history since last version tag
echo "Looking for last version tag: v$LAST_VERSION"
PREV_TAG="v$LAST_VERSION"

if ! git rev-parse "$PREV_TAG" >/dev/null 2>&1; then
  echo "❌ Error: Could not find previous tag for $PREV_TAG"
  exit 1
fi

echo "✅ Previous tag found: $PREV_TAG"
echo "Collecting commits since $PREV_TAG..."
COMMITS=$(git log $PREV_TAG..HEAD --pretty=format:"- %h%n%w(0,2,2)%B" | sed 's/"/\\"/g' | sed ':a;N;$!ba;s/\n/\\n/g')
echo "✅ Collected $(echo "$COMMITS" | egrep -o -e "- \w{7}\\\\n" | wc -l) commits since $PREV_TAG"

# Prepare the prompt
PROMPT=\
"You are a technical writer creating user-friendly release notes for Twitch Drops Miner, an application that automatically mines Twitch drops.

Based on the following git commits, create release notes in this EXACT format:

# Release Notes - v$VERSION

[Overall summary of the release in 1-2 sentences, highlighting key improvements and new features.]

[Organize changes into relevant sections with emojis, such as:]
### 🌍 [Feature Category Name]
[User-friendly description of the feature, what it does, and why users will like it]

### 🎮 [Another Feature Category if applicable]
- **Bold Feature Name**: Description
- **Another Feature**: Description

### 🐛 Bug Fixes
- **Issue Description**: What was fixed and how it helps users
- **Another Issue**: Description

### 📚 [Other relevant sections]
[Any other improvements worth mentioning, like performance enhancements, UI tweaks, etc.]

Ensure the notes are clear, concise, yet casual, attractive, and helpful for end-users. Use bullet points and emojis to enhance readability.

Git commits:
$COMMITS"

# Call Gemini API
echo "Calling Gemini API..."
RESPONSE=$(curl -s -X POST \
  "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash-preview-09-2025:generateContent?key=$GEMINI_API_KEY" \
  -H "Content-Type: application/json" \
  -d "{
    \"contents\": [{
      \"parts\": [{
        \"text\": $(echo "$PROMPT" | jq -Rs .)
      }]
    }],
    \"generationConfig\": {
      \"temperature\": 0.7,
      \"maxOutputTokens\": 2048
    }
  }")

# Extract the generated text
GENERATED_NOTES=$(echo "$RESPONSE" | jq -r '.candidates[0].content.parts[0].text // empty')

if [ -z "$GENERATED_NOTES" ]; then
  echo "❌ Error: Failed to generate release notes with Gemini AI"
  echo "API Response: $RESPONSE"
  exit 1
fi

# if dry run, just output the notes
if [ "$DRY_RUN" = "true" ]; then
  echo "✅ Dry run mode - generated release notes:"
  echo "$GENERATED_NOTES"
  exit 0
fi

# # Write to RELEASE_NOTES.md
if [ -f "RELEASE_NOTES.md" ]; then
  # Prepend to existing file
  echo -e "$GENERATED_NOTES\n\n$(cat RELEASE_NOTES.md)" > RELEASE_NOTES.md
else
  # Create new file
  echo "$GENERATED_NOTES" > RELEASE_NOTES.md
fi

# # Commit the generated release notes
git config user.name "github-actions[bot]"
git config user.email "github-actions[bot]@users.noreply.github.com"
git add RELEASE_NOTES.md
git commit -m "docs: auto-generate release notes for v$VERSION"
git push origin HEAD

echo "✅ Generated and committed release notes for v$VERSION"
