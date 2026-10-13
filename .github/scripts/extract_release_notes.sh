#!/bin/bash
set -e

# Script to extract release notes for a specific version from RELEASE_NOTES.md
# Usage: ./extract_release_notes.sh <version>

VERSION="$1"

if [ -z "$VERSION" ]; then
  echo "❌ Error: Version argument required"
  echo "Usage: $0 <version>"
  exit 1
fi

echo "Extracting release notes for version $VERSION from RELEASE_NOTES.md"

# Extract the section for the current version
# Find the line with "# Release Notes - vX.X.X" and extract until the next version or EOF
awk -v ver="$VERSION" '
  BEGIN { found=0; printing=0 }
  /^# Release Notes - v/ {
    if ($0 ~ ver) {
      found=1
      printing=1
      next
    } else if (found && printing) {
      exit
    }
  }
  printing { print }
' RELEASE_NOTES.md > release_notes.md

# Check if we found content (should always succeed now)
if [ ! -s release_notes.md ]; then
  echo "❌ Error: Could not extract release notes for version $VERSION"
  exit 1
fi

echo "✅ Successfully extracted release notes for version $VERSION"

# Append Docker information
echo "---" >> release_notes.md
echo "" >> release_notes.md
echo "### Docker Images" >> release_notes.md
echo "" >> release_notes.md
echo '```bash' >> release_notes.md
echo "docker pull rangermix/twitch-drops-miner:$VERSION" >> release_notes.md
echo '```' >> release_notes.md

cat >> release_notes.md <<'EOF'

### Twitch sign in

When login is needed, open TDM’s dashboard and sign in to Twitch in the browser shown
there. Complete verification, select **Finish sign in**, and wait for the dashboard
to return. The Docker image includes the browser and uses it by default.
Set Docker’s `TZ` to the timezone of your home internet connection.
Use **Log out of Twitch** at the bottom of Settings to change the miner’s account.

If Twitch rejects the embedded browser, select **Use desktop helper** and download
the Windows, Linux, or macOS helper from this same release. Extract and run it on
your computer, then enter only the dashboard URL. The ten-minute access window
accepts the first helper to connect; **Return to embedded browser** closes access.
Complete Twitch sign-in, then close the helper's browser windows (quit that instance
on macOS). Leave the helper open until it reports success.
EOF

echo "✅ Release notes written to release_notes.md"
