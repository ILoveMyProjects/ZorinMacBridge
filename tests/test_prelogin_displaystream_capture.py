from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

swift = (ROOT / 'native/macos/ZMBStreamerLib.swift').read_text(encoding='utf-8')
server = (ROOT / 'mac_server.py').read_text(encoding='utf-8')

# LoginWindow uses WindowServer's display stream first. This path is known to
# deliver the login screen in login-window helpers; SCScreenshotManager remains
# a synchronous-start fallback rather than the sole capture backend.
assert 'CGDisplayStream(' in swift
assert 'dispatchQueueDisplay: displayID' in swift
assert 'prelogin-capture-mode=cgdisplaystream' in swift
assert 'prelogin-cgdisplaystream-first-frame' in swift
assert 'prelogin-capture-mode=screenshot-manager-fallback' in swift
assert swift.index('startPreloginDisplayStream') < swift.index('runPreloginScreenshotLoop')
assert 'activeLoginDisplayStream.stop()' in swift
assert 'CVPixelBufferCreateWithIOSurface' in swift
assert 'encodeImageBuffer(activeCompression, imageBuffer: pixelBuffer' in swift

# Xcode 16.4 imports the old C entry points/property constants as unavailable
# Swift names. Keep the modern imported API so the macOS release build compiles.
for obsolete in [
    'CGDisplayStreamCreateWithDispatchQueue',
    'CGDisplayStreamStart(',
    'CGDisplayStreamStop(',
    'kCGDisplayStreamShowCursor',
    'kCGDisplayStreamQueueDepth',
    'kCGDisplayStreamMinimumFrameTime',
]:
    assert obsolete not in swift, obsolete
assert 'CGDisplayStream.showCursor' in swift
assert 'CGDisplayStream.queueDepth' in swift
assert 'CGDisplayStream.minimumFrameTime' in swift
assert 'displayStream.start()' in swift
assert 'displayStream.stop()' in swift

# Python still has a bounded first-access-unit timeout and carries native logs
# back to the client if neither capture backend yields an encoded frame.
assert 'VIDEO_FIRST_ACCESS_UNIT_TIMEOUT_SECONDS = 6.0' in server
assert 'no first H.264 access unit within {startup_timeout:g}s; native={detail}' in server

print('pre-login CGDisplayStream capture regression tests: OK')
