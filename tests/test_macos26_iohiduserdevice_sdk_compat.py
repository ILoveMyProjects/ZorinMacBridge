#!/usr/bin/env python3
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
c = (ROOT / 'native/macos/ZMBLoginInput.c').read_text()
gate = (ROOT / 'scripts/verify-release-source.sh').read_text()

assert '#include <IOKit/hid/IOHIDUserDevice.h>' not in c
assert 'typedef struct __IOHIDUserDevice *IOHIDUserDeviceRef;' in c
assert 'dlopen(' in c
assert 'dlsym(' in c
assert '"IOHIDUserDeviceCreate"' in c
assert '"IOHIDUserDeviceHandleReport"' in c
assert 'return (int32_t)kIOReturnUnsupported;' in c
assert "Xcode 26.6 SDK does not ship IOHIDUserDevice.h" in gate

print('macOS 26 IOHIDUserDevice SDK compatibility contract: OK')
