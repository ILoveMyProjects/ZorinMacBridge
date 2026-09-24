from pathlib import Path

root = Path(__file__).resolve().parent
client = (root / 'linux_client_gtk.py').read_text(encoding='utf-8')
server = (root / 'mac_server_gui.py').read_text(encoding='utf-8')
release = (root / '.github/workflows/release.yml').read_text(encoding='utf-8')

assert "gi.require_version('Gtk', '4.0')" in client
assert "gi.require_version('Adw', '1')" in client
assert 'class ClientWindow(Adw.ApplicationWindow, CoreClient)' in client
assert 'def connect_remote(' in client
assert 'def disconnect_remote(' in client
assert "Gtk.ListBox()" in client
assert "folder-symbolic" in client
assert "Gtk.FileDialog" in client
assert "Double-click" in (root / 'README.md').read_text(encoding='utf-8')
assert 'def open_shared_folder(' in server
assert "['/usr/bin/open', str(path)]" in server
assert "accelerator='⌘⇧O'" in server
assert 'gir1.2-gtk-4.0' in release and 'gir1.2-adw-1' in release
assert 'linux_client_gtk.py' in release
print('native UI static tests: OK')
