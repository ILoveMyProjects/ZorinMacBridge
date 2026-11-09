from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SWIFT = (ROOT / "native/macos/ZMBStreamerLib.swift").read_text(encoding="utf-8")

start = SWIFT.index("    private func start() async throws {")
prelogin_branch = SWIFT.index("if prelogin {", start)
managed_call = SWIFT.index("try startPreloginScreenCaptureKit()", prelogin_branch)
aqua_shareable = SWIFT.index("SCShareableContent.excludingDesktopWindows", managed_call)

# LoginWindow selects its dedicated bounded ScreenCaptureKit startup before the
# normal Aqua async discovery path.
assert prelogin_branch < managed_call < aqua_shareable
assert "private func startPreloginScreenCaptureKit() throws" in SWIFT
assert "SCShareableContent.getExcludingDesktopWindows" in SWIFT
assert "startPreloginStreamBounded" in SWIFT
assert 'prelogin-backend=screencapturekit-persistent' in SWIFT
assert 'prelogin-failure=first-frame-timeout:' in SWIFT
assert 'prelogin-failure=managed-screencapturekit-start-failed:' in SWIFT

# Production LoginWindow capture no longer uses the failed system-screenshot
# workaround from 0.15.23.
assert 'startPreloginSystemCapture' not in SWIFT
assert 'screencapture-cli' not in SWIFT
assert 'process.executableURL = URL(fileURLWithPath: "/usr/sbin/screencapture")' not in SWIFT
assert 'runPreloginSCStreamWithScreenshotFallback' not in SWIFT

print("managed pre-login ScreenCaptureKit ordering regression tests: OK")
