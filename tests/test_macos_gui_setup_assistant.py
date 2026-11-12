from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
gui = (ROOT / 'mac_server_gui.py').read_text(encoding='utf-8')
installer = (ROOT / 'install-macos.sh').read_text(encoding='utf-8')
admin = (ROOT / 'mac_admin_setup.py').read_text(encoding='utf-8')
uninstaller = (ROOT / 'native/macos/admin/uninstall-zmb.sh').read_text(encoding='utf-8')
readme = (ROOT / 'README.md').read_text(encoding='utf-8')

# Main Mac UI is organized rather than one long panel.
assert 'ttk.Notebook(outer)' in gui
for tab in ('Overview', 'Connection', 'Setup', 'Recovery', 'Logs', 'Maintenance'):
    assert f"text='{tab}'" in gui
assert "self.root.geometry('920x680')" in gui
assert "self.root.minsize(820, 600)" in gui

# Guided setup uses existing application actions, not a new backend.
assert 'class _SetupAssistant' in gui
assert "('Session password', self._password)" in gui
assert "('macOS permissions', self._permissions)" in gui
assert "('System services', self._system_services)" in gui
assert "('Startup', self._startup)" in gui
assert "('Finish', self._finish)" in gui
assert 'self.gui.enable_unattended_access' in gui
assert 'self.gui.request_screen_recording_access' in gui
assert 'self.gui.request_input_access' in gui
assert "if '--setup-assistant' in sys.argv" in gui

# Installer hands first-time setup to the GUI; terminal setup remains compatibility-only.
assert '/usr/bin/open "$DEST" --args --setup-assistant' in installer
assert '"$DEST/Contents/MacOS/ZorinMacBridge Server" --terminal-setup' not in installer
assert "if '--terminal-setup' in sys.argv" in gui

# Maintenance includes a ZMB-only clean uninstall.
assert 'Uninstall ZorinMacBridge…' in gui
assert 'clean_uninstall_zmb' in gui
assert "def clean_uninstall_zmb(*, keep_user_config: bool = False)" in admin
assert 'full-clean|keep-config' in uninstaller
assert 'Remote Login was left unchanged.' in uninstaller
assert 'shared Karabiner DriverKit VirtualHIDDevice package was left unchanged.' in uninstaller
assert "ZorinMacBridge-Admin:" in uninstaller
assert '/bin/rm -rf "$APP"' in uninstaller
assert 'Library/Application Support/ZorinMacBridge' in uninstaller
assert '.zorin-mac-bridge' in uninstaller

# README must not contain the removed accidental note marker.
assert '**User-requested note:**' not in readme

print('macOS GUI/setup-assistant/uninstall regression tests: OK')
