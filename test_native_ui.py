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
assert 'Request Screen Recording Access' in server
assert 'Request Mouse/Keyboard Access' in server
assert "['/usr/bin/open', str(path)]" in server
assert "accelerator='⌘⇧O'" in server
assert 'gir1.2-gtk-4.0' in release and 'gir1.2-adw-1' in release
assert 'linux_client_gtk.py' in release
assert "os.execv(exe, [exe])" in client

restart_block = client.split('    def _restart_after_update(self) -> None:', 1)[1].split('    # ---------- Queue / video ----------', 1)[0]
assert 'os.execv(exe, [exe])' in restart_block
assert 'self.tray.stop()' not in restart_block
assert 'self.disconnect_remote()' not in restart_block

assert not (root / 'scripts/setup-stable-signing-linux.sh').exists()
assert (root / 'mac_local_signing.py').exists()
assert (root / 'scripts/sign-macos-transport.sh').exists()
assert 'Require stable macOS signing identity' not in release
assert 'MACOS_CERTIFICATE_P12_BASE64' not in release
assert '--self-test-local-signing-identity' in release
assert 'bootstrap_installed_app_identity' in server

assert 'Auto reconnect' in client
assert 'Video quality' in client
assert 'Double-click this bar or press Alt+Esc to exit' in client
assert 'def _schedule_auto_reconnect' in client
assert 'def _fullscreen_bar_pressed' in client
assert 'and not self.fullscreen_active' in client
print('native UI static tests: OK')
