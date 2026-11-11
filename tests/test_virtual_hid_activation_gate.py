#!/usr/bin/env python3
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
installer = (ROOT/'native/macos/admin/install-virtual-hid.sh').read_text()
admin = (ROOT/'native/macos/admin/zmb-admin').read_text()
server = (ROOT/'mac_server.py').read_text()
client = (ROOT/'zmb_macos_virtual_hid.py').read_text()

assert "EXTENSION_ID='org.pqrs.Karabiner-DriverKit-VirtualHIDDevice'" in installer
assert 'systemextensionsctl list' in installer
assert "'[activated enabled]'" in installer
assert 'virtual_hid=approval-required' in installer
assert 'virtual_hid=devices-not-ready' in installer
assert 'ZMB_VHID_SELFTEST_WAIT=10' in installer
assert '--self-test-virtual-hid' in installer
assert "echo 'virtual_hid=ready'" in installer
# Readiness must no longer be merely the presence of the daemon socket.
assert 'if [ -S "$SOCKET" ]; then\n    echo \'virtual_hid=ready\'' not in installer

assert 'virtual_hid_extension_ready=' in admin
assert 'virtual_hid_extension_state=' in admin
assert 'virtual_hid_input_detail=' in admin
assert "result'] = 'STARTING'" in server
assert "active == 'driverkit' and rc == RC_NOT_READY" in server
assert 'STATUS_DRIVER_ACTIVATED = 1' in client
assert 'STATUS_DRIVER_CONNECTED = 2' in client
print('virtual HID activation/readiness gate tests: OK')
