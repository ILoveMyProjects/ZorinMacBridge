#!/usr/bin/env python3
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import mac_server


class FakeLib:
    def __init__(self, legacy_rc=0):
        self.legacy_rc = legacy_rc
        self.calls = []

    def zmb_corehid_input_open(self):
        self.calls.append(('corehid_open',))
        return -11

    def zmb_corehid_input_close(self):
        self.calls.append(('corehid_close',))

    def zmb_corehid_input_key(self, usage, down):
        self.calls.append(('corehid_key', int(usage), int(down)))
        return 0

    def zmb_corehid_input_pointer(self, x, y, buttons, wheel):
        self.calls.append(('corehid_pointer', int(x), int(y), int(buttons), int(wheel)))
        return 0

    def zmb_login_input_open(self):
        self.calls.append(('legacy_open',))
        return self.legacy_rc

    def zmb_login_input_close(self):
        self.calls.append(('legacy_close',))

    def zmb_login_input_key(self, code, down, flags, modifier):
        flags = int(getattr(flags, 'value', flags))
        self.calls.append(('legacy_key', int(code), int(down), flags, int(modifier)))
        return 0

    def zmb_login_input_pointer(self, event_type, x, y, button, flags):
        flags = int(getattr(flags, 'value', flags))
        event_type = int(getattr(event_type, 'value', event_type))
        self.calls.append(('legacy_pointer', event_type, int(x), int(y), int(button), flags))
        return 0

    def zmb_login_input_scroll(self, dx, dy, flags):
        flags = int(getattr(flags, 'value', flags))
        self.calls.append(('legacy_scroll', int(dx), int(dy), flags))
        return 0


class FakeNative:
    def __init__(self, lib):
        self.lib = lib


def fake_mac_input_init(self):
    self.held_buttons = set()
    self.held_modifiers = set()


def run():
    original_init = mac_server.MacInput.__init__
    original_native = mac_server.NativeStreamerLibrary
    try:
        mac_server.MacInput.__init__ = fake_mac_input_init

        # Exact real-Mac failure: CoreHID returns -11. Production must fall back
        # instead of raising from LoginWindowInput.__init__ and killing control.
        lib = FakeLib(legacy_rc=0)
        mac_server.NativeStreamerLibrary = lambda: FakeNative(lib)
        inp = mac_server.LoginWindowInput()
        assert inp.backend == 'legacy'
        assert inp.corehid_open_rc == -11
        assert inp.legacy_open_rc == 0
        inp.key('a', True)
        inp.key('a', False)
        assert ('legacy_key', 0, 1, 0, 0) in lib.calls
        assert ('legacy_key', 0, 0, 0, 0) in lib.calls
        inp.release_all()
        assert ('legacy_close',) in lib.calls

        # Even if both native paths are unavailable, constructor must remain
        # non-fatal so the Desktop owner stays alive and pre-login video can
        # authenticate. Input failure is reported only when an event arrives.
        lib2 = FakeLib(legacy_rc=-99)
        mac_server.NativeStreamerLibrary = lambda: FakeNative(lib2)
        inp2 = mac_server.LoginWindowInput()
        assert inp2.backend == 'unavailable'
        try:
            inp2.key('a', True)
        except RuntimeError as exc:
            assert 'LoginWindow input unavailable' in str(exc)
        else:
            raise AssertionError('unavailable input event must report an error')
    finally:
        mac_server.MacInput.__init__ = original_init
        mac_server.NativeStreamerLibrary = original_native


if __name__ == '__main__':
    run()
    print('pre-login runtime input fallback tests: OK')
