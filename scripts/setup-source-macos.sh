#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
command -v python3 >/dev/null 2>&1 || { echo 'python3 is required on macOS.' >&2; exit 1; }
python3 -m venv .venv
. .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
./scripts/build-macos-streamer.sh
printf '\nSource environment ready. Start the macOS server GUI with:\n  ./.venv/bin/python mac_server_gui.py\n'
