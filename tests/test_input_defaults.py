from pathlib import Path
import ast

ROOT = Path(__file__).resolve().parents[1]
gtk = (ROOT / 'linux_client_gtk.py').read_text(encoding='utf-8')
legacy = (ROOT / 'linux_client.py').read_text(encoding='utf-8')

assert (ROOT / 'VERSION').read_text(encoding='utf-8').strip() == '0.14.18'
assert 'self.capture_input_var = ValueVar(True)' in gtk
assert 'self.capture_input_var = tk.BooleanVar(value=True)' in legacy
assert "self.enter_fullscreen_btn = Gtk.Button(label='Full Screen')" in gtk
assert "ttk.Button(options, text='Full Screen', command=self._toggle_fullscreen)" in legacy
assert "self.canvas.bind('<Double-Button-1>', self._toggle_fullscreen)" not in legacy
assert 'def _fullscreen_bar_pressed' not in gtk


def method_source(text: str, cls: str, method: str) -> str:
    tree = ast.parse(text)
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name == cls:
            for child in node.body:
                if isinstance(child, ast.FunctionDef) and child.name == method:
                    return ast.get_source_segment(text, child) or ''
    raise AssertionError(f'{cls}.{method} missing')

pressed = method_source(gtk, 'ClientWindow', '_gtk_pressed')
assert '_toggle_fullscreen' not in pressed
assert 'n_press == 2' not in pressed
assert 'MOUSE_BUTTON' in pressed

queue = method_source(gtk, 'ClientWindow', '_process_gtk_queue')
assert "if self.capture_input_var.get():" in queue
assert "self.status_var.set('Connected — keyboard & mouse capture enabled')" in queue
assert 'self.picture.grab_focus()' in queue

legacy_queue = method_source(legacy, 'ClientApp', '_process_ui_queue')
assert "if self.capture_input_var.get():" in legacy_queue
assert "self.status_var.set('Connected — keyboard & mouse capture enabled')" in legacy_queue
assert 'self.canvas.focus_set()' in legacy_queue

readme = (ROOT / 'README.md').read_text(encoding='utf-8')
assert 'Double-click is never consumed by the local UI' in readme
assert 'Use the explicit **Full Screen** button' in readme

print('default input / explicit fullscreen regression tests: OK')
