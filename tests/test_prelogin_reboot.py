from pathlib import Path
import plistlib
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

server = (ROOT / 'mac_server.py').read_text(encoding='utf-8')
gui = (ROOT / 'mac_server_gui.py').read_text(encoding='utf-8')
settings = (ROOT / 'settings.py').read_text(encoding='utf-8')
client = (ROOT / 'linux_client.py').read_text(encoding='utf-8')
gtk = (ROOT / 'linux_client_gtk.py').read_text(encoding='utf-8')
install = (ROOT / 'native/macos/admin/install-admin.sh').read_text(encoding='utf-8')
uninstall = (ROOT / 'native/macos/admin/uninstall-admin.sh').read_text(encoding='utf-8')
admin = (ROOT / 'native/macos/admin/zmb-admin').read_text(encoding='utf-8')
build = (ROOT / 'scripts/build-macos-streamer.sh').read_text(encoding='utf-8')
workflow = (ROOT / '.github/workflows/release.yml').read_text(encoding='utf-8')
gate = (ROOT / 'scripts/verify-macos-native-gate.sh').read_text(encoding='utf-8')
native_c = (ROOT / 'native/macos/ZMBLoginInput.c').read_text(encoding='utf-8')
corehid = (ROOT / 'native/macos/ZMBCoreHIDInput.swift').read_text(encoding='utf-8')

plist_path = ROOT / 'native/macos/admin/com.ilovemyprojects.zorinmacbridge.prelogin.plist'
plist = plistlib.loads(plist_path.read_bytes())
assert plist['Label'] == 'com.ilovemyprojects.zorinmacbridge.prelogin'
assert plist['LimitLoadToSessionType'] == 'LoginWindow'
assert plist['ProgramArguments'][-1] == '--prelogin-server'
assert plist['RunAtLoad'] is True
assert plist['KeepAlive'] is True
assert '/Applications/ZorinMacBridge Server.app/Contents/MacOS/ZorinMacBridge Server' in plist['ProgramArguments']

# Root LoginWindow helper reuses the paired user's identity instead of creating a
# second certificate/password ceremony after reboot.
assert 'def configure_server_home' in settings
assert 'def configure_runtime_home' in server
assert "if '--prelogin-server' in sys.argv:" in gui
assert 'configure_server_home(home)' in gui
assert 'configure_runtime_home(home)' in gui
assert 'load_server_password_verifier()' in gui
assert "saved.get('server_id') or _prelogin_config_value('SERVER_ID')" in gui
assert "Existing ZorinMacBridge TLS identity is unavailable for pre-login reuse" in gui
assert "Bonjour advertisement failed" in gui
assert 'prelogin=True' in gui

# Pre-login transport is deliberately limited to the physical login screen and
# control/video. Files and virtual 2K/4K are restored only after Aqua handoff.
assert "if prelogin and role == 'file':" in server
assert "'prelogin': bool(prelogin)" in server
assert "pre-login mode: physical macOS LoginWindow display prepared" in server
assert 'display_id = 0' in server
assert 'max_width = min(max_width, 1920)' in server
assert 'fps = min(fps, 30.0)' in server
assert 'LoginWindowInput' in server

# Production LoginWindow input cannot block control startup on CoreGraphics
# delivery observation. It selects by exact open and fails over on real events.
assert 'HIDVirtualDevice(properties:' in corehid
assert 'zmb_corehid_input_key' in corehid
assert 'zmb_corehid_input_pointer' in corehid
assert '-framework CoreHID' in build
assert 'ZMBCoreHIDInput.swift' in build
assert 'ZMBLoginInput.c' in build
assert 'ZMBLoginInput.o' in build
assert '-framework IOKit' in build
assert 'zmb_login_input_open_backend' in server
assert '_activate_from_index' in server
assert '_dispatch_with_failover' in server
assert 'backend opened; delivery will be verified by real user input' in server
assert "('corehid', 0)" in server
assert "('userdevice', 1)" in server
assert "('iohidsystem', 2)" in server
assert "('quartz', 3)" in server
assert 'IOHIDPostEvent' in native_c
assert 'scripts/build-macos-streamer.sh' in gate
assert 'scripts/verify-macos-native-gate.sh' in workflow

# Installer/recovery own the /Library/LaunchAgents helper; uninstall removes it.
assert '/Library/LaunchAgents/${PRELOGIN_LABEL}.plist' in install
assert 'com.ilovemyprojects.zorinmacbridge.prelogin.plist' in install
assert '/Library/LaunchAgents/com.ilovemyprojects.zorinmacbridge.prelogin.plist' in uninstall
assert 'repair_prelogin_agent()' in admin
assert 'PRELOGIN_AGENT_MISSING' in admin
assert 'recover_prelogin_cmd()' in admin
assert 'recover-prelogin) recover_prelogin_cmd' in admin
assert 'prelogin_ready=%s' in admin
assert 'prelogin_release_identity_ready=%s' in admin
assert 'prelogin_process_running()' in admin
assert 'gui/0/${PRELOGIN_LABEL}' not in admin
assert 'launchctl bootstrap loginwindow' in admin
assert 'launchctl load -w -S LoginWindow' in admin

# Linux client recognises loginwindow state and does not try the file role until
# the normal Aqua server reconnects.
assert "self.ui_queue.put(('prelogin_state', self.prelogin_session))" in client
assert 'if not self.connected or self.prelogin_session:' in client
assert "Connected — macOS login window" in gtk
assert 'self.connected and not self.prelogin_session' in gtk
assert 'if not self.prelogin_session:' in gtk
assert 'Desktop Connect: System/P0 is online while Desktop is absent; using session recovery instead of Wake-on-LAN.' in gtk
assert "action = 'recover-prelogin' if no_gui else 'recover-desktop'" in gtk
assert 'self.selected_system_available = bool(server.admin_available and not server.paired_only)' in gtk

# This feature must not weaken login/FileVault policy.
affected = '\n'.join([gui, server, install, admin, native_c, corehid])
assert 'autoLoginUser' not in affected
assert 'DisableFDEAutoLogin' not in affected
assert 'fdesetup disable' not in affected

print('pre-login reboot recovery regression tests: OK')
