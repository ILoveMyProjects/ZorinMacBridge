from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

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

# Mac accepts only one native video owner at a time, but replacement actively
# interrupts a stale owner instead of passively waiting 20 seconds.
assert '_VIDEO_SESSION_LOCK = threading.Lock()' in server
assert '_ACTIVE_VIDEO_SOCKET' in server
assert 'interrupting stale owner before replacement' in server
assert 'forced stale video socket shutdown before replacement' in server
assert 'VIDEO_REPLACEMENT_GRACE_SECONDS = 4.0' in server
assert 'previous video session did not stop within {VIDEO_REPLACEMENT_GRACE_SECONDS:g}s' in server

# A video generation that authenticates but never produces an access unit must
# fail quickly with native diagnostics instead of monopolising the global lock.
assert 'VIDEO_FIRST_ACCESS_UNIT_TIMEOUT_SECONDS = 6.0' in server
assert 'no first H.264 access unit within {startup_timeout:g}s; native={detail}' in server

# Native pipe reads also monitor the TLS socket, so a client-side reconfigure does not
# leave the old ScreenCaptureKit streamer alive for the full 15-second pipe timeout.
assert 'disconnect_sock: ssl.SSLSocket | None = None' in server
assert 'watchers.append(disconnect_sock)' in server
assert "raise ConnectionError('video client disconnected during native capture')" in server
assert '_read_exact_file(stream, 4, timeout=startup_timeout, disconnect_sock=sock, idle_callback=flush_pipeline_events)' in server
assert '_read_exact_file(stream, size, timeout=startup_timeout, disconnect_sock=sock, idle_callback=flush_pipeline_events)' in server
assert "select.select(watchers, [], [], min(0.1 if idle_callback is not None else 0.5, remaining))" in server

print('video restart stability regression tests: OK')
