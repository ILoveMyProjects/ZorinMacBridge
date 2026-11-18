from __future__ import annotations

import ast
from pathlib import Path

from zmb_client_runtime import initialize_client_runtime_state

ROOT = Path(__file__).resolve().parents[1]


class Dummy:
    pass


def test_shared_runtime_defaults():
    obj = Dummy()
    initialize_client_runtime_state(obj)
    assert obj.outgoing.maxsize == 1000
    assert obj.video_frames.maxsize == 1
    assert obj.connected is False
    assert obj.prelogin_session is False
    assert obj.prelogin_video_ready is False
    assert obj.prelogin_input_ready is False
    assert obj._desktop_session_token == ''
    assert obj._desktop_session_generation == 0
    assert obj.fullscreen_active is False
    assert obj.disconnect_requested is False
    assert obj.mouse_buttons_down == set()
    assert obj.modifiers_down == set()
    assert obj.keys_down == set()
    assert obj.remote_image_size == (1, 1)
    assert obj.display_rect == (0, 0, 1, 1)


def _init_calls(path: Path, class_name: str) -> set[str]:
    tree = ast.parse(path.read_text(encoding='utf-8'), filename=str(path))
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name == class_name:
            for child in node.body:
                if isinstance(child, ast.FunctionDef) and child.name == '__init__':
                    calls = set()
                    for call in ast.walk(child):
                        if isinstance(call, ast.Call) and isinstance(call.func, ast.Name):
                            calls.add(call.func.id)
                    return calls
    raise AssertionError(f'{class_name}.__init__ not found in {path.name}')


def test_both_frontends_use_shared_initializer():
    assert 'initialize_client_runtime_state' in _init_calls(ROOT / 'linux_client.py', 'ClientApp')
    assert 'initialize_client_runtime_state' in _init_calls(ROOT / 'linux_client_gtk.py', 'ClientWindow')



def _assigned_self_attrs(path: Path, class_name: str) -> set[str]:
    tree = ast.parse(path.read_text(encoding='utf-8'), filename=str(path))
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name == class_name:
            for child in node.body:
                if isinstance(child, ast.FunctionDef) and child.name == '__init__':
                    attrs = set()
                    for item in ast.walk(child):
                        if isinstance(item, (ast.Assign, ast.AnnAssign)):
                            targets = item.targets if isinstance(item, ast.Assign) else [item.target]
                            for target in targets:
                                if isinstance(target, ast.Attribute) and isinstance(target.value, ast.Name) and target.value.id == 'self':
                                    attrs.add(target.attr)
                    return attrs
    raise AssertionError(f'{class_name}.__init__ not found in {path.name}')


def test_gtk_frontend_keeps_frontend_only_state():
    attrs = _assigned_self_attrs(ROOT / 'linux_client_gtk.py', 'ClientWindow')
    required = {
        'machine_var', 'remember_credentials_var', 'discovered_by_label',
        'selected_server_id', 'selected_server_name', 'selected_desktop_available',
        'selected_system_available', 'selected_paired_only', '_display_choices',
        '_reconnect_source', '_reconnect_attempt', '_discovery_watchdog',
        '_file_render_serial', '_desktop_trust_recovery_busy',
        '_online_desktop_recovery_busy', '_wake_connect_busy', '_display_wake_busy',
        '_last_display_wake_at', '_closing',
    }
    missing = required - attrs
    assert not missing, f'GTK frontend-only state missing from constructor: {sorted(missing)}'

def main():
    test_shared_runtime_defaults()
    test_both_frontends_use_shared_initializer()
    test_gtk_frontend_keeps_frontend_only_state()
    print('shared client runtime state tests: OK')


if __name__ == '__main__':
    main()
