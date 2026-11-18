"""0.15.48 regressions: native encoder teardown, Tk scheduling, relative cursor."""
from __future__ import annotations

import queue
import sys
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import mac_server
from mac_server_gui import ServerGUI


def test_video_encoder_callback_owner_lifetime() -> None:
    swift = (ROOT / 'native/macos/ZMBStreamerLib.swift').read_text(encoding='utf-8')
    assert 'let callbackOwner = Unmanaged.passRetained(self)' in swift
    assert 'refcon: callbackOwner.toOpaque()' in swift
    assert 'callbackOwner.release()' in swift  # VT creation failure
    assert 'VTCompressionSessionCompleteFrames(session, untilPresentationTimeStamp: .invalid)' in swift
    assert 'VTCompressionSessionInvalidate(session)' in swift
    assert 'Unmanaged.passUnretained(self).release()' in swift
    assert 'retireEncoder(activeCompression)' in swift
    assert 'retireEncoder(session)' in swift  # VT prepare failure + cancelled creation
    assert 'sampleQueue.sync { }' in swift
    stop = swift.split('func requestStop(', 1)[1].split('private func start()', 1)[0]
    assert stop.index('done.signal()') < stop.index('retireEncoder(activeCompression)')
    assert 'VTCompressionSessionInvalidate(activeCompression)' not in stop
    assert 'stream-stop-timeout' in stop


def test_gui_jobs_never_enter_tcl_from_workers() -> None:
    class FakeRoot:
        def __init__(self) -> None:
            self.timers = []
            self.owner_tid = threading.get_ident()

        def after(self, delay: int, callback) -> None:
            assert threading.get_ident() == self.owner_tid, 'Tk was called from a worker thread'
            self.timers.append((delay, callback))

    # No actual Tk instance/window/virtual display required for this test.
    gui = ServerGUI.__new__(ServerGUI)
    gui.root = FakeRoot()
    gui._ui_jobs = queue.SimpleQueue()
    gui._ui_closed = False
    observed = []
    thread = threading.Thread(target=lambda: gui._post_ui(lambda: observed.append('delivered')))
    thread.start()
    thread.join(timeout=2.0)
    assert not thread.is_alive()
    assert observed == []
    assert gui.root.timers == []
    gui._drain_ui_jobs()
    assert observed == ['delivered']
    assert len(gui.root.timers) == 1
    gui._ui_closed = True
    gui._post_ui(lambda: observed.append('late callback'))
    gui.root.timers[0][1]()
    assert observed == ['delivered'], 'callback executed after GUI was closed'

    gui_text = (ROOT / 'mac_server_gui.py').read_text(encoding='utf-8')
    gui_impl = gui_text.split('class ServerGUI:', 1)[1]
    assert 'self.root.after(0,' not in gui_impl, 'a background-facing direct Tk after call remains'
    assert 'post_callback=self._post_ui' in gui_impl


def test_relative_cursor_first_move_does_not_jump_from_center() -> None:
    inp = mac_server.LoginWindowInput.__new__(mac_server.LoginWindowInput)
    inp.backend = 'driverkit'
    inp.last_nx, inp.last_ny = 0.5, 0.5
    inp._pointer_reference_valid = False
    inp._pointer_display_id = 0
    sent = []
    events = []
    inp._legacy_xy = lambda: (round(inp.last_nx * 1000), round(inp.last_ny * 1000))
    inp._pointer_report = lambda **kw: sent.append(kw)
    inp._emit_event = lambda **kw: events.append(kw)
    previous_display = mac_server.active_input_display
    try:
        mac_server.active_input_display = lambda: 0
        inp.move(0.94, 0.04)
        assert not sent, 'first remote motion moved the HID pointer from imaginary center'
        assert events[-1]['result'] == 'RELATIVE_BASELINE'
        inp.move(0.95, 0.05)
        assert sent[-1]['relative_delta'] == (10, 10)
        mac_server.active_input_display = lambda: 123
        inp.move(0.80, 0.60)
        assert len(sent) == 1, 'display change introduced an unexpected relative jump'
    finally:
        mac_server.active_input_display = previous_display


if __name__ == '__main__':
    test_video_encoder_callback_owner_lifetime()
    test_gui_jobs_never_enter_tcl_from_workers()
    test_relative_cursor_first_move_does_not_jump_from_center()
    print('0.15.48 native/GUI/cursor crash regression tests: OK')
