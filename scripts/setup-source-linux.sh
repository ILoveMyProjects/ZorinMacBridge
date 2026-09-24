#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."

if ! command -v apt >/dev/null 2>&1; then
  echo 'This source setup targets Zorin OS / Ubuntu / Debian systems with apt.' >&2
  exit 1
fi

sudo apt update
sudo apt install -y \
  python3 python3-gi gir1.2-gtk-4.0 gir1.2-adw-1 libadwaita-1-0 \
  python3-pil python3-pil.imagetk python3-av python3-certifi \
  python3-keyring python3-secretstorage python3-zeroconf python3-pystray \
  python3-tk wl-clipboard xclip pkexec

printf '\nNative GTK4/libadwaita source environment ready. Start the client with:\n  ./run_client.sh\n'
