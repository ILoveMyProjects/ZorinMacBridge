from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
assert (ROOT / 'VERSION').read_text(encoding='utf-8').strip() == '0.15.7'

client = (ROOT / 'linux_client.py').read_text(encoding='utf-8')
gtk = (ROOT / 'linux_client_gtk.py').read_text(encoding='utf-8')
server = (ROOT / 'mac_server.py').read_text(encoding='utf-8')
swift = (ROOT / 'native/macos/ZMBStreamerLib.swift').read_text(encoding='utf-8')
tray = (ROOT / 'tray_icon.py').read_text(encoding='utf-8')

# Quality is bitrate only; FPS and resolution are separate controls.
quality_block = client.split('VIDEO_QUALITY_PRESETS = {', 1)[1].split('VIDEO_FPS_PRESETS', 1)[0]
assert "'bitrate'" in quality_block
assert "'fps'" not in quality_block
assert "'max_width'" not in quality_block
assert "VIDEO_FPS_PRESETS" in client
assert "'24': 24" in client and "'30': 30" in client and "'60': 60" in client
assert "self.fps_var = ValueVar('30')" in gtk
assert "label='FPS'" in gtk
assert "label='Resolution'" in gtk
assert "label='Quality'" in gtk
assert "up to {profile['max_width']}" not in gtk
assert "profile['fps']" not in gtk.split('def _quality_description_text', 1)[1].split('def _quality_dropdown_changed', 1)[0]
assert 'adaptive bitrate' in gtk

# 1080p is the safe physical-display mode; only 2K/4K request virtual targets.
assert "'1080p': {'max_width': 1920, 'target_width': 0, 'target_height': 0, 'virtual': False}" in client
assert "'2K': {'max_width': 2560, 'target_width': 2560, 'target_height': 1440, 'virtual': True}" in client
assert "'4K': {'max_width': 3840, 'target_width': 3840, 'target_height': 2160, 'virtual': True}" in client
assert "allowed_targets = {(2560, 1440), (3840, 2160)}" in server

# Connect prepares the requested display before video, but preparation failure must
# not fail the authenticated control session.
assert "'remote_display': dict(self._desktop_display_snapshot or {})" in client
assert "auth_ok['remote_display'] = prepare_remote_display_from_auth(auth, log=log)" in server
assert 'Control/TLS authentication must still succeed' in server

# ScreenCaptureKit must wait for the exact requested virtual display instead of
# falling back to the physical MacBook panel.
assert 'for _ in 0..<50 where requested == nil' in swift
assert 'Requested display is not yet visible to ScreenCaptureKit' in swift
assert 'requested ?? automatic' not in swift

# 2K -> 4K replacement is transactional and preserves the previous display on failure.
assert 'var previousDisplay = display' in swift
assert 'previousDisplay = nil' in swift
assert 'Keep the working virtual display alive while constructing a replacement' in swift

# Zorin-side compatibility for old pystray + Pillow >= 10.
assert "if not hasattr(Image, 'ANTIALIAS')" in tray
assert 'Image.ANTIALIAS = resampling.LANCZOS' in tray

print('independent video controls / headless connect regression tests: OK')
