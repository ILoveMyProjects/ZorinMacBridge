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

if [ "${GITHUB_REF_TYPE:-}" = "tag" ]; then
  EXPECTED_TAG="v${VERSION_VALUE}"
  if [ "${GITHUB_REF_NAME:-}" != "$EXPECTED_TAG" ]; then
    echo "ERROR: release tag ${GITHUB_REF_NAME:-<missing>} does not match VERSION ($EXPECTED_TAG)." >&2
    exit 1
  fi
fi

for marker in \
  'MACOS_DEVELOPER_ID_P12_BASE64' \
  'MACOS_DEVELOPER_ID_PROFILE_BASE64' \
  '--codesign-identity "$MACOS_CODESIGN_IDENTITY"' \
  'scripts/sign-macos-release.sh'; do
  grep -Fq -- "$marker" .github/workflows/release.yml || {
    echo "ERROR: Stage C release marker missing from workflow: $marker" >&2
    exit 1
  }
done

for entitlement in \
  'com.apple.developer.persistent-content-capture' \
  'com.apple.developer.hid.virtual.device'; do
  grep -Fq "$entitlement" mac_release_signing.py || {
    echo "ERROR: required release entitlement missing from verifier: $entitlement" >&2
    exit 1
  }
  grep -Fq "$entitlement" native/macos/signing/ZorinMacBridge.release-entitlements.plist || {
    echo "ERROR: required release entitlement missing from manifest: $entitlement" >&2
    exit 1
  }
done

grep -Fq 'Contents/embedded.provisionprofile' scripts/sign-macos-release.sh || {
  echo 'ERROR: release signing does not embed the provisioning profile.' >&2
  exit 1
}
grep -Fq -- '--options runtime' scripts/sign-macos-release.sh || {
  echo 'ERROR: release signing does not enable hardened runtime.' >&2
  exit 1
}
grep -Fq -- '--timestamp' scripts/sign-macos-release.sh || {
  echo 'ERROR: release signing does not request a secure timestamp.' >&2
  exit 1
}
if grep -Fq 'codesign --force --deep' scripts/sign-macos-release.sh; then
  echo 'ERROR: release signer must not use --deep while signing.' >&2
  exit 1
fi

# Stage C invariant: a production app is never converted back to ad-hoc after
# download. The legacy local DR remains source/dev-only.
if grep -Fq -- '--local-sign-app' install-macos.sh; then
  echo 'ERROR: installer still locally re-signs a production release.' >&2
  exit 1
fi
if grep -Fq 'stage_and_sign' updates.py; then
  echo 'ERROR: updater still locally re-signs a production release.' >&2
  exit 1
fi
grep -Fq 'app_has_release_identity' mac_local_signing.py || {
  echo 'ERROR: legacy local-sign bootstrap does not protect provisioned releases.' >&2
  exit 1
}

echo "[release-source] Stage C source invariants OK"
