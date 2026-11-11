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
MACOS_VERSION="$(/usr/bin/sw_vers -productVersion 2>/dev/null || true)"
MACOS_MAJOR="${MACOS_VERSION%%.*}"
if [[ ! "$MACOS_MAJOR" =~ ^[0-9]+$ ]] || (( MACOS_MAJOR < 26 )); then
  echo "ZorinMacBridge requires macOS 26.0 or later; detected: ${MACOS_VERSION:-unknown}" >&2
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

# The app transport may use the stable local identity. LoginWindow keyboard
# and mouse are provided separately by the bundled driver-backed virtual HID.
codesign --verify --deep --strict --verbose=2 "$SOURCE"
ACTUAL_BUNDLE_ID="$(/usr/libexec/PlistBuddy -c 'Print :CFBundleIdentifier' "$SOURCE/Contents/Info.plist" 2>/dev/null || true)"
if [ "$ACTUAL_BUNDLE_ID" != "$BUNDLE_ID" ]; then
  echo "Unexpected bundle identifier: $ACTUAL_BUNDLE_ID" >&2
  exit 1
fi

STAGED="$STAGE/$APP_NAME"
ditto "$SOURCE" "$STAGED"
EXISTING_PRELOGIN_CAPABLE=0
if [ -d "$DEST" ] && "$SOURCE/Contents/MacOS/ZorinMacBridge Server" --verify-release-app "$DEST" >/dev/null 2>&1; then
  EXISTING_PRELOGIN_CAPABLE=1
fi
if "$SOURCE/Contents/MacOS/ZorinMacBridge Server" --verify-release-app "$SOURCE" >/dev/null 2>&1; then
  SIGNING_MODE="developer-id"
  echo "Developer ID pre-login-capable release detected; preserving publisher signature and provisioning profile."
else
  if [ "$EXISTING_PRELOGIN_CAPABLE" -eq 1 ]; then
    echo "Refusing to replace a pre-login-capable Developer ID installation with a free/ad-hoc build." >&2
    echo "That downgrade would remove Apple-managed Persistent Content Capture/CoreHID capabilities." >&2
    exit 78
  fi
  SIGNING_MODE="local-adhoc"
  echo "Free Aqua-only release detected; applying this Mac's stable local ad-hoc identity."
  "$SOURCE/Contents/MacOS/ZorinMacBridge Server" --local-sign-app "$STAGED"
fi
codesign --verify --deep --strict --verbose=2 "$STAGED"

echo "Installing verified $SIGNING_MODE release to /Applications…"
sudo rm -rf "$DEST"
sudo ditto "$STAGED" "$DEST"

codesign --verify --deep --strict --verbose=2 "$DEST"
if [ "$SIGNING_MODE" = "developer-id" ]; then
  "$DEST/Contents/MacOS/ZorinMacBridge Server" --verify-release-app "$DEST"
fi

echo
echo "ZorinMacBridge Server is installed in /Applications."
if [ "$SIGNING_MODE" = "developer-id" ]; then
  echo "Developer ID signature + provisioning profile with managed pre-login capabilities were preserved."
  echo "Pre-login still requires a physical LoginWindow capability probe before it can become VERIFIED."
else
  echo "A stable per-Mac local code identity was applied. Normal Aqua Desktop remains supported."
  echo "LoginWindow input is configured by unattended setup through the bundled virtual HID driver."
fi
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
    echo "Publish/install ZorinMacBridge v0.14.0 or newer, then rerun this installer." >&2
    exit 65
  fi
  if ! /usr/bin/awk -v v="$APP_VERSION" 'BEGIN { split(v,a,"."); major=a[1]+0; minor=a[2]+0; patch=a[3]+0; if (major>0 || (major==0 && (minor>14 || (minor==14 && patch>=0)))) exit 0; exit 1 }'; then
    echo "Installed release is ZorinMacBridge $APP_VERSION. Terminal SSH/P0 setup requires v0.14.0 or newer." >&2
    echo "Create/publish the v0.14.0 release first, then run this installer again." >&2
    exit 65
  fi
  echo "Configuring unattended access, macOS Remote Login (SSH), and the ZorinMacBridge session password…"
  "$DEST/Contents/MacOS/ZorinMacBridge Server" --terminal-setup
else
  echo
  echo "Skipped unattended/SSH setup because --app-only was used."
fi
