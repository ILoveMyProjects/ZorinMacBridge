from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SWIFT = (ROOT / "native/macos/ZMBStreamerLib.swift").read_text(encoding="utf-8")

start = SWIFT.index("    private func start() async throws {")
shareable = SWIFT.index("SCShareableContent.excludingDesktopWindows", start)
prelogin_branch = SWIFT.index("if prelogin {", start)
direct_call = SWIFT.index("try await startPreloginSystemCapture()", prelogin_branch)

# The LoginWindow branch must happen before any ScreenCaptureKit content lookup.
assert prelogin_branch < direct_call < shareable
assert "private func startPreloginSystemCapture() async throws" in SWIFT
assert "CGDisplayPixelsWide(mainDisplayID)" in SWIFT
assert "CGDisplayPixelsHigh(mainDisplayID)" in SWIFT
assert "prelogin-backend=screencapture-cli" in SWIFT
assert "process.arguments = [\"-x\", \"-m\", \"-t\", \"png\", fileURL.path]" in SWIFT
assert "prelogin-failure=system-screencapture-failed:" in SWIFT
assert "runPreloginSCStreamWithScreenshotFallback" not in SWIFT

print("pre-login direct capture ordering regression tests: OK")
