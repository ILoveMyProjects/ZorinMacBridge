from pathlib import Path
import os
import socket
import sys
import threading
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import mac_server
from zmb_session_lifecycle import make_capture_progress_state, parse_session_state


def test_pipe_cancel_event_wakes_reader() -> None:
    rfd, wfd = os.pipe()
    cancel = threading.Event()
    stream = os.fdopen(rfd, 'rb', buffering=0)
    try:
        def cancel_soon() -> None:
            time.sleep(0.12)
            cancel.set()

        threading.Thread(target=cancel_soon, daemon=True).start()
        started = time.monotonic()
        try:
            mac_server._read_exact_file(stream, 4, timeout=3.0, cancel_event=cancel)
        except ConnectionError as exc:
            assert 'cancelled for replacement' in str(exc)
        else:
            raise AssertionError('cancel_event did not interrupt native pipe read')
        assert time.monotonic() - started < 1.0
    finally:
        stream.close()
        os.close(wfd)


def test_replacement_sets_cancel_before_waiting_for_lock() -> None:
    left1, right1 = socket.socketpair()
    left2, right2 = socket.socketpair()
    acquired_second = threading.Event()
    error = []
    try:
        first_cancel = mac_server._acquire_video_session(left1, log=lambda *_: None)

        def acquire_second() -> None:
            try:
                second_cancel = mac_server._acquire_video_session(left2, log=lambda *_: None)
                acquired_second.set()
                mac_server._release_video_session(left2)
                assert not second_cancel.is_set()
            except Exception as exc:  # pragma: no cover - surfaced below
                error.append(exc)

        thread = threading.Thread(target=acquire_second, daemon=True)
        thread.start()
        deadline = time.monotonic() + 1.0
        while not first_cancel.is_set() and time.monotonic() < deadline:
            time.sleep(0.01)
        assert first_cancel.is_set(), 'replacement did not signal cancellation to stale video owner'
        mac_server._release_video_session(left1)
        thread.join(timeout=1.0)
        assert not thread.is_alive()
        assert not error, error
        assert acquired_second.is_set()
    finally:
        for sock in (left1, right1, left2, right2):
            try:
                sock.close()
            except OSError:
                pass
        # A failed assertion above must not poison the rest of the test suite.
        if mac_server._VIDEO_SESSION_LOCK.locked():
            try:
                mac_server._VIDEO_SESSION_LOCK.release()
            except RuntimeError:
                pass
        mac_server._set_active_video_socket(None, None)




def test_prelogin_never_touches_aqua_virtual_display_release() -> None:
    class FakeNative:
        def __init__(self) -> None:
            self.release_calls = 0
            self.ensure_calls = 0

        def release_virtual_display(self) -> None:
            self.release_calls += 1

        def ensure_virtual_display(self, width: int, height: int) -> int:
            self.ensure_calls += 1
            return 999

    native = FakeNative()
    selected = mac_server._prepare_video_capture_display(
        native, 0, 0, 0, prelogin=True, log=lambda *_: None,
    )
    assert selected == 0
    assert native.release_calls == 0
    assert native.ensure_calls == 0

    selected = mac_server._prepare_video_capture_display(
        native, 0, 0, 0, prelogin=False, log=lambda *_: None,
    )
    assert selected == 0
    assert native.release_calls == 1

    try:
        mac_server._prepare_video_capture_display(
            native, 0, 1920, 1080, prelogin=True, log=lambda *_: None,
        )
    except RuntimeError as exc:
        assert 'virtual display mode is unavailable' in str(exc)
    else:
        raise AssertionError('pre-login unexpectedly attempted virtual display mode')


def test_capture_progress_wire_state() -> None:
    event = parse_session_state(make_capture_progress_state('capture-matrix-start'))
    assert event.kind == 'capture-progress'
    assert event.stage == 'capture-matrix-start'


def test_source_contracts() -> None:
    server = (ROOT / 'mac_server.py').read_text(encoding='utf-8')
    swift = (ROOT / 'native/macos/ZMBStreamerLib.swift').read_text(encoding='utf-8')
    build = (ROOT / 'scripts/build-macos-streamer.sh').read_text(encoding='utf-8')
    gate = (ROOT / 'scripts/verify-macos-native-gate.sh').read_text(encoding='utf-8')

    # Pre-login starts directly on the native runner thread, not through Task.
    prelogin_branch = swift.index('if prelogin {', swift.index('func runBlocking()'))
    native_enter = swift.index('prelogin-stage=native-run-enter', prelogin_branch)
    task_start = swift.index('let task = Task', prelogin_branch)
    assert prelogin_branch < native_enter < task_start
    assert 'try startPreloginCaptureMatrix()' in swift[prelogin_branch:task_start]

    # stop() signals completion before asynchronous framework teardown.
    request_stop = swift.index('func requestStop(')
    done_signal = swift.index('done.signal()', request_stop)
    async_cleanup = swift.index('DispatchQueue.global(qos: .utility).async', request_stop)
    assert done_signal < async_cleanup

    # Python replacement cancellation does not depend solely on TCP/TLS FIN.
    assert '_ACTIVE_VIDEO_CANCEL' in server
    assert "raise ConnectionError('video session cancelled for replacement')" in server
    assert 'cancel_event=cancel_event' in server

    # Global video ownership is released before native teardown can block.
    finalizer = server.index('# Release the Python video-owner lock before asking Swift to tear down.')
    release = server.index('release_ownership()', finalizer)
    native_stop = server.index('native.lib.zmb_streamer_stop(handle)', finalizer)
    assert release < native_stop

    # The dylib carries its own compile-time version stamp and runtime verifies it.
    assert 'ZMBNativeBuildVersion.swift' in build
    assert 'ZMB_NATIVE_BUILD_VERSION' in swift
    assert '@_cdecl("zmb_streamer_build_version")' in swift
    assert '_zmb_streamer_build_version' in gate
    assert 'native streamer build version mismatch' in server


if __name__ == '__main__':
    test_pipe_cancel_event_wakes_reader()
    test_replacement_sets_cancel_before_waiting_for_lock()
    test_prelogin_never_touches_aqua_virtual_display_release()
    test_capture_progress_wire_state()
    test_source_contracts()
    print('pre-login native lifecycle tests: OK')
