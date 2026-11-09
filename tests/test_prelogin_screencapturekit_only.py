from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
swift = (ROOT / 'native/macos/ZMBStreamerLib.swift').read_text(encoding='utf-8')
server = (ROOT / 'mac_server.py').read_text(encoding='utf-8')

# CGDisplayStream remains prohibited. Aqua continues to use supported
# ScreenCaptureKit SCStream, while LoginWindow bypasses ScreenCaptureKit startup
# and enters the bounded system screencapture path directly.
assert 'CGDisplayStream' not in swift
assert 'SCStream(filter: filter, configuration: config, delegate: self)' in swift
assert '/usr/sbin/screencapture' in swift
assert 'private func startPreloginSystemCapture() async throws' in swift
assert 'runPreloginSCStreamWithScreenshotFallback' not in swift
assert 'prelogin-backend=scstream' not in swift
assert 'prelogin-scstream-no-frame-within=2s' not in swift
assert 'prelogin-backend=screencapture-cli' in swift
assert 'prelogin-screencapture-cli-started' in swift
assert 'prelogin-failure=system-screencapture-failed:' in swift

# LoginWindow system frames feed the existing VideoToolbox pipeline and Stage D
# progress states; Aqua samples still feed the same encoder helper.
for stage in ('capture-frame', 'pixel-buffer', 'encode-submit', 'h264-written'):
    assert f'markPreloginStage("{stage}")' in swift
assert 'encodeImageBuffer(activeCompression, imageBuffer: imageBuffer' in swift

# Python keeps a bounded first-access-unit timeout and native diagnostics.
assert 'VIDEO_FIRST_ACCESS_UNIT_TIMEOUT_SECONDS = 6.0' in server
assert 'no first H.264 access unit within {startup_timeout:g}s; native={detail}' in server

print('Aqua ScreenCaptureKit + LoginWindow direct system capture regression tests: OK')
