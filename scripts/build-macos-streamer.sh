#!/usr/bin/env bash
set -euo pipefail

if [[ "$(uname -s)" != "Darwin" ]]; then
  echo "This library must be built on macOS." >&2
  exit 1
fi
if [[ "$(uname -m)" != "arm64" ]]; then
  echo "ZorinMacBridge macOS builds support Apple Silicon (arm64) only." >&2
  exit 1
fi

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
mkdir -p "$ROOT/build"
NATIVE_BUILD_VERSION="$(tr -d '[:space:]' < "$ROOT/VERSION")"
case "$NATIVE_BUILD_VERSION" in
  ''|*[!0-9A-Za-z._-]*) echo "Invalid VERSION for native build stamp: $NATIVE_BUILD_VERSION" >&2; exit 1 ;;
esac
printf 'let ZMB_NATIVE_BUILD_VERSION = "%s"\n' "$NATIVE_BUILD_VERSION" > "$ROOT/build/ZMBNativeBuildVersion.swift"
MACOSX_DEPLOYMENT_TARGET=26.0 clang -arch arm64 -mmacosx-version-min=26.0 -O2 -fPIC -Wno-deprecated-declarations -c \
  "$ROOT/native/macos/ZMBLoginInput.c" \
  -o "$ROOT/build/ZMBLoginInput.o"
MACOSX_DEPLOYMENT_TARGET=26.0 swiftc -swift-version 5 -target arm64-apple-macos26.0 -O -emit-library \
  -framework ScreenCaptureKit \
  -framework VideoToolbox \
  -framework CoreMedia \
  -framework CoreVideo \
  -framework CoreGraphics \
  -framework CoreHID \
  -framework IOKit \
  -framework AppKit \
  "$ROOT/build/ZMBNativeBuildVersion.swift" \
  "$ROOT/native/macos/ZMBStreamerLib.swift" \
  "$ROOT/native/macos/ZMBCoreHIDInput.swift" \
  "$ROOT/build/ZMBLoginInput.o" \
  -o "$ROOT/build/libzmb_streamer.dylib"
chmod 0755 "$ROOT/build/libzmb_streamer.dylib"
echo "Built $ROOT/build/libzmb_streamer.dylib"
