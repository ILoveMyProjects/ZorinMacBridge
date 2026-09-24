#!/usr/bin/env bash
set -euo pipefail

MODE="${1:-}"
SELF_TEST=0
APP=""
BUNDLE_ID='com.ilovemyprojects.zorinmacbridge.server'
if [ "$MODE" = "--self-test" ]; then
  SELF_TEST=1
elif [ -n "$MODE" ]; then
  APP="$MODE"
else
  echo "usage: sign-macos-transport.sh /path/to/App.app | --self-test" >&2
  exit 2
fi

TMP="${RUNNER_TEMP:-/tmp}/zmb-transport-signing-${RANDOM}-${RANDOM}"
mkdir -p "$TMP"
trap 'rm -rf "$TMP"' EXIT

# Transport signing deliberately uses ad-hoc signing. The installed app is
# re-signed locally with an explicit stable designated requirement before it
# replaces /Applications. No certificate or keychain is needed in CI.
sign_probe() {
  local target="$1"
  local ident='com.ilovemyprojects.zorinmacbridge.transport-preflight'
  local req="$TMP/probe.req"
  printf 'designated => identifier "%s"\n' "$ident" > "$req"
  codesign --force --timestamp=none --sign - --identifier "$ident" --requirements "$req" "$target"
  codesign --verify --strict --verbose=2 "$target"
  local shown
  shown="$(codesign -d -r- "$target" 2>&1)"
  printf '%s\n' "$shown" | grep -Fq "designated => identifier \"$ident\""
  if printf '%s\n' "$shown" | grep -Fq 'cdhash '; then
    echo 'ERROR: transport signing probe fell back to a build-bound cdhash DR.' >&2
    exit 1
  fi
}

echo '[transport-sign] probing explicit ad-hoc designated requirement'
cp /usr/bin/true "$TMP/codesign-probe"
chmod u+w "$TMP/codesign-probe"
sign_probe "$TMP/codesign-probe"
echo '[transport-sign] explicit DR probe OK'

if [ "$SELF_TEST" -eq 1 ]; then
  echo '[transport-sign] probing stable per-Mac explicit DR shape with nested framework'
  TEST_APP="$TMP/ZorinMacBridge Server.app"
  TEST_FRAMEWORK="$TEST_APP/Contents/Frameworks/ZMBProbe.framework"
  mkdir -p "$TEST_APP/Contents/MacOS" "$TEST_FRAMEWORK/Versions/A/Resources"
  cp /usr/bin/true "$TEST_APP/Contents/MacOS/ZorinMacBridge Server"
  cp /usr/bin/true "$TEST_FRAMEWORK/Versions/A/ZMBProbe"
  chmod u+w "$TEST_APP/Contents/MacOS/ZorinMacBridge Server" "$TEST_FRAMEWORK/Versions/A/ZMBProbe"
  ln -s A "$TEST_FRAMEWORK/Versions/Current"
  ln -s Versions/Current/ZMBProbe "$TEST_FRAMEWORK/ZMBProbe"
  ln -s Versions/Current/Resources "$TEST_FRAMEWORK/Resources"
  cat > "$TEST_FRAMEWORK/Versions/A/Resources/Info.plist" <<'FPLIST'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>CFBundleIdentifier</key><string>com.ilovemyprojects.zorinmacbridge.signingprobe</string>
  <key>CFBundleExecutable</key><string>ZMBProbe</string>
  <key>CFBundlePackageType</key><string>FMWK</string>
  <key>CFBundleVersion</key><string>1</string>
  <key>CFBundleShortVersionString</key><string>1.0</string>
</dict></plist>
FPLIST
  cat > "$TEST_APP/Contents/Info.plist" <<'PLIST'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>CFBundleIdentifier</key><string>com.ilovemyprojects.zorinmacbridge.server</string>
  <key>CFBundleExecutable</key><string>ZorinMacBridge Server</string>
  <key>CFBundlePackageType</key><string>APPL</string>
  <key>ZMBLocalIdentity</key><string>0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef</string>
</dict></plist>
PLIST
  # Simulate PyInstaller: nested code is already signed before the outer bundle.
  codesign --force --timestamp=none --sign - "$TEST_FRAMEWORK"
  codesign --force --timestamp=none --sign - "$TEST_APP/Contents/MacOS/ZorinMacBridge Server"

  LOCAL_REQ="$TMP/local.req"
  LOCAL_EXPR="$TMP/local.expr"
  printf '%s\n' 'designated => identifier "com.ilovemyprojects.zorinmacbridge.server" and info[ZMBLocalIdentity] = "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef"' > "$LOCAL_REQ"
  printf '%s\n' 'identifier "com.ilovemyprojects.zorinmacbridge.server" and info[ZMBLocalIdentity] = "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef"' > "$LOCAL_EXPR"

  # Critical regression check: do NOT touch already-signed nested code. Re-sign
  # only the outer app so its resource envelope records the existing nested signatures.
  codesign --force --timestamp=none --sign - --requirements "$LOCAL_REQ" "$TEST_APP"
  codesign --verify --deep --strict --verbose=2 "$TEST_APP"
  codesign --verify --deep --strict -R "$LOCAL_EXPR" "$TEST_APP"
  codesign --verify --strict --verbose=2 "$TEST_FRAMEWORK"
  LOCAL_SHOWN="$(codesign -d -r- "$TEST_APP" 2>&1)"
  printf '%s\n' "$LOCAL_SHOWN" | grep -Fq 'info[ZMBLocalIdentity]'
  if printf '%s\n' "$LOCAL_SHOWN" | grep -Fq 'cdhash '; then
    echo 'ERROR: local stable DR self-test fell back to a build-bound cdhash.' >&2
    exit 1
  fi
  echo '[transport-sign] nested framework remained valid'
  echo '[transport-sign] stable local DR probe OK'
  echo '[transport-sign] self-test OK'
  exit 0
fi

if [ ! -d "$APP" ]; then
  echo "ERROR: app bundle not found: $APP" >&2
  exit 2
fi

ACTUAL_BUNDLE_ID="$(/usr/libexec/PlistBuddy -c 'Print :CFBundleIdentifier' "$APP/Contents/Info.plist" 2>/dev/null || true)"
if [ "$ACTUAL_BUNDLE_ID" != "$BUNDLE_ID" ]; then
  echo "ERROR: unexpected bundle identifier: $ACTUAL_BUNDLE_ID" >&2
  exit 1
fi

REQ="$TMP/app.req"
REQ_EXPR="$TMP/app.expr"
printf 'designated => identifier "%s"\n' "$BUNDLE_ID" > "$REQ"
printf 'identifier "%s"\n' "$BUNDLE_ID" > "$REQ_EXPR"
echo '[transport-sign] preserving PyInstaller nested signatures'
echo '[transport-sign] signing only the outer application bundle with explicit transport DR'
# PyInstaller already signs its collected Mach-O files and nested framework bundles.
# Re-signing individual .so/.dylib files here invalidates the resource envelopes of
# containing bundles such as Python.framework. Sign only the outer app, inside-out.
codesign --force --timestamp=none --sign - --requirements "$REQ" "$APP"
codesign --verify --deep --strict --verbose=2 "$APP"
codesign --verify --deep --strict -R "$REQ_EXPR" "$APP"
echo '[transport-sign] OK'
