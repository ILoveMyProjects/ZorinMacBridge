#!/usr/bin/env python3
from __future__ import annotations

import ast
from pathlib import Path

from mac_server import MacInput
from protocol import KEY, TEXT, PacketReader, ascii_physical_keysym, unpack_json

ROOT = Path(__file__).resolve().parents[1]


def _method_source(filename: str, class_name: str, method_name: str) -> str:
    text = (ROOT / filename).read_text()
    tree = ast.parse(text)
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name == class_name:
            for child in node.body:
                if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)) and child.name == method_name:
                    return ast.get_source_segment(text, child) or ''
    raise AssertionError(f'{class_name}.{method_name} not found in {filename}')


def _function_source(filename: str, function_name: str) -> str:
    text = (ROOT / filename).read_text()
    tree = ast.parse(text)
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == function_name:
            return ast.get_source_segment(text, node) or ''
    raise AssertionError(f'{function_name} not found in {filename}')


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


def test_ascii_mapping_uses_existing_key_packet_path():
    assert ascii_physical_keysym('b') == ('b', False)
    assert ascii_physical_keysym('A') == ('a', True)
    assert ascii_physical_keysym('7') == ('7', False)
    assert ascii_physical_keysym('!') == ('1', True)
    assert ascii_physical_keysym('?') == ('slash', True)
    assert ascii_physical_keysym(' ') == ('space', False)
    assert ascii_physical_keysym('ą') is None


def test_clients_route_printable_ascii_to_physical_key_helper_before_text_fallback():
    tk = _method_source('linux_client.py', 'ClientApp', '_key_press')
    gtk = _method_source('linux_client_gtk.py', 'ClientWindow', '_gtk_key_pressed')
    for src in (tk, gtk):
        assert '_enqueue_printable_key' in src
        assert 'pack_packet(TEXT' in src
        assert src.index('_enqueue_printable_key') < src.index('pack_packet(TEXT')


def test_server_text_is_unicode_fallback_not_physical_ascii_synthesis():
    src = _method_source('mac_server.py', 'MacInput', 'text')
    assert 'ascii_keystroke_for_char' not in src
    assert 'CGEventSetFlags' not in src
    assert 'CGEventKeyboardSetUnicodeString' in src
    # ASCII is routed as KEY by the client; TEXT is only the Unicode fallback.
    assert 'CGEventCreateKeyboardEvent(None, 0, True)' in src


def test_control_session_does_not_disconnect_on_single_keyboard_injection_error():
    src = _function_source('mac_server.py', 'control_session')
    assert '[control] keyboard event rejected:' in src
    assert '[control] text event rejected:' in src
    assert 'except Exception as exc:' in src


class _FakeCG:
    def __init__(self):
        self.created = []
        self.unicode = []
        self.posted = []

    def CGEventCreateKeyboardEvent(self, _source, code, down):
        event = {'code': int(code), 'down': bool(down)}
        self.created.append(event)
        return event

    def CGEventKeyboardSetUnicodeString(self, event, count, _arr):
        self.unicode.append((event, int(count)))

    def CGEventPost(self, _tap, event):
        self.posted.append(event)


class _FakeCF:
    def CFRelease(self, _event):
        pass


def test_unicode_fallback_still_posts_text_without_throwing():
    inp = MacInput.__new__(MacInput)
    inp.cg = _FakeCG()
    inp.cf = _FakeCF()
    inp.held_modifiers = set()
    inp.held_buttons = set()
    inp.text('ą')
    assert [(e['code'], e['down']) for e in inp.cg.created] == [(0, True), (0, False)]
    assert len(inp.cg.unicode) == 2
    assert len(inp.cg.posted) == 2


def test_runtime_client_ascii_helper_when_dependencies_are_available():
    try:
        from linux_client import ClientApp
    except ModuleNotFoundError:
        # Lightweight artifact container may not ship PyAV/Tk runtime deps. CI/release
        # installs the full runtime and exercises this branch there.
        return

    class Var:
        def __init__(self, value):
            self.value = value
        def get(self):
            return self.value

    client = ClientApp.__new__(ClientApp)
    client.linux_shortcuts_var = Var(True)
    client.modifiers_down = set()
    packets = []
    client._enqueue = packets.append

    assert client._enqueue_printable_key('b') is True
    decoded = PacketReader().feed(b''.join(packets))
    assert [kind for kind, _ in decoded] == [KEY, KEY]
    events = [unpack_json(payload) for _, payload in decoded]
    assert events == [
        {'keysym': 'b', 'down': True},
        {'keysym': 'b', 'down': False},
    ]

    packets.clear()
    assert client._enqueue_printable_key('!') is True
    decoded = PacketReader().feed(b''.join(packets))
    events = [unpack_json(payload) for kind, payload in decoded if kind == KEY]
    assert events == [
        {'keysym': 'Shift_L', 'down': True},
        {'keysym': '1', 'down': True},
        {'keysym': '1', 'down': False},
        {'keysym': 'Shift_L', 'down': False},
    ]
    assert client._enqueue_printable_key('ą') is False


if __name__ == '__main__':
    test_linux_shortcut_keeps_right_ctrl_as_real_macos_control()
    test_paste_orders_clipboard_before_command_v()
    test_copy_cut_pull_is_scheduled_on_press_not_release()
    test_ascii_mapping_uses_existing_key_packet_path()
    test_clients_route_printable_ascii_to_physical_key_helper_before_text_fallback()
    test_server_text_is_unicode_fallback_not_physical_ascii_synthesis()
    test_control_session_does_not_disconnect_on_single_keyboard_injection_error()
    test_unicode_fallback_still_posts_text_without_throwing()
    test_runtime_client_ascii_helper_when_dependencies_are_available()
    print('keyboard/clipboard regression tests: OK')
