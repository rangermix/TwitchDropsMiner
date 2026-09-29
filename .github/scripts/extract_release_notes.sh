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

### Native login helper

Download the `tdm-login-helper-<version>-<platform>.tar.gz` asset matching your desktop:
`windows-x64`, `macos-arm64` (Apple Silicon), `macos-x64` (Intel), or `linux-x64`.
Extract the archive, run `tdm-login-helper` (`tdm-login-helper.exe` on Windows), enter
your TDM address, and sign into Twitch in the browser it opens. Complete verification,
then close all windows of that browser instance (on macOS, quit it). Keep the helper
open; the browser reopens to verify and transfer the session. Wait for confirmed
success. TDM then renews the session on your home server without the desktop remaining open.
Install native Chrome, Chromium or Firefox 143+; automatic selection uses that order.
Python is not required. Binaries are unsigned.
`SHA256SUMS` contains checksums for all four archives.
EOF

echo "✅ Release notes written to release_notes.md"
