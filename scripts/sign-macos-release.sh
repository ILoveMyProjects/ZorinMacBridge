#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
APP="${1:-}"
PROFILE="${2:-}"
IDENTITY="${3:-}"

if [[ "$(uname -s)" != "Darwin" ]]; then
  echo 'ERROR: release signing must run on macOS.' >&2
  exit 1
fi
if [[ -z "$APP" || -z "$PROFILE" || -z "$IDENTITY" ]]; then
  echo 'usage: sign-macos-release.sh APP PROFILE "Developer ID Application: ..."' >&2
  exit 2
fi
if [[ ! -d "$APP" ]]; then
  echo "ERROR: app not found: $APP" >&2
  exit 1
fi
if [[ ! -f "$PROFILE" ]]; then
  echo "ERROR: provisioning profile not found: $PROFILE" >&2
  exit 1
fi

mkdir -p "$ROOT/build/signing"
ENTITLEMENTS="$ROOT/build/signing/ZorinMacBridge.release-entitlements.plist"

IDENTITY_SHA1="${MACOS_CODESIGN_SHA1:-$(security find-identity -v -p codesigning | awk -v needle="$IDENTITY" 'index($0, "\"" needle "\"") { print $2; exit }')}"
if [[ -z "$IDENTITY_SHA1" ]]; then
  echo "ERROR: Developer ID signing identity not found in keychain: $IDENTITY" >&2
  exit 1
fi
python3 "$ROOT/mac_release_signing.py" --verify-profile "$PROFILE" "$IDENTITY_SHA1"
python3 "$ROOT/mac_release_signing.py" --write-entitlements "$PROFILE" "$ENTITLEMENTS"

# The restricted entitlement claim and the Apple-issued profile must travel
# together. Embed the profile before the final outer-bundle signature so it is
# sealed by the app signature.
cp "$PROFILE" "$APP/Contents/embedded.provisionprofile"

# PyInstaller is invoked with --codesign-identity, so nested Mach-O code already
# carries the Developer ID identity. Finalize the outer app with the restricted
# entitlements, hardened runtime and a secure timestamp. Never use --deep to sign.
codesign --force --timestamp --options runtime \
  --sign "$IDENTITY" \
  --entitlements "$ENTITLEMENTS" \
  "$APP"

python3 "$ROOT/mac_release_signing.py" --verify-app "$APP"
echo 'macOS Developer ID + provisioning signing PASS'
