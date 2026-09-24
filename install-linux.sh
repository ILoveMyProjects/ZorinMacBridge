#!/usr/bin/env bash
set -euo pipefail

REPO="ILoveMyProjects/ZorinMacBridge"
BASE="https://github.com/${REPO}/releases/latest/download"

if ! command -v dpkg >/dev/null 2>&1 || ! command -v apt >/dev/null 2>&1; then
  echo "This installer supports Debian/Ubuntu/Zorin systems with dpkg and apt." >&2
  exit 1
fi

ARCH="$(dpkg --print-architecture)"
case "$ARCH" in
  amd64)
    ASSET="ZorinMacBridge-Client_linux-amd64.deb"
    SUMS="SHA256SUMS-linux.txt"
    ;;
  *)
    echo "Unsupported Linux architecture: $ARCH" >&2
    echo "Current public package: amd64 only." >&2
    exit 1
    ;;
esac

TMPDIR="$(mktemp -d)"
trap 'rm -rf "$TMPDIR"' EXIT
PKG="$TMPDIR/$ASSET"
SUMFILE="$TMPDIR/$SUMS"

echo "Downloading the latest ZorinMacBridge Client…"
curl -fL --retry 3 --connect-timeout 10 "${BASE}/${ASSET}" -o "$PKG"
curl -fL --retry 3 --connect-timeout 10 "${BASE}/${SUMS}" -o "$SUMFILE"

EXPECTED="$(awk -v name="$ASSET" '$2 == name {print $1}' "$SUMFILE" | head -n1)"
ACTUAL="$(sha256sum "$PKG" | awk '{print $1}')"
if [ -z "$EXPECTED" ] || [ "$EXPECTED" != "$ACTUAL" ]; then
  echo "SHA-256 verification failed. The package will not be installed." >&2
  exit 1
fi

echo "SHA-256 verified."
echo "Installing ZorinMacBridge Client…"
sudo apt install -y "$PKG"

echo
echo "ZorinMacBridge Client is installed."
echo "Open it from your application menu."
echo "Re-run this same command later to install the latest release."
