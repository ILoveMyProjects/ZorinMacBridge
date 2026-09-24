#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

VERSION_VALUE="$(tr -d '[:space:]' < VERSION)"
HEAD_SHA="$(git rev-parse HEAD 2>/dev/null || printf 'unknown')"
SCRIPT_SHA="$(shasum -a 256 scripts/sign-macos-transport.sh | awk '{print $1}')"

echo "[release-source] VERSION=$VERSION_VALUE"
echo "[release-source] HEAD=$HEAD_SHA"
echo "[release-source] sign-macos-transport.sh SHA256=$SCRIPT_SHA"
echo "[release-source] ref=${GITHUB_REF:-<none>}"
echo "[release-source] ref_name=${GITHUB_REF_NAME:-<none>}"
echo "[release-source] ref_type=${GITHUB_REF_TYPE:-<none>}"

if [ "${GITHUB_REF_TYPE:-}" = "tag" ]; then
  EXPECTED_TAG="v${VERSION_VALUE}"
  if [ "${GITHUB_REF_NAME:-}" != "$EXPECTED_TAG" ]; then
    echo "ERROR: release tag ${GITHUB_REF_NAME:-<missing>} does not match VERSION ($EXPECTED_TAG)." >&2
    exit 1
  fi
fi

# v0.6.7+ invariant: PyInstaller nested signatures must be preserved.
if grep -Fq "signing nested native libraries ad-hoc" scripts/sign-macos-transport.sh; then
  echo "ERROR: obsolete v0.6.6 transport-signing code is present in this checkout." >&2
  exit 1
fi
if ! grep -Fq "preserving PyInstaller nested signatures" scripts/sign-macos-transport.sh; then
  echo "ERROR: expected v0.6.7+ outer-bundle-only signing marker is missing." >&2
  exit 1
fi
if ! grep -Fq "probing stable per-Mac explicit DR shape with nested framework" scripts/sign-macos-transport.sh; then
  echo "ERROR: expected nested-framework signing preflight is missing." >&2
  exit 1
fi

# Keep runtime local signing aligned with the release signer.
if grep -Fq "rglob('*.dylib')" mac_local_signing.py || grep -Fq "rglob('*.so')" mac_local_signing.py; then
  echo "ERROR: runtime local signing appears to re-sign nested Mach-O files." >&2
  exit 1
fi
if ! grep -Fq "Only the outer .app needs a fresh signature" mac_local_signing.py; then
  echo "ERROR: expected outer-app-only local-signing invariant is missing." >&2
  exit 1
fi

echo "[release-source] source invariants OK"
