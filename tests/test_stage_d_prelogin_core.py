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
    test_runtime_uses_shared_lifecycle_module()
    test_native_stage_d_contracts()
    print('Stage D pre-login core lifecycle regression checks passed')


if __name__ == '__main__':
    main()
