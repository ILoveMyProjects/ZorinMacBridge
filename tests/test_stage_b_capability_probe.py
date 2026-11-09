from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

probe = (ROOT / 'native/macos/capability-probe/ZMBCapabilityProbe.swift').read_text(encoding='utf-8')
probe_build = (ROOT / 'scripts/build-macos-capability-probe.sh').read_text(encoding='utf-8')
gate = (ROOT / 'scripts/verify-macos-native-gate.sh').read_text(encoding='utf-8')
streamer_build = (ROOT / 'scripts/build-macos-streamer.sh').read_text(encoding='utf-8')
server = (ROOT / 'mac_server.py').read_text(encoding='utf-8')
gui = (ROOT / 'mac_server_gui.py').read_text(encoding='utf-8')
admin = (ROOT / 'native/macos/admin/zmb-admin').read_text(encoding='utf-8')
admin_ssh = (ROOT / 'native/macos/admin/zmb-admin-ssh').read_text(encoding='utf-8')
install_admin = (ROOT / 'native/macos/admin/install-admin.sh').read_text(encoding='utf-8')
backend = (ROOT / 'zmb_admin_backend.py').read_text(encoding='utf-8')
release = (ROOT / '.github/workflows/release.yml').read_text(encoding='utf-8')

# Stage B must run under the exact existing app identity, not as a helper executable.
assert "--capability-probe" in gui
assert "CapabilityProbeLibrary" in server
assert "libzmb_capability_probe.dylib" in server
assert "--add-binary 'build/libzmb_capability_probe.dylib:.'" in release
assert "--self-test-capability-probe-load" in release

# CoreHID is the required pre-login input proof, but Stage 1 still keeps it isolated from the normal Aqua streamer dylib.
assert 'CoreHID.framework' in probe_build
assert '-weak_framework' not in probe_build
assert '-framework CoreHID' not in streamer_build
assert 'scripts/build-macos-capability-probe.sh' in gate
assert '_zmb_capability_probe_run' in gate
assert "CoreHID.framework" in gate
assert "CoreHID probe support: required and linked for macOS 26+ target" in gate

# The capture proof must save a real screenshot and reject a uniform/black frame.
for marker in (
    'SCScreenshotManager.captureImage',
    'capture_png_sha256',
    'capture_luma_range',
    'FAIL_BLACK_OR_UNIFORM',
    'capture_remote_desktop_authorization_query',
    'UNAVAILABLE_PUBLIC_API',
):
    assert marker in probe, marker

# The input proof must use a virtual keyboard and only a harmless Shift down/up report.
for marker in (
    'HIDVirtualDevice',
    'dispatchInputReport',
    'entitlement_hid_virtual_device',
    'CGEventSource.flagsState',
    'ZMB Capability Probe Keyboard',
):
    assert marker in probe, marker
assert 'password' not in probe.lower()
assert 'Data([0x02, 0, 0, 0, 0, 0, 0, 0])' in probe

# Current IOHID/Quartz input remains a diagnostic comparison only. It must not
# be allowed to turn the managed pre-login probe PASS without CoreHID.
assert 'zmb_login_input_open' in probe
assert 'input_legacy_result' in probe
assert 'if captureOK && coreHIDOK' in probe
assert 'input_result", coreHIDOK ? "PASS" : "FAIL"' in probe

# Probe is remotely callable only through the already-restricted admin command path.
assert 'capability-probe' in admin
assert '"zmb-admin capability-probe"' in admin_ssh
assert '/usr/local/sbin/zmb-admin capability-probe' in install_admin
assert '"capability-probe"' in backend
assert 'prelogin_console_active || fail' in admin
assert '--verify-release-app "$APP_PATH"' in admin
assert 'Persistent Content Capture and CoreHID Virtual Device entitlements' in admin
assert 'LimitLoadToSessionType' in admin and 'LoginWindow' in admin

# Target runtime is macOS 26+. CoreHID is a required SDK/runtime dependency.
assert 'runs-on: macos-26' in release
assert 'MACOSX_DEPLOYMENT_TARGET=26.0' in probe_build
assert 'arm64-apple-macos26.0' in probe_build
assert 'import CoreHID' in probe
assert '#if canImport(CoreHID)' not in probe
assert 'UNAVAILABLE_SDK' not in probe
assert 'UNAVAILABLE_OS' not in probe
assert 'MACOSX_DEPLOYMENT_TARGET=26.0' in streamer_build

print('Stage B pre-login capability probe regression checks passed')
