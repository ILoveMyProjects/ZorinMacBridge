#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
if [ "$(uname -s)" != "Darwin" ]; then
  echo "This setup script is for macOS only." >&2
  exit 1
fi
if [ "$(uname -m)" != "arm64" ]; then
  echo "ZorinMacBridge macOS development supports Apple Silicon (arm64) only." >&2
  exit 1
fi
MACOS_VERSION="$(/usr/bin/sw_vers -productVersion 2>/dev/null || true)"
MACOS_MAJOR="${MACOS_VERSION%%.*}"
if [[ ! "$MACOS_MAJOR" =~ ^[0-9]+$ ]] || (( MACOS_MAJOR < 26 )); then
  echo "ZorinMacBridge requires macOS 26.0 or later; detected: ${MACOS_VERSION:-unknown}" >&2
  exit 1
fi
command -v python3 >/dev/null 2>&1 || { echo 'python3 is required on macOS.' >&2; exit 1; }
python3 -m venv .venv
. .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
./scripts/build-macos-streamer.sh
printf '\nSource environment ready. Start the macOS server GUI with:\n  ./.venv/bin/python mac_server_gui.py\n'
