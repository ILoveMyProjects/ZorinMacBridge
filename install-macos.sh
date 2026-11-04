#!/usr/bin/env bash
set -euo pipefail

APP_ONLY=0
if [ "${1:-}" = "--app-only" ]; then
  APP_ONLY=1
elif [ "$#" -gt 0 ]; then
  echo "Usage: $0 [--app-only]" >&2
  exit 64
fi

REPO="ILoveMyProjects/ZorinMacBridge"
BASE="https://github.com/${REPO}/releases/latest/download"
APP_NAME="ZorinMacBridge Server.app"
DEST="/Applications/$APP_NAME"
BUNDLE_ID="com.ilovemyprojects.zorinmacbridge.server"

ARCH="$(uname -m)"
if [ "$ARCH" != "arm64" ]; then
  echo "ZorinMacBridge Server supports Apple Silicon (arm64) Macs only; detected: $ARCH" >&2
  exit 1
fi
ASSET="ZorinMacBridge-Server_macOS-arm64.dmg"
SUMS="SHA256SUMS-macOS-arm64.txt"

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

echo "Applying this Mac's stable local designated requirement…"
"$SOURCE/Contents/MacOS/ZorinMacBridge Server" --local-sign-app "$SIGNED"
codesign --verify --deep --strict --verbose=2 "$SIGNED"

echo "Installing to /Applications…"
sudo rm -rf "$DEST"
sudo ditto "$SIGNED" "$DEST"

echo
echo "ZorinMacBridge Server is installed in /Applications."
echo "This Mac now has a persistent local designated requirement for ZorinMacBridge."
echo "Future in-app updates are re-signed with the same designated requirement before installation."
echo "No certificate, keychain setup, GitHub login, Developer ID, or Linux signing setup is required."
echo "The app was NOT started automatically."

if [ "$APP_ONLY" -eq 0 ]; then
  echo
  VERSION_FILE="$(/usr/bin/find "$DEST/Contents" -maxdepth 4 -type f -name VERSION -print 2>/dev/null | /usr/bin/head -n 1)"
  APP_VERSION=""
  if [ -n "$VERSION_FILE" ]; then
    APP_VERSION="$(/bin/cat "$VERSION_FILE" | /usr/bin/tr -d '[:space:]')"
  fi
  if [ -z "$APP_VERSION" ]; then
    echo "The installed release does not expose its ZorinMacBridge VERSION; unattended setup was not started." >&2
    echo "Publish/install ZorinMacBridge v0.8.2 or newer, then rerun this installer." >&2
    exit 65
  fi
  if ! /usr/bin/awk -v v="$APP_VERSION" 'BEGIN { split(v,a,"."); major=a[1]+0; minor=a[2]+0; patch=a[3]+0; if (major>0 || (major==0 && (minor>8 || (minor==8 && patch>=2)))) exit 0; exit 1 }'; then
    echo "Installed release is ZorinMacBridge $APP_VERSION. Terminal SSH/P0 setup requires v0.8.2 or newer." >&2
    echo "Create/publish the v0.8.2 release first, then run this installer again." >&2
    exit 65
  fi
  echo "Configuring unattended access, macOS Remote Login (SSH), and the ZorinMacBridge session password…"
  "$DEST/Contents/MacOS/ZorinMacBridge Server" --terminal-setup
else
  echo
  echo "Skipped unattended/SSH setup because --app-only was used."
fi
