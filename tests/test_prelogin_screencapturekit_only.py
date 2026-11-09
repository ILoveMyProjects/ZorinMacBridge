from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
swift = (ROOT / 'native/macos/ZMBStreamerLib.swift').read_text(encoding='utf-8')
server = (ROOT / 'mac_server.py').read_text(encoding='utf-8')

# Both Aqua and LoginWindow use supported ScreenCaptureKit. LoginWindow has a
# bounded startup path so an unavailable managed capture cannot hang forever.
assert 'CGDisplayStream' not in swift
assert 'SCStream(filter: filter, configuration: config, delegate: self)' in swift
assert 'private func startPreloginScreenCaptureKit() throws' in swift
assert 'waitForPreloginShareableContent(timeout: 6.0)' in swift
assert 'startPreloginStreamBounded(newStream, timeout: 4.0)' in swift
assert 'prelogin-backend=screencapturekit-persistent' in swift
assert 'screencapture-cli' not in swift
assert 'runPreloginSCStreamWithScreenshotFallback' not in swift

# LoginWindow SCStream samples feed the existing VideoToolbox pipeline and Stage
# D progress states, including the first .started sample for a static login page.
for stage in ('capture-frame', 'pixel-buffer', 'encode-submit', 'h264-written'):
    assert f'markPreloginStage("{stage}")' in swift
assert 'status != .complete && status != .started' in swift
assert 'encodeImageBuffer(activeCompression, imageBuffer: imageBuffer' in swift

# Python keeps a bounded server-side first-access-unit timeout and diagnostics.
assert 'VIDEO_FIRST_ACCESS_UNIT_TIMEOUT_SECONDS = 6.0' in server
assert 'no first H.264 access unit within {startup_timeout:g}s; native={detail}' in server
assert 'startup_timeout = VIDEO_FIRST_ACCESS_UNIT_TIMEOUT_SECONDS if frames == 0 else None' in server

print('Aqua + managed LoginWindow ScreenCaptureKit regression tests: OK')
