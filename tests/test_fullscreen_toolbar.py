from pathlib import Path
import ast

ROOT = Path(__file__).resolve().parents[1]
client = (ROOT / "linux_client_gtk.py").read_text(encoding="utf-8")
legacy = (ROOT / "linux_client.py").read_text(encoding="utf-8")

assert (ROOT / "VERSION").read_text(encoding="utf-8").strip() == "0.14.17"
assert "def _build_fullscreen_bar" in client
assert "self.fullscreen_bar = self._build_fullscreen_bar()" in client
assert "outer.append(self.fullscreen_bar)" in client
assert "exit_btn = Gtk.Button(label='Exit Full Screen')" in client
assert "self.fs_quality_label" in client
assert "self.fs_input_label" in client
assert "self.status_box.set_visible(not active)" in client
assert "self.fullscreen_bar.set_visible(active)" in client

desktop_block = client.split("    def _build_desktop_page(self) -> Gtk.Widget:", 1)[1].split("    def _build_files_page", 1)[0]
assert "Gtk.Overlay" not in desktop_block
assert "add_overlay" not in desktop_block
assert "fullscreen_bar" not in desktop_block
assert "return frame" in desktop_block

css_block = client.split(".zmb-fullscreen-bar {", 1)[1].split("}", 1)[0]
assert "margin: 0" in css_block
assert "border-radius: 0" in css_block


def method_source(text: str, cls: str, method: str) -> str:
    tree = ast.parse(text)
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name == cls:
            for child in node.body:
                if isinstance(child, ast.FunctionDef) and child.name == method:
                    return ast.get_source_segment(text, child) or ''
    raise AssertionError(f'{cls}.{method} missing')

# Regression: reconnect must never clear full-screen state while the physical GTK
# window remains fullscreen. This was the reason Exit/Esc stopped working after a
# keyboard-triggered reconnect.
connect_src = method_source(client, 'ClientWindow', 'connect_remote')
assert 'self.fullscreen_active = False' not in connect_src
legacy_connect = method_source(legacy, 'ClientApp', 'connect')
assert 'self.fullscreen_active = False' not in legacy_connect

# Escape handling must happen before remote input dispatch and work for both plain
# Esc and Alt+Esc. Capture phase makes it independent from focused child widgets.
install_src = method_source(client, 'ClientWindow', '_install_controllers')
assert 'Gtk.PropagationPhase.CAPTURE' in install_src
press_src = method_source(client, 'ClientWindow', '_gtk_key_pressed')
assert "keysym == 'Escape' and (self._window_is_fullscreen() or self.fullscreen_active)" in press_src
assert 'ALT_MASK' not in press_src.split("keysym == 'Escape'", 1)[0]
legacy_press = method_source(legacy, 'ClientApp', '_key_press')
assert "event.keysym == 'Escape' and self.fullscreen_active" in legacy_press

# Exit is deliberately idempotent: never refuse to unfullscreen just because an
# internal boolean got out of sync with the real GTK window state.
exit_src = method_source(client, 'ClientWindow', '_exit_fullscreen')
assert 'Gtk.Window.unfullscreen(self)' in exit_src
assert 'if not self.fullscreen_active' not in exit_src
assert '_apply_fullscreen_layout(False)' in exit_src


# Regression: the GTK constructor must initialize full-screen state before any
# button/key callback can read it. The missing initialization made both buttons
# look dead by raising AttributeError inside the GTK signal callback.
init_src = method_source(client, 'ClientWindow', '__init__')
assert 'self.fullscreen_active = False' in init_src
assert "self.connect('notify::fullscreened', self._fullscreened_changed)" in init_src
assert 'def _window_is_fullscreen' in client
assert 'def _fullscreened_changed' in client
toggle_src = method_source(client, 'ClientWindow', '_toggle_fullscreen')
assert 'Gtk.Window.fullscreen(self)' in toggle_src
assert '_apply_fullscreen_layout(True)' in toggle_src

assert "self.root.bind_all('<Escape>'" in legacy
assert "self.root.bind_all('<Alt-Escape>'" in legacy
assert 'def _fullscreen_bar_pressed' not in client
assert "self.enter_fullscreen_btn = Gtk.Button(label='Full Screen')" in client
assert "n_press == 2 and not self.fullscreen_active" not in client

print("fullscreen toolbar/escape regression tests: OK")
