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

# A free Aqua-only release path remains mandatory. Developer ID is optional for
# normal Aqua operation, but reboot-to-LoginWindow READY requires the managed
# pre-login capabilities carried by the Developer ID provisioning path.
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

# Developer ID helpers must preserve both managed pre-login capabilities while
# the default Aqua-only installer/updater still supports free local ad-hoc identity.
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
grep -Fq "'prelogin_release_identity'" mac_requirements.py || {
  echo 'ERROR: pre-login READY does not require Apple-managed Developer ID/provisioning.' >&2
  exit 1
}
grep -Fq "'prelogin_release_identity_ready'" mac_requirements.py || {
  echo 'ERROR: pre-login readiness is not wired to the verified managed-capability identity.' >&2
  exit 1
}
grep -Fq "proof.get('input_corehid_result') == 'PASS'" mac_admin_setup.py || {
  echo 'ERROR: pre-login proof still permits a non-CoreHID input path.' >&2
  exit 1
}
grep -Fq 'Refusing to replace a pre-login-capable Developer ID installation with a free/ad-hoc build.' updates.py || {
  echo 'ERROR: updater can silently downgrade a pre-login-capable installation.' >&2
  exit 1
}
grep -Fq 'Refusing to replace a pre-login-capable Developer ID installation with a free/ad-hoc build.' install-macos.sh || {
  echo 'ERROR: installer can silently downgrade a pre-login-capable installation.' >&2
  exit 1
}

echo "[release-source] free Aqua-only signing path + managed pre-login capability gate OK"
# Marker used by regression tests: pre-login READY requires Apple-managed Developer ID/provisioning.
