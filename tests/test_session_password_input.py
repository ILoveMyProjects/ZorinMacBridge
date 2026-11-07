from pathlib import Path
import ast

ROOT = Path(__file__).resolve().parents[1]
client = (ROOT / 'linux_client_gtk.py').read_text(encoding='utf-8')

assert (ROOT / 'VERSION').read_text(encoding='utf-8').strip() == '0.15.6'
assert "self.password_entry = self._entry(second, 'ZMB session password'" in client


def method_source(name: str) -> str:
    tree = ast.parse(client)
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name == 'ClientWindow':
            for child in node.body:
                if isinstance(child, ast.FunctionDef) and child.name == name:
                    return ast.get_source_segment(client, child) or ''
    raise AssertionError(f'ClientWindow.{name} missing')


focus = method_source('_local_text_input_focused')
assert 'Gtk.Editable' in focus
assert 'Gtk.TextView' in focus
assert 'self.get_focus()' in focus

pressed = method_source('_gtk_key_pressed')
assert 'self._local_text_input_focused()' in pressed
assert 'if not self._remote_input_ready()' in pressed
assert pressed.index('self._local_text_input_focused()') < pressed.index('self._remote_input_ready()')

released = method_source('_gtk_key_released')
assert 'self._local_text_input_focused()' in released
assert 'if not self._remote_input_ready()' in released

params = method_source('_connection_params')
assert "raise ValueError('Enter the ZMB session password.')" in params

queue = method_source('_process_gtk_queue')
assert "auth_failed = 'bad password' in str(reason).casefold()" in queue
assert "Authentication failed — check ZMB session password" in queue
assert "auto reconnect suppressed" in queue
assert 'self.password_entry.grab_focus()' in queue
assert 'self.password_entry.select_region(0, -1)' in queue
# The auth-failure branch must suppress _schedule_auto_reconnect; reconnect is only
# present in the following elif for non-authentication disconnects.
assert "elif reason != 'Disconnected by user' and not self.disconnect_requested:" in queue

print('session password input / auth reconnect regression tests: OK')
