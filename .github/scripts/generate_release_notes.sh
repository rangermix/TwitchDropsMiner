#!/bin/bash
set -e

# Script to auto-generate release notes using Gemini AI
# Usage: ./generate_release_notes.sh <version> <gemini_api_key>

VERSION="$1"
GEMINI_API_KEY="$2"

if [ -z "$VERSION" ]; then
  echo "❌ Error: Version argument required"
  echo "Usage: $0 <version> <gemini_api_key>"
  exit 1
fi

if [ -z "$GEMINI_API_KEY" ]; then
  echo "❌ Error: GEMINI_API_KEY argument required"
  echo "Usage: $0 <version> <gemini_api_key>"
  exit 1
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

if [ "$NEEDS_GENERATION" = "true" ]; then
  echo "🤖 Generating release notes using Gemini AI..."

  # Get commit history since last tag
  if ! PREV_TAG=$(git describe --tags $(git rev-list --tags --max-count=1) 2>/dev/null); then
    echo "❌ Error: No git tags found. Something went wrong."
    exit 1
  fi

  echo "Previous tag found: $PREV_TAG"
  # verify PREV_TAG is semver-like (allow prerelease/build metadata, e.g. v1.2.3-beta.1+exp.sha.5114f85)
  if ! [[ "$PREV_TAG" =~ ^v[0-9]+\.[0-9]+\.[0-9]+(-[a-zA-Z0-9]+(\.[a-zA-Z0-9]+)*)?(\+[a-zA-Z0-9]+(\.[a-zA-Z0-9]+)*)?$ ]]; then
    echo "❌ Error: Previous tag '$PREV_TAG' is not in semver format (vX.Y.Z)"
    exit 1
  fi
  echo "Previous tag '$PREV_TAG' is in valid semver format."
  COMMITS=$(git log $PREV_TAG..HEAD --pretty=format:"- %s (%h)" | sed 's/"/\\"/g' | sed ':a;N;$!ba;s/\n/\\n/g')

  # Prepare the prompt
  PROMPT="You are a technical writer creating user-friendly release notes for Twitch Drops Miner, an application that automatically mines Twitch drops.

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

  # Write to RELEASE_NOTES.md
  if [ -f "RELEASE_NOTES.md" ]; then
    # Prepend to existing file
    echo -e "$GENERATED_NOTES\n\n$(cat RELEASE_NOTES.md)" > RELEASE_NOTES.md
  else
    # Create new file
    echo "$GENERATED_NOTES" > RELEASE_NOTES.md
  fi

  # Commit the generated release notes
  # git config user.name "github-actions[bot]"
  # git config user.email "github-actions[bot]@users.noreply.github.com"
  # git add RELEASE_NOTES.md
  # git commit -m "docs: auto-generate release notes for v$VERSION"
  # git push origin HEAD

  echo "✅ Generated and committed release notes for v$VERSION"
else
  echo "✅ Release notes already exist for v$VERSION"
fi
