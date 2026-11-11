from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
probe = (ROOT / "native/macos/capability-probe/ZMBCapabilityProbe.swift").read_text(encoding="utf-8")
runner = (ROOT / "scripts/run-tests.sh").read_text(encoding="utf-8")
source_gate = (ROOT / "scripts/verify-release-source.sh").read_text(encoding="utf-8")

# 0.15.15+ uses ScreenCaptureKit only. A legacy test left in a working tree
# must never silently become part of a later release again.
obsolete = ROOT / "tests/test_prelogin_displaystream_capture.py"
assert not obsolete.exists(), f"obsolete test must be deleted: {obsolete}"
assert "test_prelogin_displaystream_capture.py" in runner
assert "git rm -f" in runner
assert "test_prelogin_displaystream_capture.py" in source_gate

# Swift 6.3 enforces exclusive access to Array while withUnsafeMutableBytes is
# active. The luma loop must read through the borrowed raw buffer, not `bytes`.
start = probe.index("private func imageLumaRange")
end = probe.index("private func writePNG", start)
body = probe[start:end]
assert "bytes.withUnsafeMutableBytes" in body
assert "to: raw.count" in body
assert "Int(raw[offset])" in body
assert "Int(raw[offset + 1])" in body
assert "Int(raw[offset + 2])" in body
assert "to: bytes.count" not in body
assert "Int(bytes[offset])" not in body

print("macOS 26 native-build hotfix regression tests: OK")
