#!/usr/bin/env python3
from pathlib import Path
import sys
import threading
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import mac_server


class FakeLib:
    def __init__(self, *, corehid_rc=-11, opens=None, key_rc=None, pointer_rc=None):
        self.corehid_rc = int(corehid_rc)
        self.opens = {1: -101, 2: -102, 3: -103, **(opens or {})}
        self.key_rc = {1: 0, 2: 0, 3: 0, **(key_rc or {})}
        self.pointer_rc = {1: 0, 2: 0, 3: 0, **(pointer_rc or {})}
        self.active_backend = 0
        self.calls = []

    def zmb_corehid_input_open(self):
        self.calls.append(('corehid_open',))
        return self.corehid_rc

    def zmb_corehid_input_close(self):
        self.calls.append(('corehid_close',))

    def zmb_corehid_input_key(self, usage, down):
        self.calls.append(('corehid_key', int(usage), int(down)))
        return 0

    def zmb_corehid_input_pointer(self, x, y, buttons, wheel):
        self.calls.append(('corehid_pointer', int(x), int(y), int(buttons), int(wheel)))
        return 0

    def zmb_login_input_open_backend(self, backend):
        backend = int(backend)
        rc = int(self.opens.get(backend, -199))
        self.calls.append(('legacy_open_backend', backend, rc))
        self.active_backend = backend if rc == 0 else 0
        return rc

    def zmb_login_input_close(self):
        self.calls.append(('legacy_close',))
        self.active_backend = 0

    def zmb_login_input_hid_key(self, usage, down):
        rc = int(self.key_rc.get(1, 0))
        self.calls.append(('userdevice_key', int(usage), int(down), rc))
        return rc

    def zmb_login_input_hid_pointer(self, x, y, buttons, wheel):
        rc = int(self.pointer_rc.get(1, 0))
        self.calls.append(('userdevice_pointer', int(x), int(y), int(buttons), int(wheel), rc))
        return rc

    def zmb_login_input_key(self, code, down, flags, modifier):
        flags = int(getattr(flags, 'value', flags))
        rc = int(self.key_rc.get(self.active_backend, 0))
        self.calls.append(('legacy_key', self.active_backend, int(code), int(down), flags, int(modifier), rc))
        return rc

    def zmb_login_input_pointer(self, event_type, x, y, button, flags):
        flags = int(getattr(flags, 'value', flags))
        event_type = int(getattr(event_type, 'value', event_type))
        rc = int(self.pointer_rc.get(self.active_backend, 0))
        self.calls.append(('legacy_pointer', self.active_backend, event_type, int(x), int(y), int(button), flags, rc))
        return rc

    def zmb_login_input_pointer_relative(self, dx, dy, buttons, flags):
        flags = int(getattr(flags, 'value', flags))
        buttons = int(getattr(buttons, 'value', buttons))
        rc = int(self.pointer_rc.get(self.active_backend, 0))
        self.calls.append(('legacy_pointer_relative', self.active_backend, int(dx), int(dy), buttons, flags, rc))
        return rc

    def zmb_login_input_button(self, button, down, flags):
        flags = int(getattr(flags, 'value', flags))
        rc = int(self.pointer_rc.get(self.active_backend, 0))
        self.calls.append(('legacy_button', self.active_backend, int(button), int(down), flags, rc))
        return rc

    def zmb_login_input_scroll(self, dx, dy, flags):
        flags = int(getattr(flags, 'value', flags))
        rc = int(self.pointer_rc.get(self.active_backend, 0))
        self.calls.append(('legacy_scroll', self.active_backend, int(dx), int(dy), flags, rc))
        return rc

    def zmb_login_input_quartz_key(self, code, down, flags):
        flags = int(getattr(flags, 'value', flags))
        rc = int(self.key_rc.get(3, 0))
        self.calls.append(('quartz_key_direct', int(code), int(down), flags, rc))
        return rc

    def zmb_login_input_quartz_pointer(self, event_type, x, y, button, flags):
        flags = int(getattr(flags, 'value', flags))
        event_type = int(getattr(event_type, 'value', event_type))
        rc = int(self.pointer_rc.get(3, 0))
        self.calls.append(('quartz_pointer_direct', event_type, int(x), int(y), int(button), flags, rc))
        return rc

    def zmb_login_input_quartz_scroll(self, dx, dy, flags):
        flags = int(getattr(flags, 'value', flags))
        rc = int(self.pointer_rc.get(3, 0))
        self.calls.append(('quartz_scroll_direct', int(dx), int(dy), flags, rc))
        return rc


class FakeNative:
    def __init__(self, lib):
        self.lib = lib


def fake_mac_input_init(self):
    self.held_buttons = set()
    self.held_modifiers = set()


def fake_bounds(self):
    return mac_server.CGRect(mac_server.CGPoint(0, 0), mac_server.CGSize(1680, 1050))


def make_input(lib, states):
    mac_server.NativeStreamerLibrary = lambda: FakeNative(lib)
    inp = mac_server.LoginWindowInput()
    inp.set_state_callback(states.append, replay=True)
    return inp


def run():
    original_init = mac_server.MacInput.__init__
    original_bounds = mac_server.MacInput._bounds
    original_native = mac_server.NativeStreamerLibrary
    original_flags = mac_server.MacInput._combined_session_flags
    original_cursor = mac_server.MacInput._cursor_location
    try:
        mac_server.MacInput.__init__ = fake_mac_input_init
        mac_server.MacInput._bounds = fake_bounds
        # These calls were the old critical-startup delivery proof. They must
        # never run while constructing LoginWindowInput anymore.
        mac_server.MacInput._combined_session_flags = lambda self: (_ for _ in ()).throw(AssertionError('flags probe called'))
        mac_server.MacInput._cursor_location = lambda self: (_ for _ in ()).throw(AssertionError('cursor probe called'))

        # Real historical CoreHID failure (-11) must fall directly to root
        # IOHIDSystem before IOHIDUserDevice and become usable immediately.
        states = []
        lib = FakeLib(corehid_rc=-11, opens={2: 0, 1: 0, 3: 0})
        inp = make_input(lib, states)
        assert inp.backend == 'iohidsystem'
        assert inp.corehid_open_rc == -11
        assert inp.legacy_backend == 2
        assert [c for c in lib.calls if c[0] in ('corehid_open', 'legacy_open_backend')] == [
            ('corehid_open',), ('legacy_open_backend', 2, 0)
        ]
        assert states[-1]['backend'] == 'iohidsystem'
        assert states[-1]['result'] == 'OPEN_READY'
        assert states[-1]['selected'] is True
        inp.key('a', True)
        assert any(c[:4] == ('legacy_key', 2, 0, 1) for c in lib.calls)
        assert any(s['backend'] == 'iohidsystem' and s['result'] == 'ACTIVE' and s['keyboard'] == 'DISPATCH_OK' for s in states)
        inp.release_all()

        # A real dispatch error must not kill Desktop/video. Close the failed
        # backend, open the next candidate and retry the SAME key once.
        states2 = []
        lib2 = FakeLib(corehid_rc=-11, opens={2: 0, 1: 0, 3: 0}, key_rc={2: -700, 1: 0})
        inp2 = make_input(lib2, states2)
        assert inp2.backend == 'iohidsystem'
        inp2.key('a', True)
        assert inp2.backend == 'userdevice'
        assert any(c[:4] == ('legacy_key', 2, 0, 1) and c[-1] == -700 for c in lib2.calls)
        assert ('legacy_open_backend', 1, 0) in lib2.calls
        assert any(c[:3] == ('userdevice_key', 0x04, 1) and c[-1] == 0 for c in lib2.calls)
        assert any(s['backend'] == 'iohidsystem' and s['result'] == 'DISPATCH_FAIL' for s in states2)
        assert any(s['backend'] == 'userdevice' and s['result'] == 'OPEN_READY' and s['selected'] for s in states2)
        inp2.release_all()

        # IOHIDSystem pointer movement now uses relative cursor movement, which
        # avoids the absolute kIOHIDSetCursorPosition privilege path seen on the
        # physical macOS 27 LoginWindow test.
        states3 = []
        lib3 = FakeLib(corehid_rc=-11, opens={2: 0, 1: 0, 3: 0}, pointer_rc={2: 0, 1: 0, 3: 0})
        inp3 = make_input(lib3, states3)
        inp3.move(0.25, 0.75)
        assert inp3.backend == 'iohidsystem'
        assert any(c[0] == 'legacy_pointer_relative' and c[1] == 2 and c[-1] == 0 for c in lib3.calls)
        inp3.release_all()

        # If IOHIDSystem pointer dispatch still fails, only POINTER falls back
        # to Quartz, and Quartz must run OUTSIDE the control thread.  A successful
        # CGEventPost return is diagnostic-only because LoginWindow delivery is
        # not independently observable.
        states3b = []
        events3b = []
        lib3b = FakeLib(corehid_rc=-11, opens={2: 0, 1: 0, 3: 0}, pointer_rc={2: -701, 3: 0})
        inp3b = make_input(lib3b, states3b)
        inp3b.set_event_callback(events3b.append)
        inp3b.move(0.25, 0.75)
        assert inp3b.backend == 'iohidsystem'
        assert inp3b._channel_override['pointer'] == 'quartz-async'
        assert inp3b._channel_override['keyboard'] == ''
        assert any(c[0] == 'legacy_pointer_relative' and c[-1] == -701 for c in lib3b.calls)
        deadline = time.monotonic() + 1.0
        while not any(c[0] == 'quartz_pointer_direct' for c in lib3b.calls) and time.monotonic() < deadline:
            time.sleep(0.005)
        inp3b.drain_async_events()
        assert any(c[0] == 'quartz_pointer_direct' and c[-1] == 0 for c in lib3b.calls)
        assert any(e['channel'] == 'pointer' and e['backend'] == 'quartz-async' and e['result'] == 'DISPATCH_ACCEPTED_UNVERIFIED' for e in events3b)
        inp3b.key('a', True)
        assert any(c[:4] == ('legacy_key', 2, 0, 1) and c[-1] == 0 for c in lib3b.calls)
        inp3b.release_all()

        # Regression for the physical 0.15.39 failure: a Quartz pointer call may
        # never return.  The move call must still return immediately, the next
        # keyboard packet must stay on IOHIDSystem, and a bounded timeout must be
        # reported without killing the control path.
        states3c = []
        events3c = []
        lib3c = FakeLib(corehid_rc=-11, opens={2: 0, 1: 0, 3: 0}, pointer_rc={2: -536870207, 3: 0})
        gate = threading.Event()
        original_quartz_pointer = lib3c.zmb_login_input_quartz_pointer

        def hanging_quartz_pointer(*args):
            lib3c.calls.append(('quartz_pointer_hang_enter',))
            gate.wait(2.0)
            return original_quartz_pointer(*args)

        lib3c.zmb_login_input_quartz_pointer = hanging_quartz_pointer
        inp3c = make_input(lib3c, states3c)
        inp3c.QUARTZ_ASYNC_TIMEOUT_SECONDS = 0.05
        inp3c.set_event_callback(events3c.append)
        started = time.monotonic()
        inp3c.move(0.25, 0.75)
        assert time.monotonic() - started < 0.04, 'pointer fallback blocked the control thread'
        assert any(c[0] == 'quartz_pointer_hang_enter' for c in lib3c.calls)

        # This is the key acceptance: keyboard is processed while Quartz pointer
        # remains hung in a daemon worker.
        inp3c.key('a', True)
        assert any(c[:4] == ('legacy_key', 2, 0, 1) and c[-1] == 0 for c in lib3c.calls)
        assert any(e['channel'] == 'keyboard' and e['backend'] == 'iohidsystem' and e['result'] == 'DISPATCH_OK' for e in events3c)

        time.sleep(0.08)
        inp3c.drain_async_events()
        assert any(e['channel'] == 'pointer' and e['backend'] == 'quartz-async' and e['result'] == 'DISPATCH_TIMEOUT' for e in events3c)
        assert inp3c._channel_override['pointer'] == 'quartz-disabled'
        gate.set()
        inp3c.release_all()

        # If every exact backend fails to open, construction remains non-fatal
        # so the proven LoginWindow video stays visible in view-only mode.
        states4 = []
        lib4 = FakeLib(corehid_rc=-11, opens={2: -2, 1: -1, 3: -3})
        inp4 = make_input(lib4, states4)
        assert inp4.backend == 'unavailable'
        assert states4[-1]['backend'] == 'unavailable'
        assert states4[-1]['result'] == 'UNAVAILABLE'
        try:
            inp4.key('a', True)
        except RuntimeError as exc:
            assert 'backend activation' in str(exc)
        else:
            raise AssertionError('unavailable input event must report an error')
    finally:
        mac_server.MacInput.__init__ = original_init
        mac_server.MacInput._bounds = original_bounds
        mac_server.NativeStreamerLibrary = original_native
        mac_server.MacInput._combined_session_flags = original_flags
        mac_server.MacInput._cursor_location = original_cursor


if __name__ == '__main__':
    run()
    print('pre-login runtime input activation/failover tests: OK')
