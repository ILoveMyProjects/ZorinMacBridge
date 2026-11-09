from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SWIFT = (ROOT / 'native/macos/ZMBStreamerLib.swift').read_text(encoding='utf-8')
SERVER = (ROOT / 'mac_server.py').read_text(encoding='utf-8')
CLIENT = (ROOT / 'linux_client.py').read_text(encoding='utf-8')
PROBE = (ROOT / 'native/macos/capability-probe/ZMBCapabilityProbe.swift').read_text(encoding='utf-8')

assert 'CGDisplayStream' not in SWIFT
assert 'private func startPreloginScreenCaptureKit() throws' in SWIFT
assert 'SCShareableContent.getExcludingDesktopWindows(false, onScreenWindowsOnly: false)' in SWIFT
assert 'SCStream(filter: filter, configuration: config, delegate: self)' in SWIFT
assert 'prelogin-backend=screencapturekit-persistent' in SWIFT
assert '/usr/sbin/screencapture' not in SWIFT.replace('do not fall back to /usr/sbin/screencapture', '')

# The physical capability probe now proves an actual SCStream first frame, not
# just a separate screenshot API.
assert 'private final class ProbeStreamReceiver' in PROBE
assert 'waitForStreamFrame(filter: filter, config: config, timeout: 5.0)' in PROBE
assert 'capture_scstream_first_frame' in PROBE

# The Python/native ABI tells Swift which capture mode it is in.
assert '1 if prelogin else 0' in SERVER
assert '_ prelogin: Int32' in SWIFT

# Pre-login must not bypass Screen Recording preflight silently.
assert 'permission = screen_capture_permission_status()' in SERVER
assert 'permission = None if prelogin else' not in SERVER

# Backend selection and native failures reach the client.
assert "line.startswith('prelogin-backend=')" in SERVER
assert "line.startswith('prelogin-failure=')" in SERVER
assert "Pre-login capture backend: {event.backend}." in CLIENT
assert "Pre-login capture failed: {event.reason}." in CLIENT

print('managed pre-login SCStream regression tests: OK')
