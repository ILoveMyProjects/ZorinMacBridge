from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
assert (ROOT / 'VERSION').read_text(encoding='utf-8').strip() == '0.15.7'

swift = (ROOT / 'native/macos/ZMBStreamerLib.swift').read_text(encoding='utf-8')
server = (ROOT / 'mac_server.py').read_text(encoding='utf-8')

# LoginWindow uses WindowServer's display stream first. This path is known to
# deliver the login screen in login-window helpers; SCScreenshotManager remains
# a synchronous-start fallback rather than the sole capture backend.
assert 'CGDisplayStreamCreateWithDispatchQueue' in swift
assert 'prelogin-capture-mode=cgdisplaystream' in swift
assert 'prelogin-cgdisplaystream-first-frame' in swift
assert 'prelogin-capture-mode=screenshot-manager-fallback' in swift
assert swift.index('startPreloginDisplayStream') < swift.index('runPreloginScreenshotLoop')
assert 'CGDisplayStreamStop(activeLoginDisplayStream)' in swift
assert 'CVPixelBufferCreateWithIOSurface' in swift
assert 'encodeImageBuffer(activeCompression, imageBuffer: pixelBuffer' in swift

# Python still has a bounded first-access-unit timeout and carries native logs
# back to the client if neither capture backend yields an encoded frame.
assert 'VIDEO_FIRST_ACCESS_UNIT_TIMEOUT_SECONDS = 6.0' in server
assert 'no first H.264 access unit within {startup_timeout:g}s; native={detail}' in server

print('pre-login CGDisplayStream capture regression tests: OK')
