from pathlib import Path

ROOT = Path(__file__).resolve().parent
client = (ROOT / "linux_client_gtk.py").read_text(encoding="utf-8")

assert (ROOT / "VERSION").read_text(encoding="utf-8").strip() == "0.14.6"
assert "def _build_fullscreen_bar" in client
assert "self.fullscreen_bar = self._build_fullscreen_bar()" in client
assert "outer.append(self.fullscreen_bar)" in client
assert "exit_btn = Gtk.Button(label='Exit Full Screen')" in client
assert "self.fs_quality_label" in client
assert "self.fs_input_label" in client
assert "self.status_box.set_visible(False)" in client
assert "self.status_box.set_visible(True)" in client

desktop_block = client.split("    def _build_desktop_page(self) -> Gtk.Widget:", 1)[1].split("    def _build_files_page", 1)[0]
assert "Gtk.Overlay" not in desktop_block
assert "add_overlay" not in desktop_block
assert "fullscreen_bar" not in desktop_block
assert "return frame" in desktop_block

css_block = client.split(".zmb-fullscreen-bar {", 1)[1].split("}", 1)[0]
assert "margin: 0" in css_block
assert "border-radius: 0" in css_block

print("fullscreen toolbar tests: OK")
