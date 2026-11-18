from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
swift = (ROOT / 'native/macos/ZMBStreamerLib.swift').read_text(encoding='utf-8')
server = (ROOT / 'mac_server.py').read_text(encoding='utf-8')
client = (ROOT / 'linux_client.py').read_text(encoding='utf-8')

order = [
    swift.index('if tryPreloginSCStream() { return }'),
    swift.index('if runPreloginSampleBufferBackend() { return }'),
    swift.index('if runPreloginImageBackend() { return }'),
]
assert order == sorted(order)
for backend in ('screencapturekit-persistent', 'screenshot-sample-buffer', 'screenshot-image-rect'):
    assert backend in swift
assert r'log("prelogin-backend=\(backend)")' in swift
assert 'preloginAccessUnitWritten = true' in swift
assert 'waitForPreloginAccessUnit(timeout:' in swift
assert 'writeQueue.async { drain.signal() }' in swift
assert 'all supported LoginWindow capture backends failed to produce H.264' in swift
assert 'VIDEO_FIRST_ACCESS_UNIT_TIMEOUT_SECONDS = 35.0' in server
assert 'first_frame_hard_deadline = first_frame_started + 40.0' in client
assert "first_frame_deadline = min(first_frame_hard_deadline, now_progress + 10.0)" in client
print('pre-login capture matrix contract tests: OK')
