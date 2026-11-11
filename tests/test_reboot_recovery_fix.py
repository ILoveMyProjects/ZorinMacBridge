from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from discovery import desktop_connect_strategy
from zmb_session_lifecycle import make_capture_failure_state, parse_session_state

# A real successful Desktop TCP probe wins over stale Bonjour saved/offline state.
assert desktop_connect_strategy(desktop_available=True, system_available=False, paired_only=True) == "connect"

gtk = (ROOT / "linux_client_gtk.py").read_text(encoding="utf-8")
assert "self.selected_desktop_available = True" in gtk
assert "self.selected_paired_only = False" in gtk
assert "clearing saved/offline state" in gtk

swift = (ROOT / "native/macos/ZMBStreamerLib.swift").read_text(encoding="utf-8")
assert "private func startPreloginScreenCaptureKit() throws" in swift
assert "prelogin-backend=screencapturekit-persistent" in swift
assert "startPreloginStreamBounded" in swift
assert "prelogin-failure=first-frame-timeout:" in swift
assert "prelogin-failure=managed-screencapturekit-start-failed:" in swift
assert "screencapture-cli" not in swift

server = (ROOT / "mac_server.py").read_text(encoding="utf-8")
client = (ROOT / "linux_client.py").read_text(encoding="utf-8")
assert "make_capture_failure_state" in server
assert "line.startswith('prelogin-failure=')" in server
assert "event.kind == 'capture-failure'" in client
assert "Pre-login video produced no first frame within 10 seconds" in client

event = parse_session_state(make_capture_failure_state("managed-screencapturekit-start-failed: missing entitlement"))
assert event.kind == "capture-failure"
assert event.reason == "managed-screencapturekit-start-failed: missing entitlement"

print("0.15.27 reboot recovery + managed capture regression tests: OK")
