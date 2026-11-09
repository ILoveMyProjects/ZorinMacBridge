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

# Release publication must be resilient: Aqua/ad-hoc remains a working fallback,
# while a valid Developer ID + provisioning profile upgrades the same artifact
# to the managed pre-login capability tier. Managed-signing setup failure must
# not abort the whole tagged release.
for marker in \
  'MACOS_RELEASE_SIGNING_MODE=adhoc' \
  'Managed Developer ID signing unavailable or invalid; falling back to the working Aqua/ad-hoc macOS artifact.' \
  "scripts/sign-macos-transport.sh 'dist/ZorinMacBridge Server.app'" \
  'MACOS_RELEASE_SIGNING_MODE=developer-id' \
  'continue-on-error: true' \
  'steps.managed_signing.outcome'; do
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

# Production LoginWindow keeps managed ScreenCaptureKit/CoreHID as primary,
# but CoreHID creation failure must not kill the control session.  The bundled
# native IOHIDSystem/kCGSessionEventTap bridge is the in-process fallback.
grep -Fq 'prelogin-backend=screencapturekit-persistent' native/macos/ZMBStreamerLib.swift || {
  echo 'ERROR: production pre-login video is not using managed ScreenCaptureKit.' >&2
  exit 1
}
grep -Fq 'startPreloginStreamBounded' native/macos/ZMBStreamerLib.swift || {
  echo 'ERROR: pre-login SCStream startup lost its bounded startup contract.' >&2
  exit 1
}
if grep -Fq 'screencapture-cli' native/macos/ZMBStreamerLib.swift || grep -Fq '/usr/sbin/screencapture' native/macos/ZMBStreamerLib.swift; then
  echo 'ERROR: obsolete LoginWindow system-screenshot backend returned to production.' >&2
  exit 1
fi
grep -Fq 'native/macos/ZMBCoreHIDInput.swift' scripts/build-macos-streamer.sh || {
  echo 'ERROR: production streamer no longer compiles the CoreHID LoginWindow input backend.' >&2
  exit 1
}
grep -Fq 'zmb_corehid_input_open' mac_server.py || {
  echo 'ERROR: production LoginWindow input is not wired to CoreHID.' >&2
  exit 1
}
grep -Fq 'zmb_corehid_input_pointer' mac_server.py || {
  echo 'ERROR: production LoginWindow pointer is not wired to CoreHID.' >&2
  exit 1
}
grep -Fq 'native/macos/ZMBLoginInput.c' scripts/build-macos-streamer.sh || {
  echo 'ERROR: production streamer no longer compiles the LoginWindow native input fallback.' >&2
  exit 1
}
grep -Fq 'zmb_login_input_open' mac_server.py || {
  echo 'ERROR: production LoginWindow input fallback is not wired.' >&2
  exit 1
}
grep -Fq 'kCGSessionEventTap' native/macos/ZMBLoginInput.c || {
  echo 'ERROR: LoginWindow native fallback lost the session event-tap path.' >&2
  exit 1
}
grep -Fq 'capture_scstream_first_frame' native/macos/capability-probe/ZMBCapabilityProbe.swift || {
  echo 'ERROR: physical capability probe no longer proves an SCStream first frame.' >&2
  exit 1
}
grep -Fq 'input_corehid_pointer_result' native/macos/capability-probe/ZMBCapabilityProbe.swift || {
  echo 'ERROR: physical capability probe no longer proves CoreHID pointer delivery.' >&2
  exit 1
}
for symbol in zmb_corehid_input_open zmb_corehid_input_close zmb_corehid_input_key zmb_corehid_input_pointer; do
  if grep -Fq "@_silgen_name(\"${symbol}\")" native/macos/capability-probe/ZMBCapabilityProbe.swift; then
    echo "ERROR: capability probe redeclares same-module CoreHID symbol ${symbol}." >&2
    exit 1
  fi
done
grep -Fq 'Refusing to replace a pre-login-capable Developer ID installation with a free/ad-hoc build.' updates.py || {
  echo 'ERROR: updater can silently downgrade a pre-login-capable installation.' >&2
  exit 1
}
grep -Fq 'Refusing to replace a pre-login-capable Developer ID installation with a free/ad-hoc build.' install-macos.sh || {
  echo 'ERROR: installer can silently downgrade a pre-login-capable installation.' >&2
  exit 1
}

echo "[release-source] resilient Aqua + LoginWindow native input fallback gate OK"

# Marker used by readiness regression tests: pre-login READY requires Apple-managed Developer ID/provisioning.
# resilient Aqua fallback remains supported alongside LoginWindow native input fallback.
