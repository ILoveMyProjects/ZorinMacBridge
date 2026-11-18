from pathlib import Path
import socket
import threading
import time
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import mac_server


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

server = (ROOT / 'mac_server.py').read_text(encoding='utf-8')
# Ownership must be released before any native teardown call. A stuck Swift
# stop/cleanup path must not prevent the replacement TLS video session.
finalizer = server.index('# Release the Python video-owner lock before asking Swift to tear down.')
release_idx = server.index('release_ownership()', finalizer)
stop_idx = server.index('native.lib.zmb_streamer_stop(handle)', finalizer)
join_idx = server.index('runner.join(timeout=6.0)', stop_idx)
assert release_idx < stop_idx < join_idx
assert 'prelogin=prelogin, log=log, release_ownership=release_video_ownership, cancel_event=video_cancel_event' in server
assert '_ACTIVE_VIDEO_CANCEL' in server

swift = (ROOT / 'native/macos/ZMBStreamerLib.swift').read_text(encoding='utf-8')
assert 'private var runTask: Task<Void, Never>?' in swift
assert 'activeTask?.cancel()' in swift
assert 'task.cancel()' in swift
# Teardown must not drain frames or synchronously wait for ScreenCaptureKit /
# VideoToolbox cleanup. requestStop signals runBlocking first and cleans up async.
assert 'VTCompressionSessionCompleteFrames(activeCompression' not in swift
assert 'writeQueue.sync {}' not in swift
request_stop = swift.index('func requestStop(')
assert swift.index('done.signal()', request_stop) < swift.index('DispatchQueue.global(qos: .utility).async', request_stop)
assert 'try? output.close()' in swift

print('video session recovery regression tests: OK')
