#!/usr/bin/env python3
from __future__ import annotations

import ast
from pathlib import Path

from mac_server import MacInput, ascii_keystroke_for_char

ROOT = Path(__file__).resolve().parent


def _method_source(filename: str, class_name: str, method_name: str) -> str:
    text = (ROOT / filename).read_text()
    tree = ast.parse(text)
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name == class_name:
            for child in node.body:
                if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)) and child.name == method_name:
                    return ast.get_source_segment(text, child) or ''
    raise AssertionError(f'{class_name}.{method_name} not found in {filename}')


def test_linux_shortcut_keeps_right_ctrl_as_real_macos_control():
    src = _method_source('linux_client.py', 'ClientApp', '_map_keysym')
    assert "keysym == 'Control_L'" in src
    assert "return 'Meta_L'" in src
    assert "Control_R" not in src


def test_paste_orders_clipboard_before_command_v():
    core = _method_source('linux_client.py', 'ClientApp', '_paste_clipboard_shortcut')
    gtk = _method_source('linux_client_gtk.py', 'ClientWindow', '_paste_clipboard_shortcut')
    assert core.index('CLIPBOARD_SET') < core.index("_enqueue_command_shortcut('v')")
    assert gtk.index('CLIPBOARD_SET') < gtk.index("_enqueue_command_shortcut('v')")


def test_copy_cut_pull_is_scheduled_on_press_not_release():
    press = _method_source('linux_client_gtk.py', 'ClientWindow', '_gtk_key_pressed')
    release = _method_source('linux_client_gtk.py', 'ClientWindow', '_gtk_key_released')
    assert "key_lower in {'c', 'x'}" in press
    assert '_request_remote_clipboard' in press
    assert '_request_remote_clipboard' not in release


def test_ascii_key_mapping_for_simulator():
    assert ascii_keystroke_for_char('b') == ('b', False)
    assert ascii_keystroke_for_char('A') == ('a', True)
    assert ascii_keystroke_for_char('7') == ('7', False)
    assert ascii_keystroke_for_char('!') == ('1', True)
    assert ascii_keystroke_for_char('?') == ('slash', True)
    assert ascii_keystroke_for_char(' ') == ('space', False)
    assert ascii_keystroke_for_char('ą') is None


class _FakeCG:
    def __init__(self):
        self.created = []
        self.flags = []
        self.unicode = []
        self.posted = []

    def CGEventCreateKeyboardEvent(self, _source, code, down):
        event = {'code': int(code), 'down': bool(down)}
        self.created.append(event)
        return event

    def CGEventSetFlags(self, event, flags):
        value = flags.value if hasattr(flags, 'value') else int(flags)
        self.flags.append((event, int(value)))

    def CGEventKeyboardSetUnicodeString(self, event, count, _arr):
        self.unicode.append((event, int(count)))

    def CGEventPost(self, _tap, event):
        self.posted.append(event)


class _FakeCF:
    def CFRelease(self, _event):
        pass


def test_ascii_text_uses_real_mac_keycodes_not_keycode_zero_for_every_char():
    inp = MacInput.__new__(MacInput)
    inp.cg = _FakeCG()
    inp.cf = _FakeCF()
    inp.held_modifiers = set()
    inp.held_buttons = set()
    inp.text('bc!')
    # b=11, c=8, !=Shift+1 (18). The old implementation used keycode 0
    # for every character, which iOS Simulator could interpret as repeated A.
    down_events = [event for event in inp.cg.created if event['down']]
    assert [event['code'] for event in down_events] == [11, 8, 18]
    assert all(event['code'] != 0 for event in down_events)
    # ! must carry the macOS Shift flag.
    shifted_event = down_events[-1]
    shifted_flags = [flags for event, flags in inp.cg.flags if event is shifted_event]
    assert shifted_flags == [MacInput.MOD_FLAGS['Shift_L']]


def test_runtime_client_helpers_when_dependencies_are_available():
    try:
        from linux_client import ClientApp
        from protocol import CLIPBOARD_SET, KEY, PacketReader, unpack_json
    except ModuleNotFoundError:
        # Minimal artifact container does not ship PyAV. CI/release installs the
        # full runtime and executes this branch there; static guards above still
        # protect the source shape in lightweight environments.
        return

    class Var:
        def __init__(self, value):
            self.value = value
        def get(self):
            return self.value

    client = ClientApp.__new__(ClientApp)
    client.linux_shortcuts_var = Var(True)
    client.modifiers_down = {'Meta_L'}
    client.connected = True
    packets = []
    client._enqueue = packets.append
    client._get_local_clipboard = lambda: 'runtime clipboard'

    assert client._map_keysym('Control_L') == 'Meta_L'
    assert client._map_keysym('Control_R') == 'Control_R'
    client._paste_clipboard_shortcut()
    decoded = PacketReader().feed(b''.join(packets))
    assert decoded[0] == (CLIPBOARD_SET, b'runtime clipboard')
    key_events = [unpack_json(payload) for kind, payload in decoded[1:] if kind == KEY]
    assert key_events[1:3] == [
        {'keysym': 'v', 'down': True},
        {'keysym': 'v', 'down': False},
    ]


if __name__ == '__main__':
    test_linux_shortcut_keeps_right_ctrl_as_real_macos_control()
    test_paste_orders_clipboard_before_command_v()
    test_copy_cut_pull_is_scheduled_on_press_not_release()
    test_ascii_key_mapping_for_simulator()
    test_ascii_text_uses_real_mac_keycodes_not_keycode_zero_for_every_char()
    test_runtime_client_helpers_when_dependencies_are_available()
    print('keyboard/clipboard regression tests: OK')
