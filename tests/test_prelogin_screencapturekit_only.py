from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
swift = (ROOT / 'native/macos/ZMBStreamerLib.swift').read_text(encoding='utf-8')
server = (ROOT / 'mac_server.py').read_text(encoding='utf-8')
client = (ROOT / 'linux_client.py').read_text(encoding='utf-8')

# Aqua stays on the existing persistent SCStream. LoginWindow may fall through
# supported Apple screenshot APIs only after SCStream fails to produce H.264.
assert 'CGDisplayStream' not in swift
assert 'SCStream(filter: filter, configuration: config, delegate: self)' in swift
assert 'private func startPreloginCaptureMatrix() throws' in swift
assert 'tryPreloginSCStream()' in swift
assert 'SCScreenshotManager.captureSampleBuffer' in swift
assert 'SCScreenshotManager.captureImage(in: rect)' in swift
assert 'screencapturekit-persistent' in swift
assert 'screencapture-cli' not in swift

# LoginWindow success is measured at the real H.264 pipe write.
for stage in ('capture-frame', 'pixel-buffer', 'encode-submit', 'h264-written'):
    assert f'markPreloginStage("{stage}")' in swift
assert 'preloginAccessUnitWritten = true' in swift
assert 'waitForPreloginAccessUnit(timeout:' in swift

# Server gives the bounded matrix enough time, while client uses progress-aware
# soft timeout plus an absolute hard timeout.
assert 'VIDEO_FIRST_ACCESS_UNIT_TIMEOUT_SECONDS = 35.0' in server
assert 'first_frame_hard_deadline = first_frame_started + 40.0' in client
assert "event.kind in ('pipeline', 'capture-progress', 'capture-backend', 'capture-failure')" in client
assert "Pre-login video made no capture progress for 10 seconds" in client

print('Aqua + LoginWindow capture-matrix regression tests: OK')
