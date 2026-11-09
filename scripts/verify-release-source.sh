#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

VERSION_VALUE="$(tr -d '[:space:]' < VERSION)"
HEAD_SHA="$(git rev-parse HEAD 2>/dev/null || printf 'unknown')"
SIGN_SHA="$(shasum -a 256 scripts/sign-macos-release.sh | awk '{print $1}')"
PROFILE_CODE_SHA="$(shasum -a 256 mac_release_signing.py | awk '{print $1}')"

echo "[release-source] VERSION=$VERSION_VALUE"
echo "[release-source] HEAD=$HEAD_SHA"
echo "[release-source] sign-macos-release.sh SHA256=$SIGN_SHA"
echo "[release-source] mac_release_signing.py SHA256=$PROFILE_CODE_SHA"
echo "[release-source] ref=${GITHUB_REF:-<none>}"
echo "[release-source] ref_name=${GITHUB_REF_NAME:-<none>}"
echo "[release-source] ref_type=${GITHUB_REF_TYPE:-<none>}"

OBSOLETE_PRELOGIN_TEST="tests/test_prelogin_displaystream_capture.py"
if [ -e "$OBSOLETE_PRELOGIN_TEST" ]; then
  echo "ERROR: obsolete CGDisplayStream regression test is still tracked: $OBSOLETE_PRELOGIN_TEST" >&2
  exit 1
fi

if [ "${GITHUB_REF_TYPE:-}" = "tag" ]; then
  EXPECTED_TAG="v${VERSION_VALUE}"
  if [ "${GITHUB_REF_NAME:-}" != "$EXPECTED_TAG" ]; then
    echo "ERROR: release tag ${GITHUB_REF_NAME:-<missing>} does not match VERSION ($EXPECTED_TAG)." >&2
    exit 1
  fi
fi

# Free release path is mandatory. Developer ID is optional and may be enabled only
# when all credentials are supplied by the maintainer.
for marker in \
  'MACOS_RELEASE_SIGNING_MODE=adhoc' \
  'Apple Developer credentials not configured; using free ad-hoc transport signing.' \
  "scripts/sign-macos-transport.sh 'dist/ZorinMacBridge Server.app'" \
  'MACOS_RELEASE_SIGNING_MODE=developer-id'; do
  grep -Fq -- "$marker" .github/workflows/release.yml || {
    echo "ERROR: free/optional macOS signing marker missing from workflow: $marker" >&2
    exit 1
  }
done

for forbidden in \
  "Missing secret: MACOS_DEVELOPER_ID_P12_BASE64" \
  "Missing secret: MACOS_DEVELOPER_ID_P12_PASSWORD" \
  "Missing secret: MACOS_DEVELOPER_ID_PROFILE_BASE64"; do
  if grep -Fq -- "$forbidden" .github/workflows/release.yml; then
    echo "ERROR: paid Apple signing is still mandatory: $forbidden" >&2
    exit 1
  fi
done

# Optional Developer ID helpers remain valid when a maintainer explicitly supplies
# credentials, but the default installer/updater must support free local ad-hoc
# identity without requiring a provisioning profile.
for entitlement in \
  'com.apple.developer.persistent-content-capture' \
  'com.apple.developer.hid.virtual.device'; do
  grep -Fq "$entitlement" mac_release_signing.py || {
    echo "ERROR: optional Developer ID verifier lost entitlement support: $entitlement" >&2
    exit 1
  }
done

grep -Fq -- '--local-sign-app "$STAGED"' install-macos.sh || {
  echo 'ERROR: free installer path does not apply a stable local ad-hoc identity.' >&2
  exit 1
}
grep -Fq 'stage_and_sign' updates.py || {
  echo 'ERROR: free updater path does not apply a stable local ad-hoc identity.' >&2
  exit 1
}
if grep -Fq "'prelogin_release_identity'" mac_requirements.py; then
  echo 'ERROR: READY still requires paid Developer ID/provisioning.' >&2
  exit 1
fi

echo "[release-source] free macOS signing path + optional Developer ID invariants OK"
