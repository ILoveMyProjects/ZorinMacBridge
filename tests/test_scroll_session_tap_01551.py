"""0.15.51: Aqua scroll must use kCGSessionEventTap without changing other input taps."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
source = (ROOT / "mac_server.py").read_text(encoding="utf-8")

assert "SESSION_TAP = 1" in source
assert "self._post(event, tap=self.SESSION_TAP)" in source
assert "self.cg.CGEventPost(self.HID_TAP if tap is None else int(tap), event)" in source
# Mouse move/button code must keep the default HID tap path.
assert "self._post(self.cg.CGEventCreateMouseEvent(None, etype, CGPoint(x, y), button))" in source
print("0.15.51 Aqua scroll session-tap regression: OK")
