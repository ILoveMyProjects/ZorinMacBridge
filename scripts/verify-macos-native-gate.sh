#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

if [[ "$(uname -s)" != "Darwin" ]]; then
  echo "ERROR: macOS native gate must run on Darwin." >&2
  exit 1
fi
if [[ "$(uname -m)" != "arm64" ]]; then
  echo "ERROR: macOS native gate requires an Apple Silicon arm64 runner." >&2
  exit 1
fi

for tool in clang swiftc xcodebuild file otool nm; do
  command -v "$tool" >/dev/null 2>&1 || {
    echo "ERROR: required macOS build tool is missing: $tool" >&2
    exit 1
  }
done

echo '=== macOS native build environment ==='
sw_vers
xcodebuild -version
swiftc --version
clang --version | head -n 1

echo '=== shell syntax ==='
bash -n scripts/build-macos-streamer.sh
bash -n scripts/build-macos-capability-probe.sh
bash -n scripts/sign-macos-transport.sh
bash -n scripts/sign-macos-release.sh
python3 -m py_compile mac_release_signing.py
plutil -lint native/macos/signing/ZorinMacBridge.release-entitlements.plist
bash -n native/macos/admin/install-admin.sh
bash -n native/macos/admin/uninstall-admin.sh
bash -n native/macos/admin/zmb-admin
bash -n native/macos/admin/zmb-admin-ssh
bash -n native/macos/admin/zmb-system-daemon

echo '=== compile native streamer dylib ==='
rm -f build/ZMBLoginInput.o build/libzmb_streamer.dylib build/libzmb_capability_probe.dylib
scripts/build-macos-streamer.sh
scripts/build-macos-capability-probe.sh

test -s build/ZMBLoginInput.o
test -s build/libzmb_streamer.dylib
test -s build/libzmb_capability_probe.dylib

file build/ZMBLoginInput.o | grep -F 'arm64'
file build/libzmb_streamer.dylib | grep -F 'arm64'
file build/libzmb_capability_probe.dylib | grep -F 'arm64'

echo '=== verify linked macOS frameworks ==='
otool -L build/libzmb_streamer.dylib | tee build/libzmb_streamer.otool.txt
grep -F '/System/Library/Frameworks/ScreenCaptureKit.framework/' build/libzmb_streamer.otool.txt
grep -F '/System/Library/Frameworks/VideoToolbox.framework/' build/libzmb_streamer.otool.txt
grep -F '/System/Library/Frameworks/CoreGraphics.framework/' build/libzmb_streamer.otool.txt
grep -F '/System/Library/Frameworks/IOKit.framework/' build/libzmb_streamer.otool.txt

echo '=== verify Stage-B capability probe frameworks ==='
otool -L build/libzmb_capability_probe.dylib | tee build/libzmb_capability_probe.otool.txt
grep -F '/System/Library/Frameworks/ScreenCaptureKit.framework/' build/libzmb_capability_probe.otool.txt
grep -F '/System/Library/Frameworks/CoreHID.framework/' build/libzmb_capability_probe.otool.txt
grep -F '/System/Library/Frameworks/Security.framework/' build/libzmb_capability_probe.otool.txt

echo '=== verify exported ABI ==='
nm -gU build/libzmb_streamer.dylib | tee build/libzmb_streamer.nm.txt
for symbol in \
  _zmb_streamer_create \
  _zmb_streamer_run \
  _zmb_streamer_stop \
  _zmb_streamer_destroy \
  _zmb_login_input_open \
  _zmb_login_input_key \
  _zmb_login_input_pointer \
  _zmb_login_input_scroll; do
  grep -Eq "[[:space:]]${symbol}$" build/libzmb_streamer.nm.txt || {
    echo "ERROR: missing exported ABI symbol: ${symbol#_}" >&2
    exit 1
  }
done

nm -gU build/libzmb_capability_probe.dylib | tee build/libzmb_capability_probe.nm.txt
grep -Eq '[[:space:]]_zmb_capability_probe_run$' build/libzmb_capability_probe.nm.txt || {
  echo 'ERROR: missing exported Stage-B ABI symbol: zmb_capability_probe_run' >&2
  exit 1
}

echo 'macOS native build gate PASS'
