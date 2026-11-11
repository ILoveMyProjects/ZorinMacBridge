#!/usr/bin/env python3
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
C = (ROOT / 'native/macos/ZMBLoginInput.c').read_text()
SERVER = (ROOT / 'mac_server.py').read_text()
GATE = (ROOT / 'scripts/verify-macos-native-gate.sh').read_text()

assert '#include <IOKit/hid/IOHIDUserDevice.h>' not in C
assert '#include <dlfcn.h>' in C
assert 'typedef struct __IOHIDUserDevice *IOHIDUserDeviceRef;' in C
assert 'dlsym(' in C
assert '"IOHIDUserDeviceCreate"' in C
assert '"IOHIDUserDeviceHandleReport"' in C
assert 'g_zmb_user_device_create' in C
assert 'g_zmb_user_device_handle_report' in C
assert 'ZMB_LOGIN_INPUT_USER_DEVICE' in C
assert 'zmb_login_input_backend' in C
assert 'zmb_login_input_open_backend' in C
assert 'zmb_login_input_hid_key' in C
assert 'zmb_login_input_hid_pointer' in C
assert "('userdevice', 1)" in SERVER
assert '_probe_keyboard_delivery' in SERVER
assert '_probe_pointer_delivery' in SERVER
assert "IOHIDUserDevice virtual HID" in SERVER
assert '_zmb_login_input_backend' in GATE
assert '_zmb_login_input_open_backend' in GATE
assert '_zmb_login_input_hid_key' in GATE
assert '_zmb_login_input_hid_pointer' in GATE

print('pre-login IOHIDUserDevice source contract: OK')
