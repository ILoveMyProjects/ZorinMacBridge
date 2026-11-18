#!/usr/bin/env python3
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SERVER = (ROOT / 'mac_server.py').read_text()
CLIENT = (ROOT / 'linux_client.py').read_text()
GTK = (ROOT / 'linux_client_gtk.py').read_text()
LIFECYCLE = (ROOT / 'zmb_session_lifecycle.py').read_text()
C = (ROOT / 'native/macos/ZMBLoginInput.c').read_text()
PROBE = (ROOT / 'native/macos/capability-probe/ZMBCapabilityProbe.swift').read_text()
ADMIN = (ROOT / 'native/macos/admin/zmb-admin').read_text()
GATE = (ROOT / 'scripts/verify-macos-native-gate.sh').read_text()
SOURCE_GATE = (ROOT / 'scripts/verify-release-source.sh').read_text()
# Input matrix remains independent from the pre-login capture matrix and wake path.
assert 'screencapturekit-persistent' in (ROOT / 'native/macos/ZMBStreamerLib.swift').read_text()
assert 'ScreenCaptureKit reported no display; requesting one-shot display wake through paired P0.' in GTK

# Runtime uses bounded exact backend activation and real-event failover.
for marker in (
    "('corehid', 0)",
    "('iohidsystem', 2)",
    "('userdevice', 1)",
    "('quartz', 3)",
    '_activate_from_index',
    '_dispatch_with_failover',
    'zmb_login_input_open_backend',
    'OPEN_READY',
):
    assert marker in SERVER, marker

# C exact-open API prevents IOHIDSystem from silently falling through to Quartz
# while it is being tested.
assert 'int32_t zmb_login_input_open_backend(int32_t backend)' in C
assert 'zmb_open_backend_locked' in C
assert 'if (backend == ZMB_LOGIN_INPUT_QUARTZ)' in C
assert '_zmb_login_input_open_backend' in GATE

# Activation/runtime results travel over the existing SESSION_STATE channel.
assert "'state': 'input-probe'" in LIFECYCLE
assert "event.kind == 'input-probe'" in CLIENT
assert 'Pre-login input check:' in CLIENT
assert 'make_input_probe_state' in SERVER

# Physical capability probe uses the same exact matrix and reports selection.
assert 'runExactLegacyInputProbe(backend: 1, name: "userdevice"' in PROBE
assert 'runExactLegacyInputProbe(backend: 2, name: "iohidsystem"' in PROBE
assert 'runExactLegacyInputProbe(backend: 3, name: "quartz"' in PROBE
assert 'input_selected_backend' in PROBE
assert 'let anyInputOK = selectedBackend != "none"' in PROBE

# Admin status exposes the last physical matrix instead of only CoreHID.
for key in (
    'prelogin_probe_input_result',
    'prelogin_probe_input_selected',
    'prelogin_probe_input_corehid',
    'prelogin_probe_input_userdevice',
    'prelogin_probe_input_iohidsystem',
    'prelogin_probe_input_quartz',
):
    assert key in ADMIN, key

assert 'reboot LoginWindow input matrix' in SOURCE_GATE
print('reboot LoginWindow evidence matrix regression tests: OK')
