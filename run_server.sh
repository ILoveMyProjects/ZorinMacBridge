#!/bin/sh
set -eu
cd "$(dirname "$0")"
if [ "$#" -lt 1 ]; then
  echo "Usage: $0 <mac_private_ip> [port]" >&2
  exit 2
fi
IP="$1"
PORT="${2:-45950}"
exec .venv/bin/python mac_server.py --bind "$IP" --port "$PORT"
