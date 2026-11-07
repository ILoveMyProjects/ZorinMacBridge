from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
assert (ROOT / 'VERSION').read_text(encoding='utf-8').strip() == '0.14.19'

client = (ROOT / 'linux_client.py').read_text(encoding='utf-8')
gtk = (ROOT / 'linux_client_gtk.py').read_text(encoding='utf-8')
server = (ROOT / 'mac_server.py').read_text(encoding='utf-8')
swift = (ROOT / 'native/macos/ZMBStreamerLib.swift').read_text(encoding='utf-8')
backend = (ROOT / 'zmb_admin_backend.py').read_text(encoding='utf-8')
admin = (ROOT / 'native/macos/admin/zmb-admin').read_text(encoding='utf-8')
forced = (ROOT / 'native/macos/admin/zmb-admin-ssh').read_text(encoding='utf-8')
sudoers = (ROOT / 'native/macos/admin/install-admin.sh').read_text(encoding='utf-8')

# Sleeping/off displays are now a video-only retry condition. The control
# channel must not be torn down merely because ScreenCaptureKit has no display.
assert "'no display available for screencapturekit' in lower_reason" in client
assert "self.ui_queue.put(('display_sleeping', str(exc)))" in client
assert "self.stop_event.set()" in client  # still present for genuinely fatal video errors
assert "elif kind == 'display_sleeping':" in gtk
assert "def _wake_display_for_video_async" in gtk

# One-shot display wake is a restricted P0 command, not arbitrary shell access.
assert '"wake-display"' in backend
assert 'wake_display_cmd()' in admin
assert '/usr/bin/caffeinate -u -t 8' in admin
assert '"zmb-admin wake-display")' in forced
assert '/usr/local/sbin/zmb-admin wake-display' in sudoers

# Capture source is selectable in Auto/native mode; explicit resolutions use a true virtual desktop.
assert 'zmb_display_list_json' in swift
assert 'CGDisplayIsBuiltin' in swift
assert 'let mainDisplayID = CGMainDisplayID()' in swift
assert 'content.displays.first(where: { $0.displayID == mainDisplayID })' in swift
assert 'displayID: CGDirectDisplayID' in swift
assert "auth_ok['displays']" in server
assert "'display_id': display_id" in client
assert 'Auto — current Mac display' in gtk
assert "'1080p': {'max_width': 1920, 'target_width': 0, 'target_height': 0, 'virtual': False}" in client
assert "'2K': {'max_width': 2560, 'target_width': 2560, 'target_height': 1440, 'virtual': True}" in client
assert "'4K': {'max_width': 3840, 'target_width': 3840, 'target_height': 2160, 'virtual': True}" in client
assert "profile['target_width'] = target_width" in client or "profile['target_width']" in client
assert "profile['target_height'] = target_height" in client or "profile['target_height']" in client
assert 'zmb_virtual_display_ensure' in swift
assert 'CGVirtualDisplayDescriptor' in swift
assert 'sourceRect = nil' in swift
assert 'control session remains connected' in client

# No login/autologin policy changes are part of this repair.
combined = '\n'.join([gtk, backend, admin])
assert 'sysadminctl -autologin' not in combined

print('display/wake regression tests: OK')
