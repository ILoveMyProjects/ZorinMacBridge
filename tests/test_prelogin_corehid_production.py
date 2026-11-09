from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SERVER = (ROOT / 'mac_server.py').read_text(encoding='utf-8')
COREHID = (ROOT / 'native/macos/ZMBCoreHIDInput.swift').read_text(encoding='utf-8')
PROBE = (ROOT / 'native/macos/capability-probe/ZMBCapabilityProbe.swift').read_text(encoding='utf-8')
BUILD = (ROOT / 'scripts/build-macos-streamer.sh').read_text(encoding='utf-8')
GATE = (ROOT / 'scripts/verify-macos-native-gate.sh').read_text(encoding='utf-8')

# Production native payload contains real CoreHID virtual keyboard + pointer.
assert 'import CoreHID' in COREHID
assert 'HIDVirtualDevice(properties:' in COREHID
assert 'ZorinMacBridge LoginWindow Keyboard' in COREHID
assert 'ZorinMacBridge LoginWindow Pointer' in COREHID
for symbol in ('zmb_corehid_input_open', 'zmb_corehid_input_close', 'zmb_corehid_input_key', 'zmb_corehid_input_pointer'):
    assert f'@_cdecl("{symbol}")' in COREHID
    assert f'_{symbol}' in GATE

assert '-framework CoreHID' in BUILD
assert 'native/macos/ZMBCoreHIDInput.swift' in BUILD

# LoginWindowInput uses only the CoreHID ABI for production keyboard/pointer.
start = SERVER.index('class LoginWindowInput(MacInput):')
end = SERVER.index('\n\nclass MacClipboard:', start)
login_input = SERVER[start:end]
assert 'zmb_corehid_input_open' in login_input
assert 'zmb_corehid_input_key' in login_input
assert 'zmb_corehid_input_pointer' in login_input
assert 'zmb_login_input_open' not in login_input
assert 'zmb_login_input_key' not in login_input
assert 'zmb_login_input_pointer' not in login_input
assert 'HID_USAGES' in login_input

# Capability probe calls the same production CoreHID ABI and requires both a
# keyboard modifier transition and a real pointer movement before PASS.
assert '@_silgen_name("zmb_corehid_input_open")' in PROBE
assert '@_silgen_name("zmb_corehid_input_key")' in PROBE
assert '@_silgen_name("zmb_corehid_input_pointer")' in PROBE
assert 'input_corehid_keyboard_result' in PROBE
assert 'input_corehid_pointer_result' in PROBE
assert 'let ok = keyboardOK && pointerOK' in PROBE
assert 'report.add("input_corehid_result", ok ? "PASS" : "FAIL")' in PROBE

print('production CoreHID LoginWindow input regression tests: OK')
