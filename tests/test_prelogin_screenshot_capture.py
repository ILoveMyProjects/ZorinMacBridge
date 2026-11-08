from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SWIFT = (ROOT / 'native/macos/ZMBStreamerLib.swift').read_text(encoding='utf-8')
SERVER = (ROOT / 'mac_server.py').read_text(encoding='utf-8')

# LoginWindow on supported macOS 26+ uses the ScreenCaptureKit screenshot API.
assert 'SCScreenshotManager.captureSampleBuffer' in SWIFT
assert 'prelogin-capture-mode=screenshot-manager' in SWIFT
assert 'if prelogin {' in SWIFT
assert 'runPreloginScreenshotLoop' in SWIFT
assert 'CGDisplayStream' not in SWIFT
assert 'macos<14' not in SWIFT
assert '#available(macOS 14.0' not in SWIFT

# The Python/native ABI must tell the Swift streamer which capture mode it is in.
assert '1 if prelogin else 0' in SERVER
assert '_ prelogin: Int32' in SWIFT

# Pre-login must not skip the Screen Recording preflight silently.
assert 'permission = screen_capture_permission_status()' in SERVER
assert 'permission = None if prelogin else' not in SERVER
assert 'Screen Recording is not granted to ZorinMacBridge Server in the LoginWindow session' in SERVER

# Normal Aqua capture still keeps continuous SCStream.
assert 'let newStream = SCStream(filter: filter, configuration: config, delegate: self)' in SWIFT

print('pre-login screenshot capture regression tests: OK')
