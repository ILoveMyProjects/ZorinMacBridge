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

# Production LoginWindow keeps ScreenCaptureKit SCStream/CoreHID as primary.
# Capture may fall through supported ScreenCaptureKit screenshot APIs only after
# the prior candidate fails to write a real H.264 access unit. Input fallback is
# independently delivery-probed.
grep -Fq 'screencapturekit-persistent' native/macos/ZMBStreamerLib.swift || {
  echo 'ERROR: production pre-login video lost its primary ScreenCaptureKit backend.' >&2
  exit 1
}
grep -Fq 'startPreloginStreamBounded' native/macos/ZMBStreamerLib.swift || {
  echo 'ERROR: pre-login SCStream startup lost its bounded startup contract.' >&2
  exit 1
}
for marker in 'startPreloginCaptureMatrix()' 'SCScreenshotManager.captureSampleBuffer' 'SCScreenshotManager.captureImage(in: rect)' 'preloginAccessUnitWritten = true'; do
  grep -Fq "$marker" native/macos/ZMBStreamerLib.swift || {
    echo "ERROR: pre-login capture matrix marker missing: $marker" >&2
    exit 1
  }
done
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
grep -Fq 'zmb_login_input_open_backend' mac_server.py || {
  echo 'ERROR: reboot LoginWindow input matrix cannot open exact native backends.' >&2
  exit 1
}
grep -Fq '_probe_keyboard_delivery' mac_server.py || {
  echo 'ERROR: reboot LoginWindow input matrix lost keyboard delivery proof.' >&2
  exit 1
}
grep -Fq '_probe_pointer_delivery' mac_server.py || {
  echo 'ERROR: reboot LoginWindow input matrix lost pointer delivery proof.' >&2
  exit 1
}
grep -Fq 'Pre-login input check:' linux_client.py || {
  echo 'ERROR: Linux client no longer exposes reboot input matrix results.' >&2
  exit 1
}
if grep -Fq '#include <IOKit/hid/IOHIDUserDevice.h>' native/macos/ZMBLoginInput.c; then
  echo 'ERROR: Xcode 26.6 SDK does not ship IOHIDUserDevice.h; use runtime symbol resolution.' >&2
  exit 1
fi
grep -Fq 'dlsym(' native/macos/ZMBLoginInput.c || {
  echo 'ERROR: IOHIDUserDevice backend lost macOS 26-compatible runtime symbol resolution.' >&2
  exit 1
}
grep -Fq '"IOHIDUserDeviceCreate"' native/macos/ZMBLoginInput.c || {
  echo 'ERROR: LoginWindow fallback lost IOHIDUserDeviceCreate runtime lookup.' >&2
  exit 1
}
grep -Fq '"IOHIDUserDeviceHandleReport"' native/macos/ZMBLoginInput.c || {
  echo 'ERROR: LoginWindow fallback lost IOHIDUserDeviceHandleReport runtime lookup.' >&2
  exit 1
}
grep -Fq 'zmb_login_input_hid_key' mac_server.py || {
  echo 'ERROR: production LoginWindow keyboard is not wired to IOHIDUserDevice.' >&2
  exit 1
}
grep -Fq 'zmb_login_input_hid_pointer' mac_server.py || {
  echo 'ERROR: production LoginWindow pointer is not wired to IOHIDUserDevice.' >&2
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
grep -Fq 'input_selected_backend' native/macos/capability-probe/ZMBCapabilityProbe.swift || {
  echo 'ERROR: physical capability probe no longer reports the proven LoginWindow input backend.' >&2
  exit 1
}
for backend in userdevice iohidsystem quartz; do
  grep -Fq "name: \"${backend}\"" native/macos/capability-probe/ZMBCapabilityProbe.swift || {
    echo "ERROR: physical capability probe no longer checks ${backend}." >&2
    exit 1
  }
done
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

# 0.15.36: cold-boot LoginWindow must be supervised by system P0, not by a
# client-triggered recover-prelogin request.
grep -Fq 'autoheal_prelogin()' native/macos/admin/zmb-admin || {
  echo 'ERROR: P0 no longer has autonomous LoginWindow autoheal.' >&2; exit 1;
}
grep -Fq 'PRELOGIN_AUTOHEAL_RECOVERED' native/macos/admin/zmb-admin || {
  echo 'ERROR: P0 LoginWindow recovery no longer verifies restored readiness.' >&2; exit 1;
}
grep -Fq '/bin/sleep 5' native/macos/admin/zmb-system-daemon || {
  echo 'ERROR: P0 boot supervision cadence regressed.' >&2; exit 1;
}

# 0.15.37: LoginWindow native lifecycle must be observable and replacement-safe.
grep -Fq 'prelogin-stage=native-run-enter' native/macos/ZMBStreamerLib.swift || {
  echo 'ERROR: pre-login native runner no longer emits its entry stage.' >&2; exit 1;
}
grep -Fq 'try startPreloginCaptureMatrix()' native/macos/ZMBStreamerLib.swift || {
  echo 'ERROR: pre-login capture matrix no longer runs directly on the native runner.' >&2; exit 1;
}
grep -Fq '_ACTIVE_VIDEO_CANCEL' mac_server.py || {
  echo 'ERROR: video replacement cancel-event is missing.' >&2; exit 1;
}
grep -Fq "raise ConnectionError('video session cancelled for replacement')" mac_server.py || {
  echo 'ERROR: native pipe reader no longer honors replacement cancellation.' >&2; exit 1;
}
grep -Fq "state': 'capture-progress'" zmb_session_lifecycle.py || {
  echo 'ERROR: pre-login diagnostic capture-progress wire state is missing.' >&2; exit 1;
}
grep -Fq 'ZMBNativeBuildVersion.swift' scripts/build-macos-streamer.sh || {
  echo 'ERROR: native streamer build version is not embedded at compile time.' >&2; exit 1;
}
grep -Fq 'zmb_streamer_build_version' mac_server.py || {
  echo 'ERROR: live server no longer verifies native streamer build version.' >&2; exit 1;
}

echo "[release-source] resilient Aqua + LoginWindow native input fallback gate OK"

# Marker used by readiness regression tests: pre-login READY requires Apple-managed Developer ID/provisioning.
# resilient Aqua fallback remains supported alongside LoginWindow native input fallback.

# Distribution license gate: tagged/source releases must preserve the project
# no-sale policy and canonical source attribution.
grep -Fq 'ZorinMacBridge No-Sale Source Attribution License 1.0' LICENSE || {
  echo 'ERROR: project no-sale license is missing.' >&2
  exit 1
}
grep -Fq 'NO SALE OR PAID REDISTRIBUTION' LICENSE || {
  echo 'ERROR: no-sale license condition is missing.' >&2
  exit 1
}
for file in LICENSE NOTICE README.md; do
  grep -Fq 'https://github.com/ILoveMyProjects/ZorinMacBridge' "$file" || {
    echo "ERROR: canonical source attribution missing from $file." >&2
    exit 1
  }
done
if grep -Fq 'MIT License' LICENSE || grep -Fq 'MIT — see [LICENSE](LICENSE).' README.md; then
  echo 'ERROR: obsolete MIT distribution terms remain active.' >&2
  exit 1
fi
for marker in \
  'install -m 0644 LICENSE NOTICE "$PKG/usr/share/doc/zorinmacbridge/"' \
  "--add-data 'LICENSE:.'" \
  "--add-data 'NOTICE:.'" \
  'cp LICENSE NOTICE build/dmgroot/'; do
  grep -Fq -- "$marker" .github/workflows/release.yml || {
    echo "ERROR: release package does not embed license/source attribution: $marker" >&2
    exit 1
  }
done

# 0.15.36: never allow client/server version skew to become invisible again.
grep -Fq "'runtime_version': runtime_version()" mac_server.py || {
  echo 'ERROR: AUTH_OK no longer exposes Mac runtime_version.' >&2; exit 1;
}
grep -Fq 'Pre-login runtime version skew:' linux_client.py || {
  echo 'ERROR: Linux client no longer reports pre-login runtime version skew.' >&2; exit 1;
}
grep -Fq '"version=$version"' native/macos/admin/zmb-system-daemon || {
  echo 'ERROR: P0 Bonjour no longer advertises the installed Mac app version.' >&2; exit 1;
}
grep -Fq 'needs: [release-source-check, macos-native-gate]' .github/workflows/release.yml || {
  echo 'ERROR: Linux release artifact is no longer gated by the macOS native build.' >&2; exit 1;
}
