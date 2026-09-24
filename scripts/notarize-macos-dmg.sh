#!/usr/bin/env bash
set -euo pipefail

DMG="${1:?usage: notarize-macos-dmg.sh /path/to/file.dmg}"

if [[ -z "${APPLE_ID:-}" || -z "${APPLE_TEAM_ID:-}" || -z "${APPLE_APP_PASSWORD:-}" ]]; then
  echo "Notarization secrets are not configured; skipping notarization."
  exit 0
fi

xcrun notarytool submit "$DMG" \
  --apple-id "$APPLE_ID" \
  --team-id "$APPLE_TEAM_ID" \
  --password "$APPLE_APP_PASSWORD" \
  --wait
xcrun stapler staple "$DMG"
xcrun stapler validate "$DMG"
echo "Notarization and stapling complete."
