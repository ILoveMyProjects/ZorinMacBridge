#!/usr/bin/env bash
set -euo pipefail

REPO="ILoveMyProjects/ZorinMacBridge"
BASE="https://github.com/${REPO}/releases/latest/download"
APP_NAME="ZorinMacBridge Server.app"
DEST="/Applications/$APP_NAME"

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
DMG="$TMPDIR/$ASSET"
SUMFILE="$TMPDIR/$SUMS"
mkdir -p "$MOUNT"
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

if [ ! -d "$MOUNT/$APP_NAME" ]; then
  echo "The DMG does not contain $APP_NAME" >&2
  exit 1
fi

echo "Installing to /Applications…"
sudo rm -rf "$DEST"
sudo ditto "$MOUNT/$APP_NAME" "$DEST"

echo
echo "ZorinMacBridge Server is installed in /Applications."
echo "It was NOT started automatically. Open it manually when you want to use the Mac remotely."
echo "Closing the app stops the server; no daemon or login item is installed."
echo "Re-run this same command later to install the latest release."
