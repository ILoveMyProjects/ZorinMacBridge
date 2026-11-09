from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SWIFT = (ROOT / 'native/macos/ZMBStreamerLib.swift').read_text(encoding='utf-8')
SERVER = (ROOT / 'mac_server.py').read_text(encoding='utf-8')
CLIENT = (ROOT / 'linux_client.py').read_text(encoding='utf-8')

# Supported macOS 26+ LoginWindow capture is ScreenCaptureKit only.
assert 'CGDisplayStream' not in SWIFT
assert 'runPreloginSCStreamWithScreenshotFallback' in SWIFT
assert 'SCStream(filter: filter, configuration: configuration, delegate: self)' in SWIFT
assert 'SCScreenshotManager.captureSampleBuffer' in SWIFT

# The Python/native ABI tells Swift which capture mode it is in.
assert '1 if prelogin else 0' in SERVER
assert '_ prelogin: Int32' in SWIFT

# Pre-login must not bypass Screen Recording preflight silently.
assert 'permission = screen_capture_permission_status()' in SERVER
assert 'permission = None if prelogin else' not in SERVER

# Backend selection is forwarded to the client for real-device diagnostics.
assert "line.startswith('prelogin-backend=')" in SERVER
assert "Pre-login capture backend: {event.backend}." in CLIENT

print('pre-login ScreenCaptureKit recovery regression tests: OK')
