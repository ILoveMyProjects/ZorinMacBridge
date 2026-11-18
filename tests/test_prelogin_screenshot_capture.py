from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SWIFT = (ROOT / 'native/macos/ZMBStreamerLib.swift').read_text(encoding='utf-8')
SERVER = (ROOT / 'mac_server.py').read_text(encoding='utf-8')
CLIENT = (ROOT / 'linux_client.py').read_text(encoding='utf-8')
PROBE = (ROOT / 'native/macos/capability-probe/ZMBCapabilityProbe.swift').read_text(encoding='utf-8')

assert 'CGDisplayStream' not in SWIFT
assert 'private func startPreloginCaptureMatrix() throws' in SWIFT
assert 'SCShareableContent.getExcludingDesktopWindows(false, onScreenWindowsOnly: false)' in SWIFT
assert 'SCStream(filter: filter, configuration: config, delegate: self)' in SWIFT
assert 'SCScreenshotManager.captureSampleBuffer(contentFilter: filter, configuration: config)' in SWIFT
assert 'SCScreenshotManager.captureImage(in: rect)' in SWIFT
assert '/usr/sbin/screencapture' not in SWIFT
assert 'all supported LoginWindow capture backends failed to produce H.264' in SWIFT

# Physical probe still proves an actual SCStream first frame independently.
assert 'private final class ProbeStreamReceiver' in PROBE
assert 'waitForStreamFrame(filter: filter, config: config, timeout: 5.0)' in PROBE
assert 'capture_scstream_first_frame' in PROBE

assert '1 if prelogin else 0' in SERVER
assert '_ prelogin: Int32' in SWIFT
assert 'permission = screen_capture_permission_status()' in SERVER
assert "line.startswith('prelogin-backend=')" in SERVER
assert "line.startswith('prelogin-failure=')" in SERVER
assert "Pre-login capture backend: {event.backend}." in CLIENT
assert "Pre-login capture failed: {event.reason}." in CLIENT

print('pre-login ScreenCaptureKit screenshot fallback regression tests: OK')
