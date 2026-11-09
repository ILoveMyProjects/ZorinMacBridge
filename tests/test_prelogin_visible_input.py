#!/usr/bin/env python3
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
swift = (ROOT / 'native/macos/ZMBStreamerLib.swift').read_text(encoding='utf-8')
native = (ROOT / 'native/macos/ZMBLoginInput.c').read_text(encoding='utf-8')
server = (ROOT / 'mac_server.py').read_text(encoding='utf-8')
client = (ROOT / 'linux_client.py').read_text(encoding='utf-8')
gtk = (ROOT / 'linux_client_gtk.py').read_text(encoding='utf-8')

# The first ScreenCaptureKit frame is .started, not .complete.  It must not be discarded.
assert 'status != .complete && status != .started' in swift
assert 'first sample after startCapture()' in swift

# CoreHID failure must prefer a real virtual HID device before legacy event injection.
assert 'IOHIDUserDeviceCreate' in native
assert 'IOHIDUserDeviceHandleReport' in native
assert 'ZMB_LOGIN_INPUT_USER_DEVICE' in native
assert 'zmb_login_input_hid_key' in native
assert 'zmb_login_input_hid_pointer' in native
# Older IOHIDSystem and Quartz paths remain only after the virtual-device path.
assert 'kIOReturnNotPrivileged' in native
assert 'kCGSessionEventTap' in native
assert 'zmb_quartz_pointer' in native
assert 'zmb_quartz_key' in native
assert 'zmb_quartz_scroll' in native

# Mouse failures are per-event; one rejected pointer packet cannot close Desktop.
assert "[control] pointer move rejected" in server
assert "[control] mouse button rejected" in server
assert "[control] scroll event rejected" in server

# Do not inject pre-login input until the first decoded frame is actually visible.
assert 'self.prelogin_video_ready = True' in client
assert "('prelogin_video_ready', img.width, img.height)" in client
assert 'def _remote_input_ready(self)' in gtk
assert 'self.prelogin_session and not self.prelogin_video_ready' in gtk
assert 'waiting for first frame' in gtk

print('pre-login visible/input regression tests: OK')
