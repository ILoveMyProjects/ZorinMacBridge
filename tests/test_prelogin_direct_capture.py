from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SWIFT = (ROOT / "native/macos/ZMBStreamerLib.swift").read_text(encoding="utf-8")

start = SWIFT.index("    private func start() async throws {")
prelogin_branch = SWIFT.index("if prelogin {", start)
matrix_call = SWIFT.index("try startPreloginCaptureMatrix()", prelogin_branch)
aqua_shareable = SWIFT.index("SCShareableContent.excludingDesktopWindows", matrix_call)

assert prelogin_branch < matrix_call < aqua_shareable
assert "private func startPreloginCaptureMatrix() throws" in SWIFT
assert "private func tryPreloginSCStream() -> Bool" in SWIFT
assert "SCScreenshotManager.captureSampleBuffer" in SWIFT
assert "SCScreenshotManager.captureImage(in: rect)" in SWIFT
assert "screencapturekit-persistent" in SWIFT
assert "screenshot-sample-buffer" in SWIFT
assert "screenshot-image-rect" in SWIFT
assert "preloginAccessUnitWritten = true" in SWIFT

# Failed CLI workaround must remain absent.
assert "startPreloginSystemCapture" not in SWIFT
assert "screencapture-cli" not in SWIFT
assert 'process.executableURL = URL(fileURLWithPath: "/usr/sbin/screencapture")' not in SWIFT

print("pre-login capture-matrix ordering regression tests: OK")
