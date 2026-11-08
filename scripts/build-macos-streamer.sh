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
MACOSX_DEPLOYMENT_TARGET=26.0 clang -arch arm64 -mmacosx-version-min=26.0 -O2 -fPIC -Wno-deprecated-declarations -c \
  "$ROOT/native/macos/ZMBLoginInput.c" \
  -o "$ROOT/build/ZMBLoginInput.o"
MACOSX_DEPLOYMENT_TARGET=26.0 swiftc -target arm64-apple-macos26.0 -O -emit-library \
  -framework ScreenCaptureKit \
  -framework VideoToolbox \
  -framework CoreMedia \
  -framework CoreVideo \
  -framework CoreGraphics \
  -framework IOKit \
  -framework AppKit \
  "$ROOT/native/macos/ZMBStreamerLib.swift" \
  "$ROOT/build/ZMBLoginInput.o" \
  -o "$ROOT/build/libzmb_streamer.dylib"
chmod 0755 "$ROOT/build/libzmb_streamer.dylib"
echo "Built $ROOT/build/libzmb_streamer.dylib"
