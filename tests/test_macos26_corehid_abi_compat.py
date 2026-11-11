from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
COREHID = (ROOT / "native/macos/ZMBCoreHIDInput.swift").read_text(encoding="utf-8")
BUILD = (ROOT / "scripts/build-macos-streamer.sh").read_text(encoding="utf-8")

# Xcode 26 and newer SDK imports differ in HIDVirtualDeviceDelegate async-ness.
# Keep source-compatible overloads without changing the runtime backend.
assert COREHID.count("receivedSetReportRequestOfType type: HIDReportType") == 2
assert COREHID.count("receivedGetReportRequestOfType type: HIDReportType") == 2
assert ") throws {" in COREHID
assert ") async throws {" in COREHID
assert ") throws -> Data {" in COREHID
assert ") async throws -> Data {" in COREHID

# Async report dispatch must not directly lock/unlock NSLock.
start = COREHID.index("private func dispatch(_ data: Data")
end = COREHID.index("\n    func open()", start)
dispatch = COREHID[start:end]
assert "resultLock.lock()" not in dispatch
assert "resultLock.unlock()" not in dispatch
assert "ZMBInt32ResultBox" in dispatch
assert "SuspendingClock.now" in dispatch

assert "-framework CoreHID" in BUILD
assert "ZMBCoreHIDInput.swift" in BUILD

print("macOS 26/newer CoreHID ABI compatibility tests: OK")
