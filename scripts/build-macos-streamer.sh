#!/usr/bin/env bash
set -euo pipefail

if [[ "$(uname -s)" != "Darwin" ]]; then
  echo "This library must be built on macOS." >&2
  exit 1
fi

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
mkdir -p "$ROOT/build"
MACOSX_DEPLOYMENT_TARGET=13.0 swiftc -O -emit-library \
  -framework ScreenCaptureKit \
  -framework VideoToolbox \
  -framework CoreMedia \
  -framework CoreVideo \
  -framework AppKit \
  "$ROOT/native/macos/ZMBStreamerLib.swift" \
  -o "$ROOT/build/libzmb_streamer.dylib"
chmod 0755 "$ROOT/build/libzmb_streamer.dylib"
echo "Built $ROOT/build/libzmb_streamer.dylib"
