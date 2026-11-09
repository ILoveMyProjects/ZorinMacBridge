from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

probe = (ROOT / 'native/macos/capability-probe/ZMBCapabilityProbe.swift').read_text(encoding='utf-8')
corehid = (ROOT / 'native/macos/ZMBCoreHIDInput.swift').read_text(encoding='utf-8')
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

# Stage B runs inside the exact existing app identity, not as a helper executable.
assert "--capability-probe" in gui
assert "CapabilityProbeLibrary" in server
assert "libzmb_capability_probe.dylib" in server
assert "--add-binary 'build/libzmb_capability_probe.dylib:.'" in release
assert "--self-test-capability-probe-load" in release

# CoreHID is now both the production LoginWindow input backend and the physical
# proof backend. Both native dylibs compile the exact same implementation file.
assert 'CoreHID.framework' in probe_build
assert '-weak_framework' not in probe_build
assert '-framework CoreHID' in streamer_build
assert 'native/macos/ZMBCoreHIDInput.swift' in streamer_build
assert 'native/macos/ZMBCoreHIDInput.swift' in probe_build
assert 'scripts/build-macos-capability-probe.sh' in gate
assert '_zmb_capability_probe_run' in gate
assert '_zmb_corehid_input_open' in gate
assert '_zmb_corehid_input_pointer' in gate
assert 'CoreHID.framework' in gate

# Capture proof must prove an actual SCStream first frame, save it, and reject a
# uniform/black result.
for marker in (
    'ProbeStreamReceiver',
    'SCStream(filter: filter, configuration: config, delegate: receiver)',
    'capture_scstream_first_frame',
    'capture_png_sha256',
    'capture_luma_range',
    'FAIL_BLACK_OR_UNIFORM',
    'capture_remote_desktop_authorization_query',
    'UNAVAILABLE_PUBLIC_API',
):
    assert marker in probe, marker

# Input proof calls the same production CoreHID ABI and proves keyboard+pointer.
for marker in (
    'zmb_corehid_input_open()',
    'zmb_corehid_input_key(0xE1, 1)',
    'zmb_corehid_input_pointer(',
    'input_corehid_keyboard_result',
    'input_corehid_pointer_result',
    'CGEventSource.flagsState',
):
    assert marker in probe, marker
# ZMBCapabilityProbe.swift and ZMBCoreHIDInput.swift are compiled into the same
# Swift module. Redeclaring the @cdecl CoreHID symbols through @_silgen_name
# causes an invalid-redeclaration failure on the macOS 26/Xcode 26 native gate.
for symbol in ('zmb_corehid_input_open', 'zmb_corehid_input_close', 'zmb_corehid_input_key', 'zmb_corehid_input_pointer'):
    assert f'@_silgen_name("{symbol}")' not in probe
assert 'HIDVirtualDevice(properties:' in corehid
assert 'ZorinMacBridge LoginWindow Keyboard' in corehid
assert 'ZorinMacBridge LoginWindow Pointer' in corehid
assert 'password' not in probe.lower()

# Current IOHID/Quartz remains diagnostic comparison only and cannot make the
# managed pre-login proof PASS without the production CoreHID path.
assert 'zmb_login_input_open' in probe
assert 'input_legacy_result' in probe
assert 'if captureOK && coreHIDOK' in probe
assert 'input_result", coreHIDOK ? "PASS" : "FAIL"' in probe

# Probe remains remotely callable only through the restricted admin path.
assert 'capability-probe' in admin
assert '"zmb-admin capability-probe"' in admin_ssh
assert '/usr/local/sbin/zmb-admin capability-probe' in install_admin
assert '"capability-probe"' in backend
assert 'prelogin_console_active || fail' in admin
assert '--verify-release-app "$APP_PATH"' in admin
assert 'Persistent Content Capture and CoreHID Virtual Device entitlements' in admin
assert 'LimitLoadToSessionType' in admin and 'LoginWindow' in admin

# Target runtime is macOS 26+ and both native paths require CoreHID.
assert 'runs-on: macos-26' in release
assert 'MACOSX_DEPLOYMENT_TARGET=26.0' in probe_build
assert 'arm64-apple-macos26.0' in probe_build
assert 'import CoreHID' in corehid
assert '#if canImport(CoreHID)' not in corehid
assert 'MACOSX_DEPLOYMENT_TARGET=26.0' in streamer_build

print('Stage B production-aligned pre-login capability probe regression checks passed')
