from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
swift = (ROOT / 'native/macos/ZMBStreamerLib.swift').read_text(encoding='utf-8')
server = (ROOT / 'mac_server.py').read_text(encoding='utf-8')

# macOS 26+ SDKs make CGDisplayStream unavailable. LoginWindow capture must stay
# entirely on supported ScreenCaptureKit APIs.
assert 'CGDisplayStream' not in swift
assert 'SCStream(filter: filter, configuration: configuration, delegate: self)' in swift
assert '/usr/sbin/screencapture' in swift

# LoginWindow uses continuous SCStream first and falls back to the system
# screencapture utility in the same LoginWindow bootstrap session if the initial
# frame/encode proof does not arrive quickly.
assert 'runPreloginSCStreamWithScreenshotFallback' in swift
assert 'prelogin-backend=scstream' in swift
assert 'prelogin-scstream-no-frame-within=2s' in swift
assert 'prelogin-backend=screencapture-cli' in swift
assert 'prelogin-screencapture-cli-started' in swift
assert 'prelogin-failure=system-screencapture-failed:' in swift

# Both backends feed the existing VideoToolbox pipeline and Stage D progress.
for stage in ('capture-frame', 'pixel-buffer', 'encode-submit', 'h264-written'):
    assert f'markPreloginStage("{stage}")' in swift
assert 'encodeImageBuffer(activeCompression, imageBuffer: imageBuffer' in swift

# Python still has a bounded first-access-unit timeout and native diagnostics.
assert 'VIDEO_FIRST_ACCESS_UNIT_TIMEOUT_SECONDS = 6.0' in server
assert 'no first H.264 access unit within {startup_timeout:g}s; native={detail}' in server

print('pre-login ScreenCaptureKit primary/fallback regression tests: OK')
