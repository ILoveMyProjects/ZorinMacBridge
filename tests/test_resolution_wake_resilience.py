from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
assert (ROOT / "VERSION").read_text(encoding="utf-8").strip() == "0.15.2"

client = (ROOT / "linux_client.py").read_text(encoding="utf-8")
gtk = (ROOT / "linux_client_gtk.py").read_text(encoding="utf-8")
server = (ROOT / "mac_server.py").read_text(encoding="utf-8")
swift = (ROOT / "native/macos/ZMBStreamerLib.swift").read_text(encoding="utf-8")
admin = (ROOT / "native/macos/admin/zmb-admin").read_text(encoding="utf-8")

# Explicit resolution presets are actual 16:9 output rasters.
assert "'2K': {'max_width': 2560, 'target_width': 2560, 'target_height': 1440, 'virtual': True}" in client
assert "'4K': {'max_width': 3840, 'target_width': 3840, 'target_height': 2160, 'virtual': True}" in client
assert "target_width" in server and "target_height" in server
assert "zmb_virtual_display_ensure" in swift
assert "CGVirtualDisplayDescriptor" in swift
assert "CGVirtualDisplayMode" in swift
assert "Requested virtual desktop" in swift
assert "sourceRect = nil" in swift

# Virtual-display coordinates are already the real remote desktop coordinates.
assert "def _transform_stream_point" in client
transform = client[client.index("def _transform_stream_point"):client.index("def _selected_display_id")]
assert "crop_w" not in transform and "crop_h" not in transform and "src_aspect" not in transform
assert "return min(1.0" in transform

# A video-only failure must not terminate the desktop control session.
video_start = client.index("def _video_loop")
video_end = client.index("def _process_ui_queue", video_start)
video_loop = client[video_start:video_end]
assert "self.stop_event.set()" not in video_loop
assert "display_sleeping" in video_loop
assert "control session remains connected" in video_loop

# Wake is short-lived, user-session aware, and does not alter login policy.
assert "/bin/launchctl asuser" in admin
assert "/usr/bin/caffeinate -u -t 8" in admin
assert "caffeinate', '-i', '-w'" in server
assert "sysadminctl -autologin" not in admin

print("resolution/wake resilience tests: OK")
