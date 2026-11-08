from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SWIFT = (ROOT / 'native/macos/ZMBStreamerLib.swift').read_text(encoding='utf-8')
SERVER = (ROOT / 'mac_server.py').read_text(encoding='utf-8')


# SCScreenshotManager remains a modern LoginWindow fallback, while 0.15.10
# prefers CGDisplayStream for the physical login screen. Normal Aqua SCStream
# remains separate.
assert 'SCScreenshotManager.captureSampleBuffer' in SWIFT
assert 'prelogin-capture-mode=screenshot-manager-fallback' in SWIFT
assert 'if prelogin {' in SWIFT
assert 'runPreloginScreenshotLoop' in SWIFT

# The Python/native ABI must tell the Swift streamer which capture mode it is in.
assert '1 if prelogin else 0' in SERVER
assert '_ prelogin: Int32' in SWIFT

# Pre-login must no longer skip the Screen Recording preflight silently.
assert 'permission = screen_capture_permission_status()' in SERVER
assert 'permission = None if prelogin else' not in SERVER
assert 'Screen Recording is not granted to ZorinMacBridge Server in the LoginWindow session' in SERVER

# Normal Aqua capture still keeps SCStream.
assert 'let newStream = SCStream(filter: filter, configuration: config, delegate: self)' in SWIFT

print('pre-login screenshot capture regression tests: OK')
