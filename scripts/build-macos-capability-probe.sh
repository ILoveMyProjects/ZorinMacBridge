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

mkdir -p "$ROOT/build"
if [[ ! -f "$ROOT/build/ZMBLoginInput.o" ]]; then
  MACOSX_DEPLOYMENT_TARGET=26.0 clang -arch arm64 -mmacosx-version-min=26.0 -O2 -fPIC -Wno-deprecated-declarations -c \
    "$ROOT/native/macos/ZMBLoginInput.c" \
    -o "$ROOT/build/ZMBLoginInput.o"
fi

SWIFT_ARGS=(
  -swift-version 5
  -target arm64-apple-macos26.0
  -O
  -emit-library
  -framework ScreenCaptureKit
  -framework CoreGraphics
  -framework ImageIO
  -framework Security
  -framework CryptoKit
  -framework IOKit
)

# macOS 26 is the minimum supported runtime. CoreHID is therefore a required
# Stage-B capability dependency, not a weak/optional compatibility path.
if [[ ! -d "$SDK/System/Library/Frameworks/CoreHID.framework" ]]; then
  echo 'ERROR: macOS 26+ SDK with CoreHID.framework is required.' >&2
  exit 1
fi
SWIFT_ARGS+=( -framework CoreHID )
echo 'Capability probe: CoreHID SDK support required (macOS 26+ target).'

MACOSX_DEPLOYMENT_TARGET=26.0 swiftc "${SWIFT_ARGS[@]}" \
  "$ROOT/native/macos/capability-probe/ZMBCapabilityProbe.swift" \
  "$ROOT/build/ZMBLoginInput.o" \
  -o "$ROOT/build/libzmb_capability_probe.dylib"
chmod 0755 "$ROOT/build/libzmb_capability_probe.dylib"
echo "Built $ROOT/build/libzmb_capability_probe.dylib"
