from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
swift = (ROOT / 'native/macos/ZMBStreamerLib.swift').read_text(encoding='utf-8')
server = (ROOT / 'mac_server.py').read_text(encoding='utf-8')

# macOS 26+ SDKs make CGDisplayStream start/stop unavailable. Production
# pre-login capture must stay entirely on ScreenCaptureKit.
assert 'CGDisplayStream' not in swift
assert 'SCScreenshotManager.captureSampleBuffer' in swift
assert 'prelogin-capture-mode=screenshot-manager' in swift
assert 'runPreloginScreenshotLoop' in swift

# LoginWindow samples must still feed the existing VideoToolbox pipeline and
# preserve the Stage D progress states.
assert 'CMSampleBufferGetImageBuffer(sampleBuffer)' in swift
assert 'markPreloginStage("capture-frame")' in swift
assert 'markPreloginStage("pixel-buffer")' in swift
assert 'encodeImageBuffer(activeCompression, imageBuffer: imageBuffer' in swift
assert 'markPreloginStage("encode-submit")' in swift

# Aqua remains on continuous SCStream; only LoginWindow uses screenshot polling.
assert 'let newStream = SCStream(filter: filter, configuration: config, delegate: self)' in swift

# Python still has a bounded first-access-unit timeout and native diagnostics.
assert 'VIDEO_FIRST_ACCESS_UNIT_TIMEOUT_SECONDS = 6.0' in server
assert 'no first H.264 access unit within {startup_timeout:g}s; native={detail}' in server

print('pre-login ScreenCaptureKit-only capture regression tests: OK')
