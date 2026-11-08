#!/usr/bin/env bash
set -euo pipefail

if [[ "$(uname -s)" != "Darwin" ]]; then
  echo "This capability probe must be built on macOS." >&2
  exit 1
fi
if [[ "$(uname -m)" != "arm64" ]]; then
  echo "ZorinMacBridge capability probe supports Apple Silicon (arm64) only." >&2
  exit 1
fi

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SDK="$(xcrun --sdk macosx --show-sdk-path)"
if [[ ! -d "$SDK/System/Library/Frameworks/CoreHID.framework" ]]; then
  echo "ERROR: selected macOS SDK does not provide CoreHID.framework; Stage B requires macOS 26+ SDK." >&2
  exit 1
fi

mkdir -p "$ROOT/build"
if [[ ! -f "$ROOT/build/ZMBLoginInput.o" ]]; then
  MACOSX_DEPLOYMENT_TARGET=13.0 clang -O2 -fPIC -Wno-deprecated-declarations -c \
    "$ROOT/native/macos/ZMBLoginInput.c" \
    -o "$ROOT/build/ZMBLoginInput.o"
fi

MACOSX_DEPLOYMENT_TARGET=26.0 swiftc -swift-version 5 -O -emit-library \
  -framework ScreenCaptureKit \
  -framework CoreGraphics \
  -framework ImageIO \
  -framework Security \
  -framework CryptoKit \
  -framework CoreHID \
  -framework IOKit \
  "$ROOT/native/macos/capability-probe/ZMBCapabilityProbe.swift" \
  "$ROOT/build/ZMBLoginInput.o" \
  -o "$ROOT/build/libzmb_capability_probe.dylib"
chmod 0755 "$ROOT/build/libzmb_capability_probe.dylib"
echo "Built $ROOT/build/libzmb_capability_probe.dylib"
