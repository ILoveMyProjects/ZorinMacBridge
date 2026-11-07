from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

assert (ROOT / 'VERSION').read_text(encoding='utf-8').strip() == '0.15.2'
client = (ROOT / 'linux_client.py').read_text(encoding='utf-8')
server = (ROOT / 'mac_server.py').read_text(encoding='utf-8')

# Rapid FPS/Quality clicks are coalesced before the live H.264 socket is torn down.
assert 'VIDEO_RECONFIGURE_DEBOUNCE_SECONDS = 0.85' in client
assert 'self._video_reconfigure_not_before = time.monotonic() + VIDEO_RECONFIGURE_DEBOUNCE_SECONDS' in client
assert 'def _video_reconfigure_ready' in client
assert 'time.monotonic() >= float(getattr(self, \'_video_reconfigure_not_before\', 0.0))' in client

# Intentional restart has a cleanup grace instead of immediately racing the next TLS connection.
assert 'VIDEO_RESTART_GRACE_SECONDS = 0.35' in client
assert 'deadline = time.monotonic() + VIDEO_RESTART_GRACE_SECONDS' in client

# Mac accepts only one native video owner at a time; replacement waits for cleanup.
assert '_VIDEO_SESSION_LOCK = threading.Lock()' in server
assert "waiting for previous video session cleanup before starting replacement" in server
assert "previous video session did not stop within 20s" in server

# Native pipe reads also monitor the TLS socket, so a client-side reconfigure does not
# leave the old ScreenCaptureKit streamer alive for the full 15-second pipe timeout.
assert 'disconnect_sock: ssl.SSLSocket | None = None' in server
assert 'watchers.append(disconnect_sock)' in server
assert "raise ConnectionError('video client disconnected during native capture')" in server
assert '_read_exact_file(stream, 4, disconnect_sock=sock)' in server
assert '_read_exact_file(stream, size, disconnect_sock=sock)' in server

print('video restart stability regression tests: OK')
