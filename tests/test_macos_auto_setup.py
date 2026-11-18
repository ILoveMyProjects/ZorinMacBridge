#!/usr/bin/env python3
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
gui = (ROOT / 'mac_server_gui.py').read_text(encoding='utf-8')
admin_py = (ROOT / 'mac_admin_setup.py').read_text(encoding='utf-8')
installer = (ROOT / 'native/macos/admin/install-admin.sh').read_text(encoding='utf-8')
vhid = (ROOT / 'native/macos/admin/install-virtual-hid.sh').read_text(encoding='utf-8')

# P0 no longer runs interactive DriverKit activation in its privileged critical path.
assert 'install-virtual-hid.sh" --prepare' in installer
assert 'VIRTUAL_HID_OUT=' in installer
assert 'virtual_hid=prepared-approval-required' in vhid
assert 'MODE="${1:-full}"' in vhid
assert '--prepare|--finalize' in vhid

# Remote Login cannot hold the administrator command forever.
assert 'SYSTEMSETUP_PID=$!' in installer
assert '/usr/bin/seq 1 30' in installer
assert 'Remote Login automatic enable timed out after 15 seconds' in installer

# Activation is submitted from the normal Aqua app and then monitored automatically.
assert 'def request_virtual_hid_activation(*, force: bool = False)' in admin_py
assert 'start_new_session=True' in admin_py
assert "action = 'forceActivate' if force else 'activate'" in admin_py
assert 'def ensure_virtual_hid_ready(self, *, on_complete=None)' in gui
assert 'request_virtual_hid_activation(force=False)' in gui
assert 'request_virtual_hid_activation(force=True)' in gui
assert 'self.root.after(1500, poll_activation)' in gui
assert 'self.open_driver_extensions_settings()' in gui
assert 'finalize_virtual_hid()' in gui

# Finish is gated by actual installable readiness, not merely Aqua readiness.
assert 'def _setup_assistant_can_finish(self) -> bool' in gui
assert "state.get('virtual_hid_input_ready')" in gui
assert 'Setup Assistant will not close while required ZMB services or Virtual HID input are not ready.' in gui
assert 'Use Enable / Repair once to install/activate the bundled virtual HID driver.' not in gui

# Admin status uses the privileged self-test when P0 is present.
assert "['/usr/bin/sudo', '-n', str(ADMIN_HELPER), 'status']" in admin_py
assert "root_values['virtual_hid_input_ready'] == '1'" in admin_py

print('macOS automatic P0/Virtual HID setup regression tests: OK')
