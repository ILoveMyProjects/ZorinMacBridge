#!/bin/sh
set -eu
cd "$(dirname "$0")"
command -v python3 >/dev/null 2>&1 || { echo 'python3 is required on macOS.' >&2; exit 1; }
python3 -m venv .venv
. .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
printf '
Installation complete. Start the macOS server with:
  ./run_server.sh YOUR_MAC_PRIVATE_IP
'
