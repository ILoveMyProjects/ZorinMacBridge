from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PROBE = (ROOT / 'native/macos/capability-probe/ZMBCapabilityProbe.swift').read_text(encoding='utf-8')
COREHID = (ROOT / 'native/macos/ZMBCoreHIDInput.swift').read_text(encoding='utf-8')
BUILD = (ROOT / 'scripts/build-macos-capability-probe.sh').read_text(encoding='utf-8')

symbols = (
    'zmb_corehid_input_open',
    'zmb_corehid_input_close',
    'zmb_corehid_input_key',
    'zmb_corehid_input_pointer',
)

# The probe and CoreHID implementation are passed to one swiftc invocation.
assert 'ZMBCapabilityProbe.swift' in BUILD
assert 'ZMBCoreHIDInput.swift' in BUILD

# CoreHID owns the Swift declarations and exports their C ABI.
for symbol in symbols:
    assert f'@_cdecl("{symbol}")' in COREHID
    # Same-module redeclaration through @_silgen_name caused Xcode 26:
    # "invalid redeclaration of zmb_corehid_input_*".
    assert f'@_silgen_name("{symbol}")' not in PROBE

# External C legacy diagnostic symbols still require Swift declarations.
assert '@_silgen_name("zmb_login_input_open")' in PROBE
assert '@_silgen_name("zmb_login_input_close")' in PROBE
assert '@_silgen_name("zmb_login_input_key")' in PROBE

# Probe still exercises the production CoreHID ABI directly.
assert 'zmb_corehid_input_open()' in PROBE
assert 'zmb_corehid_input_key(0xE1, 1)' in PROBE
assert 'zmb_corehid_input_pointer(' in PROBE

print('capability-probe CoreHID same-module linkage regression tests: OK')
