#!/usr/bin/env bash
set -euo pipefail

if [[ "$(uname -s)" != "Darwin" ]]; then
  echo "This helper must be built on macOS." >&2
  exit 1
fi

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
mkdir -p "$ROOT/build"
MACOSX_DEPLOYMENT_TARGET=13.0 swiftc -O \
  -framework ScreenCaptureKit \
  -framework VideoToolbox \
  -framework CoreMedia \
  -framework CoreVideo \
  -framework AppKit \
  "$ROOT/native/macos/ZMBStreamer.swift" \
  -o "$ROOT/build/zmb-macos-streamer"
chmod 0755 "$ROOT/build/zmb-macos-streamer"
echo "Built $ROOT/build/zmb-macos-streamer"
