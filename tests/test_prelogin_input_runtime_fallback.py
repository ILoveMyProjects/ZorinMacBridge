#!/usr/bin/env python3
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import mac_server


class FakeLib:
    def __init__(self, *, corehid_rc=-11, opens=None):
        self.corehid_rc = int(corehid_rc)
        self.opens = {1: -101, 2: -102, 3: 0, **(opens or {})}
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

    def zmb_login_input_open(self):
        self.calls.append(('legacy_open_auto',))
        return 0

    def zmb_login_input_open_backend(self, backend):
        backend = int(backend)
        rc = int(self.opens.get(backend, -199))
        self.calls.append(('legacy_open_backend', backend, rc))
        self.active_backend = backend if rc == 0 else 0
        return rc

    def zmb_login_input_backend(self):
        self.calls.append(('legacy_backend',))
        return self.active_backend

    def zmb_login_input_close(self):
        self.calls.append(('legacy_close',))
        self.active_backend = 0

    def zmb_login_input_hid_key(self, usage, down):
        self.calls.append(('userdevice_key', int(usage), int(down)))
        return 0

    def zmb_login_input_hid_pointer(self, x, y, buttons, wheel):
        self.calls.append(('userdevice_pointer', int(x), int(y), int(buttons), int(wheel)))
        return 0

    def zmb_login_input_key(self, code, down, flags, modifier):
        flags = int(getattr(flags, 'value', flags))
        self.calls.append(('legacy_key', self.active_backend, int(code), int(down), flags, int(modifier)))
        return 0

    def zmb_login_input_pointer(self, event_type, x, y, button, flags):
        flags = int(getattr(flags, 'value', flags))
        event_type = int(getattr(event_type, 'value', event_type))
        self.calls.append(('legacy_pointer', self.active_backend, event_type, int(x), int(y), int(button), flags))
        return 0

    def zmb_login_input_scroll(self, dx, dy, flags):
        flags = int(getattr(flags, 'value', flags))
        self.calls.append(('legacy_scroll', self.active_backend, int(dx), int(dy), flags))
        return 0


class FakeNative:
    def __init__(self, lib):
        self.lib = lib


def fake_mac_input_init(self):
    self.held_buttons = set()
    self.held_modifiers = set()


def run_case(lib, outcomes):
    mac_server.NativeStreamerLibrary = lambda: FakeNative(lib)
    mac_server.LoginWindowInput._probe_keyboard_delivery = lambda self, backend: (
        outcomes.get(backend, ('FAIL', 'FAIL'))[0], f'{backend}-keyboard-test'
    )
    mac_server.LoginWindowInput._probe_pointer_delivery = lambda self, backend: (
        outcomes.get(backend, ('FAIL', 'FAIL'))[1], f'{backend}-pointer-test'
    )
    return mac_server.LoginWindowInput()


def run():
    original_init = mac_server.MacInput.__init__
    original_native = mac_server.NativeStreamerLibrary
    original_keyboard_probe = mac_server.LoginWindowInput._probe_keyboard_delivery
    original_pointer_probe = mac_server.LoginWindowInput._probe_pointer_delivery
    try:
        mac_server.MacInput.__init__ = fake_mac_input_init

        # Exact real-Mac failure from 0.15.29+: CoreHID returns -11. The matrix
        # must prove IOHIDUserDevice delivery before selecting it.
        lib = FakeLib(corehid_rc=-11, opens={1: 0, 2: 0, 3: 0})
        inp = run_case(lib, {
            'userdevice': ('PASS', 'PASS'),
            'iohidsystem': ('FAIL', 'FAIL'),
            'quartz': ('FAIL', 'FAIL'),
        })
        assert inp.backend == 'userdevice'
        assert inp.corehid_open_rc == -11
        assert inp.legacy_backend == 1
        assert 'delivery-probed' in inp.backend_summary()
        assert any(r['backend'] == 'corehid' and r['keyboard'] == 'FAIL_OPEN' for r in inp.input_probe_results)
        assert any(r['backend'] == 'userdevice' and r['selected'] for r in inp.input_probe_results)
        inp.key('a', True)
        inp.key('a', False)
        assert ('userdevice_key', 0x04, 1) in lib.calls
        assert ('userdevice_key', 0x04, 0) in lib.calls
        inp.release_all()

        # A backend that opens but fails delivery MUST NOT hide the next option.
        lib2 = FakeLib(corehid_rc=-11, opens={1: 0, 2: 0, 3: 0})
        inp2 = run_case(lib2, {
            'userdevice': ('FAIL', 'FAIL'),
            'iohidsystem': ('PASS', 'PASS'),
            'quartz': ('PASS', 'PASS'),
        })
        assert inp2.backend == 'iohidsystem'
        assert inp2.legacy_backend == 2
        inp2.key('a', True)
        assert any(call[:4] == ('legacy_key', 2, 0, 1) for call in lib2.calls)

        # Quartz is only selected if all earlier native paths fail their actual
        # delivery proof, not merely because their open() failed/succeeded.
        lib3 = FakeLib(corehid_rc=-11, opens={1: 0, 2: 0, 3: 0})
        inp3 = run_case(lib3, {
            'userdevice': ('FAIL', 'FAIL'),
            'iohidsystem': ('FAIL', 'FAIL'),
            'quartz': ('PASS', 'PASS'),
        })
        assert inp3.backend == 'quartz'
        assert inp3.legacy_backend == 3
        assert 'kCGSessionEventTap' in inp3.backend_summary()

        # If no backend proves keyboard delivery, constructor remains non-fatal
        # so LoginWindow video/control can stay alive and the matrix is visible.
        lib4 = FakeLib(corehid_rc=-11, opens={1: -1, 2: -2, 3: 0})
        inp4 = run_case(lib4, {'quartz': ('FAIL', 'PASS')})
        assert inp4.backend == 'unavailable'
        assert len(inp4.input_probe_results) == 4
        try:
            inp4.key('a', True)
        except RuntimeError as exc:
            assert 'delivery matrix' in str(exc)
        else:
            raise AssertionError('unavailable input event must report an error')
    finally:
        mac_server.MacInput.__init__ = original_init
        mac_server.NativeStreamerLibrary = original_native
        mac_server.LoginWindowInput._probe_keyboard_delivery = original_keyboard_probe
        mac_server.LoginWindowInput._probe_pointer_delivery = original_pointer_probe


if __name__ == '__main__':
    run()
    print('pre-login runtime input delivery-matrix tests: OK')
