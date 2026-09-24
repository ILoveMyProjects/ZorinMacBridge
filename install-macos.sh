#!/usr/bin/env bash
set -euo pipefail

REPO="ILoveMyProjects/ZorinMacBridge"
BASE="https://github.com/${REPO}/releases/latest/download"
APP_NAME="ZorinMacBridge Server.app"
DEST="/Applications/$APP_NAME"
BUNDLE_ID="com.ilovemyprojects.zorinmacbridge.server"

case "$(uname -m)" in
  arm64)
    ASSET="ZorinMacBridge-Server_macOS-arm64.dmg"
    SUMS="SHA256SUMS-macOS-arm64.txt"
    ;;
  x86_64)
    ASSET="ZorinMacBridge-Server_macOS-x86_64.dmg"
    SUMS="SHA256SUMS-macOS-x86_64.txt"
    ;;
  *)
    echo "Unsupported Mac architecture: $(uname -m)" >&2
    exit 1
    ;;
esac

if pgrep -x "ZorinMacBridge Server" >/dev/null 2>&1; then
  echo "Quit ZorinMacBridge Server before updating it, then run this command again." >&2
  exit 1
fi

TMPDIR="$(mktemp -d)"
MOUNT="$TMPDIR/mount"
STAGE="$TMPDIR/stage"
DMG="$TMPDIR/$ASSET"
SUMFILE="$TMPDIR/$SUMS"
mkdir -p "$MOUNT" "$STAGE"
cleanup() {
  hdiutil detach "$MOUNT" -quiet >/dev/null 2>&1 || true
  rm -rf "$TMPDIR"
}
trap cleanup EXIT

echo "Downloading the latest ZorinMacBridge Server…"
curl -fL --retry 3 --connect-timeout 10 "${BASE}/${ASSET}" -o "$DMG"
curl -fL --retry 3 --connect-timeout 10 "${BASE}/${SUMS}" -o "$SUMFILE"

EXPECTED="$(awk -v name="$ASSET" '$2 == name {print $1}' "$SUMFILE" | head -n1)"
ACTUAL="$(shasum -a 256 "$DMG" | awk '{print $1}')"
if [ -z "$EXPECTED" ] || [ "$EXPECTED" != "$ACTUAL" ]; then
  echo "SHA-256 verification failed. The application will not be installed." >&2
  exit 1
fi

echo "SHA-256 verified."
echo "Mounting installer…"
hdiutil attach "$DMG" -nobrowse -readonly -mountpoint "$MOUNT" -quiet

SOURCE="$MOUNT/$APP_NAME"
if [ ! -d "$SOURCE" ]; then
  echo "The DMG does not contain $APP_NAME" >&2
  exit 1
fi

# Verify the transport artifact before changing its local code identity.
codesign --verify --deep --strict --verbose=2 "$SOURCE"
ACTUAL_BUNDLE_ID="$(/usr/libexec/PlistBuddy -c 'Print :CFBundleIdentifier' "$SOURCE/Contents/Info.plist" 2>/dev/null || true)"
if [ "$ACTUAL_BUNDLE_ID" != "$BUNDLE_ID" ]; then
  echo "Unexpected bundle identifier: $ACTUAL_BUNDLE_ID" >&2
  exit 1
fi

SIGNED="$STAGE/$APP_NAME"
ditto "$SOURCE" "$SIGNED"

echo "Preparing this Mac's persistent ZorinMacBridge identity…"
PREP_OUT="$("$SOURCE/Contents/MacOS/ZorinMacBridge Server" --prepare-local-signing)"
CERT_PATH="$(printf '%s\n' "$PREP_OUT" | sed -n 's/^CERT_PATH=//p' | tail -n1)"
TRUSTED="$(printf '%s\n' "$PREP_OUT" | sed -n 's/^TRUSTED=//p' | tail -n1)"
if [ -z "$CERT_PATH" ] || [ ! -f "$CERT_PATH" ]; then
  echo "Could not prepare the persistent local code-signing certificate." >&2
  exit 1
fi
if [ "$TRUSTED" != "1" ]; then
  echo "Authorizing this Mac's one-time local code-signing trust…"
  sudo /usr/bin/security add-trusted-cert -d -r trustRoot -p codeSign \
    -k /Library/Keychains/System.keychain "$CERT_PATH"
fi

"$SOURCE/Contents/MacOS/ZorinMacBridge Server" --local-sign-app "$SIGNED"
codesign --verify --deep --strict --verbose=2 "$SIGNED"

echo "Installing to /Applications…"
sudo rm -rf "$DEST"
sudo ditto "$SIGNED" "$DEST"

echo
echo "ZorinMacBridge Server is installed in /Applications."
echo "This Mac now owns a persistent local code-signing identity for ZorinMacBridge."
echo "Future in-app updates are re-signed with the same identity before installation."
echo "No GitHub login, GitHub secret, Developer ID, or Linux signing setup is required."
echo "The app was NOT started automatically."
