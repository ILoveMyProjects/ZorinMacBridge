from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
gtk = (ROOT / 'linux_client_gtk.py').read_text()
core = (ROOT / 'linux_client.py').read_text()

# Video-only failures must not masquerade as whole-session failures.
assert "self.ui_queue.put(('video_error', f'Video stream failed: {exc}; control session remains connected'))" in core
assert "elif kind == 'video_error':" in gtk
assert "Connected — video retrying:" in gtk

# Full Screen sensitivity follows the actual control-session boolean, not the
# human-readable status string (which may describe a transient video retry).
status = gtk.split('def _status_changed', 1)[1].split('def _quality_description_text', 1)[0]
assert "connected = bool(getattr(self, 'connected', False))" in status
assert "str(value).startswith('Connected')" not in status
assert 'self.enter_fullscreen_btn.set_sensitive(connected)' in status

# The video retry path must remain independent from the global stop event.
video = core.split('def _video_loop', 1)[1].split('def _process_ui_queue', 1)[0]
assert "control session remains connected" in video
assert "self.stop_event.set()" not in video

print('fullscreen during video retry regression tests: OK')
