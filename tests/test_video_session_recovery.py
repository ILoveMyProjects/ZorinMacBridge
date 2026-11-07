from pathlib import Path
import socket
import threading
import time
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import mac_server

assert (ROOT / 'VERSION').read_text(encoding='utf-8').strip() == '0.15.6'

# Reset shared state in case this file is run in-process with another test runner.
while mac_server._VIDEO_SESSION_LOCK.locked():
    time.sleep(0.01)
mac_server._set_active_video_socket(None)

old_sock, old_peer = socket.socketpair()
new_sock, new_peer = socket.socketpair()
ready = threading.Event()
old_released = threading.Event()
errors = []


def old_owner():
    try:
        mac_server._VIDEO_SESSION_LOCK.acquire()
        mac_server._set_active_video_socket(old_sock)
        ready.set()
        # Replacement must wake this recv by shutting down old_sock.
        data = old_sock.recv(1)
        assert data == b''
    except Exception as exc:
        errors.append(exc)
    finally:
        mac_server._release_video_session(old_sock)
        old_released.set()


t = threading.Thread(target=old_owner, daemon=True)
t.start()
assert ready.wait(1.0)

started = time.monotonic()
mac_server._acquire_video_session(new_sock, log=lambda *_: None)
elapsed = time.monotonic() - started
try:
    assert elapsed < mac_server.VIDEO_REPLACEMENT_GRACE_SECONDS
    assert old_released.wait(1.0)
    assert not errors, errors
finally:
    mac_server._release_video_session(new_sock)
    for s in (old_sock, old_peer, new_sock, new_peer):
        try:
            s.close()
        except OSError:
            pass

swift = (ROOT / 'native/macos/ZMBStreamerLib.swift').read_text(encoding='utf-8')
assert 'private var runTask: Task<Void, Never>?' in swift
assert 'activeTask?.cancel()' in swift
assert 'task.cancel()' in swift

print('video session recovery regression tests: OK')
