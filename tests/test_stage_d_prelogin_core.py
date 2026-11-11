from __future__ import annotations

import errno
import socket
import threading
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def test_control_handoff_packet_is_explicit():
    import mac_server
    from protocol import PacketReader, SESSION_STATE, unpack_json
    from zmb_session_lifecycle import PreloginControlLease, parse_session_state

    class FakeInput:
        released = False
        def move(self, *_): pass
        def button(self, *_): pass
        def scroll(self, *_): pass
        def key(self, *_): pass
        def text(self, *_): pass
        def release_all(self):
            FakeInput.released = True

    left, right = socket.socketpair()
    original = mac_server.LoginWindowInput
    try:
        mac_server.LoginWindowInput = FakeInput
        lease = PreloginControlLease('token', 7, left, '127.0.0.1')
        lease.handoff_user = 'dev'
        lease.handoff_event.set()
        mac_server.control_session(left, prelogin=True, prelogin_lease=lease, log=lambda _m: None)
        data = right.recv(65536)
        packets = PacketReader().feed(data)
        assert len(packets) == 1
        kind, payload = packets[0]
        assert kind == SESSION_STATE
        state = unpack_json(payload)
        event = parse_session_state(state)
        assert event.kind == 'handoff'
        assert event.target == 'aqua'
        assert event.user == 'dev'
        assert event.generation == 7
        assert lease.handoff_sent_event.is_set()
        assert FakeInput.released
    finally:
        mac_server.LoginWindowInput = original
        left.close()
        right.close()


def test_aqua_bind_retry_is_bounded():
    import mac_server_gui

    calls = []
    original = mac_server_gui.serve

    def fake_serve(*args, **kwargs):
        calls.append((args, kwargs))
        if len(calls) < 3:
            raise OSError(errno.EADDRINUSE, 'address in use')

    try:
        mac_server_gui.serve = fake_serve
        stop = threading.Event()
        mac_server_gui._serve_aqua_with_handoff_retry(
            '192.168.1.2', 45950, Path('/tmp/share'), 30.0, 2560, 8_000_000, object(),
            stop_event=stop, log=lambda _m: None, ready_callback=lambda _fp: None,
            retry_seconds=1.0,
        )
    finally:
        mac_server_gui.serve = original
    assert len(calls) == 3


def test_aqua_handoff_detects_gui_domain_when_console_owner_is_stale():
    import mac_server_gui

    original_console = mac_server_gui._console_session_user
    original_gui = mac_server_gui._gui_session_available
    original_getpwnam = mac_server_gui.pwd.getpwnam
    try:
        mac_server_gui._console_session_user = lambda: 'root'
        mac_server_gui._gui_session_available = lambda user: user == 'dev'

        class Pw:
            pw_uid = 501

        mac_server_gui.pwd.getpwnam = lambda _user: Pw()
        user, source = mac_server_gui._detect_aqua_session_user('dev', probe_gui_domain=True)
        assert user == 'dev'
        assert source == 'launchctl gui/501'

        # The bounded monitor should be able to skip the heavier launchctl probe
        # between intervals without fabricating an Aqua session.
        user2, source2 = mac_server_gui._detect_aqua_session_user('dev', probe_gui_domain=False)
        assert user2 == ''
        assert source2 == ''
    finally:
        mac_server_gui._console_session_user = original_console
        mac_server_gui._gui_session_available = original_gui
        mac_server_gui.pwd.getpwnam = original_getpwnam


def test_aqua_handoff_prefers_real_console_owner():
    import mac_server_gui

    original_console = mac_server_gui._console_session_user
    original_gui = mac_server_gui._gui_session_available
    try:
        mac_server_gui._console_session_user = lambda: 'dev'
        mac_server_gui._gui_session_available = lambda _user: False
        user, source = mac_server_gui._detect_aqua_session_user('dev', probe_gui_domain=True)
        assert user == 'dev'
        assert source == '/dev/console'
    finally:
        mac_server_gui._console_session_user = original_console
        mac_server_gui._gui_session_available = original_gui


def test_runtime_uses_shared_lifecycle_module():
    import mac_server
    import zmb_session_lifecycle as lifecycle

    assert mac_server.PreloginSessionCoordinator is lifecycle.PreloginSessionCoordinator
    assert mac_server.PreloginControlLease is lifecycle.PreloginControlLease


def test_native_stage_d_contracts():
    # Native code cannot execute in Linux CI; its build is enforced by Stage A.
    # Keep only the small structural checks that describe cross-language ABI/state.
    native_input = (ROOT / 'native/macos/ZMBLoginInput.c').read_text(encoding='utf-8')
    streamer = (ROOT / 'native/macos/ZMBStreamerLib.swift').read_text(encoding='utf-8')

    assert 'pthread_mutex_t g_zmb_hid_lock' in native_input
    assert 'g_zmb_hid_refs' in native_input
    for stage in ('capture-frame', 'pixel-buffer', 'encode-submit', 'h264-written'):
        assert f'markPreloginStage("{stage}")' in streamer
    assert 'preloginEncodeSubmitted' in streamer


def main():
    test_control_handoff_packet_is_explicit()
    test_aqua_bind_retry_is_bounded()
    test_aqua_handoff_detects_gui_domain_when_console_owner_is_stale()
    test_aqua_handoff_prefers_real_console_owner()
    test_runtime_uses_shared_lifecycle_module()
    test_native_stage_d_contracts()
    print('Stage D pre-login core lifecycle regression checks passed')


if __name__ == '__main__':
    main()
