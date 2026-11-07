#!/usr/bin/env python3
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
assert (ROOT / "VERSION").read_text(encoding="utf-8").strip() == "0.14.18"

client = (ROOT / "linux_client.py").read_text(encoding="utf-8")
gtk = (ROOT / "linux_client_gtk.py").read_text(encoding="utf-8")
server = (ROOT / "mac_server.py").read_text(encoding="utf-8")
swift = (ROOT / "native/macos/ZMBStreamerLib.swift").read_text(encoding="utf-8")

# 2K/4K must be real macOS desktops, not panel crop/upscale.
assert "'2K': {'max_width': 2560, 'target_width': 2560, 'target_height': 1440, 'virtual': True}" in client
assert "'4K': {'max_width': 3840, 'target_width': 3840, 'target_height': 2160, 'virtual': True}" in client
assert 'NSClassFromString("CGVirtualDisplayDescriptor")' in swift
assert 'NSClassFromString("CGVirtualDisplay")' in swift
assert 'NSClassFromString("CGVirtualDisplayMode")' in swift
assert 'NSClassFromString("CGVirtualDisplaySettings")' in swift
assert 'zmb_virtual_display_ensure' in swift
assert 'virtual remote desktop ready' in server
assert 'ensure_virtual_display' in server
assert 'Requested virtual desktop' in swift
assert 'centred 16:9 crop' not in swift

# 2K/4K are headless remote workspaces: the built-in panel is detached only
# for the current login session and restored before virtual mode is released.
assert 'CGConfigureDisplayOrigin' in swift
assert 'CGCompleteDisplayConfiguration(config, .forSession)' in swift
assert 'SLSConfigureDisplayEnabled' in swift
assert 'CGSConfigureDisplayEnabled' in swift
assert 'disableBuiltInDisplays' in swift
assert 'restoreBuiltInDisplays' in swift
assert 'originalOrigins' in swift
assert 'restoreOrigins' in swift

# Mouse/input follows the captured virtual display instead of cached main-panel bounds.
assert 'def set_active_input_display' in server
assert 'def active_input_display' in server
assert 'def _bounds(self)' in server
assert 'active_input_display() or int(self.cg.CGMainDisplayID())' in server
transform = client[client.index('def _transform_stream_point'):client.index('def _selected_display_id')]
assert 'crop_w' not in transform and 'crop_h' not in transform and 'src_aspect' not in transform

# Automatic screen-off must not automatically put the entire Mac to sleep.
assert "['/usr/bin/caffeinate', '-i', '-w', str(os.getpid())]" in server
assert 'automatic idle system sleep inhibited' in server
assert 'display sleep/login policy unchanged' in server

# Video failure remains video-only; no global stop from the video loop.
video = client[client.index('def _video_loop'):client.index('def _process_ui_queue')]
assert 'self.stop_event.set()' not in video
assert "('display_sleeping', str(exc))" in video

# UI semantics: 1080p is physical; 2K/4K are headless virtual desktops.
assert '1080p uses the physical Mac display' in gtk
assert '2K / 4K use the remote virtual desktop' in gtk

print('virtual-display/wake regression tests: OK')
