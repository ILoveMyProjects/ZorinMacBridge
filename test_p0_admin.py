from __future__ import annotations

import importlib.util
import os
from pathlib import Path
import plistlib
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parent

spec = importlib.util.spec_from_file_location('zmb_admin_backend_test', ROOT / 'zmb_admin_backend.py')
backend = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(backend)


def main() -> None:
    line = backend._restricted_authorized_key('ssh-ed25519 AAAATEST comment', 'dev-12345678')
    assert line.startswith('restrict,command="/usr/local/libexec/zmb-admin-ssh" ssh-ed25519 AAAATEST ')
    assert line.endswith('ZorinMacBridge-Admin:dev-12345678')

    try:
        backend._restricted_authorized_key('ssh-rsa AAAATEST comment', 'dev-12345678')
    except backend.AdminError:
        pass
    else:
        raise AssertionError('non-Ed25519 admin key must be rejected')


    # Hard subprocess timeout must kill the whole helper process group quickly.
    started = time.monotonic()
    try:
        backend._run([sys.executable, '-c', 'import time; time.sleep(30)'], timeout=1)
    except backend.AdminError as exc:
        assert 'Timed out after 1s' in str(exc)
    else:
        raise AssertionError('hung helper process was not timed out')
    assert time.monotonic() - started < 4.0

    assert backend._remote_command('check-update') == 'zmb-admin check-update'
    assert backend._remote_command('update-now') == 'zmb-admin update-now'
    assert backend._remote_command('set-auto-update', 'install') == 'zmb-admin set-auto-update install'
    try:
        backend._remote_command('set-auto-update', 'whatever')
    except backend.AdminError:
        pass
    else:
        raise AssertionError('invalid auto-update mode must be rejected')

    ready = backend.parse_status('protocol=2\nadmin_daemon_loaded=1\nadmin_heartbeat_age_seconds=4\nuser_session_available=1\nzmb_app_running=1\nzmb_port_listening=1\n')
    assert backend.status_summary(ready).startswith('READY')
    blocked, notes = backend.reboot_readiness({'filevault': 'on', 'filevault_ssh_unlock_eligible': '0'})
    assert blocked is False and notes

    gate = ROOT / 'native/macos/admin/zmb-admin-ssh'
    update_plist_path = ROOT / 'native/macos/admin/com.ilovemyprojects.zorinmacbridge.update.plist'
    proc = subprocess.run(
        ['/bin/bash', str(gate)],
        env={**os.environ, 'SSH_ORIGINAL_COMMAND': 'uname -a'},
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    assert proc.returncode == 126
    assert 'command denied' in proc.stderr

    with (ROOT / 'native/macos/admin/com.ilovemyprojects.zorinmacbridge.admin.plist').open('rb') as fh:
        plist = plistlib.load(fh)
    assert plist['Label'] == 'com.ilovemyprojects.zorinmacbridge.admin'
    assert plist['ProgramArguments'] == ['/usr/local/libexec/zmb-system-daemon']
    assert 'Sockets' not in plist

    with update_plist_path.open('rb') as fh:
        update_plist = plistlib.load(fh)
    assert update_plist['Label'] == 'com.ilovemyprojects.zorinmacbridge.update'
    assert update_plist['ProgramArguments'] == ['/usr/local/sbin/zmb-admin', 'auto-update-run']
    assert update_plist['StartInterval'] == 21600
    assert 'Sockets' not in update_plist

    gtk = (ROOT / 'linux_client_gtk.py').read_text(encoding='utf-8')
    system_page = (ROOT / 'zmb_system_page.py').read_text(encoding='utf-8')
    mac_gui = (ROOT / 'mac_server_gui.py').read_text(encoding='utf-8')
    release = (ROOT / '.github/workflows/release.yml').read_text(encoding='utf-8')
    installer = (ROOT / 'native/macos/admin/install-admin.sh').read_text(encoding='utf-8')
    update_gateway = gate.read_text(encoding='utf-8')
    updates_py = (ROOT / 'updates.py').read_text(encoding='utf-8')

    assert 'self.system_controller = SystemController(self)' in gtk
    assert "self.stack.add_titled(self.system_page, 'system', 'System')" in gtk
    assert '_merge_paired_admin_servers' in gtk and 'paired/offline' in (ROOT / 'discovery.py').read_text(encoding='utf-8')
    assert 'scanned_host_keys=[result["preferred"]["line"]]' in system_page
    assert 'host_entry.set_editable(False)' in system_page
    assert '_begin_operation' in system_page and '_operation_watchdog' in system_page
    assert 'timeout_ms=12000' in system_page and 'timeout_ms=34000' in system_page
    assert 'start_new_session=True' in (ROOT / 'zmb_admin_backend.py').read_text(encoding='utf-8')
    assert 'os.killpg(proc.pid, signal.SIGKILL)' in (ROOT / 'zmb_admin_backend.py').read_text(encoding='utf-8')
    assert 'BatchMode=yes' in (ROOT / 'zmb_admin_backend.py').read_text(encoding='utf-8')
    assert '_read_local_clipboard_async' in gtk
    assert 'Enable / Repair' in mac_gui and 'install_unattended_access()' in mac_gui
    assert "--add-data 'native/macos/admin:native/macos/admin'" in release
    assert '--hidden-import mac_terminal_setup' in release
    assert 'zmb_admin_backend.py zmb_system_page.py' in release
    assert 'openssh-client' in release
    assert 'systemsetup -setremotelogin on' in installer
    assert 'com.ilovemyprojects.zorinmacbridge.update.plist' in installer
    assert 'AUTO_UPDATE=$OLD_AUTO_UPDATE' in installer
    assert '/usr/local/sbin/zmb-admin update-now' in installer
    assert '/usr/local/sbin/zmb-admin set-auto-update install' in installer
    assert 'zmb-admin check-update' in update_gateway
    assert 'zmb-admin update-now' in update_gateway
    assert 'zmb-admin set-auto-update install' in update_gateway
    assert 'prepare_macos_update_for_root' in updates_py
    assert '--p0-update-stage' in mac_gui
    assert 'Check update' in system_page and 'Update now' in system_page
    assert 'Install automatically' in system_page
    assert "'/usr/bin/nc'" in (ROOT / 'mac_admin_setup.py').read_text(encoding='utf-8')
    assert '/usr/local/libexec/zmb-admin-ssh' in installer
    assert 'macos-intel' not in release
    assert 'macos-15-intel' not in release
    assert 'ZorinMacBridge-Server_macOS-x86_64.dmg' not in release
    assert 'needs: [linux-amd64, macos-arm64]' in release
    assert 'Apple Silicon (arm64) Macs only' in installer

    discovery = (ROOT / 'discovery.py').read_text(encoding='utf-8')
    daemon = (ROOT / 'native/macos/admin/zmb-system-daemon').read_text(encoding='utf-8')
    mac_admin_setup = (ROOT / 'mac_admin_setup.py').read_text(encoding='utf-8')
    assert '_zorinmacbridge-admin._tcp.local.' in discovery
    assert 'server_id' in discovery and 'hostname' in discovery
    assert '/usr/bin/dns-sd -R' in daemon
    assert '_zorinmacbridge-admin._tcp' in daemon
    assert 'persistent_server_id()' in mac_admin_setup
    assert 'SERVER_ID=$SERVER_ID' in installer
    assert 'SERVER_NAME=$SERVER_NAME' in installer
    assert 'deadline = time.monotonic() + 0.008' in gtk
    assert 'self._credential_serial' in gtk and 'credential_worker' in gtk
    assert 'self.log_buffer.get_char_count() > 1_000_000' in gtk
    assert 'self.log_buffer.delete_mark(mark)' in gtk
    assert 'def _queue_video_image(self, image)' in gtk
    assert 'index + 80' in gtk
    assert 'Bonjour scan watchdog expired after 7 seconds' in gtk

    # Audit regression guards: discovered/fixed freeze, race and root-update issues.
    legacy_client = (ROOT / 'linux_client.py').read_text(encoding='utf-8')
    mac_server = (ROOT / 'mac_server.py').read_text(encoding='utf-8')
    root_admin = (ROOT / 'native/macos/admin/zmb-admin').read_text(encoding='utf-8')
    uninstaller = (ROOT / 'native/macos/admin/uninstall-admin.sh').read_text(encoding='utf-8')
    local_signing = (ROOT / 'mac_local_signing.py').read_text(encoding='utf-8')

    assert 'names_lock = threading.Lock()' in discovery and 'names_snapshot = sorted(names)' in discovery
    assert 'scan_host = host_entry.get_text().strip()' in system_page
    assert 'self._operation_serial += 1' in system_page
    assert 'def _clear_session_queues' in legacy_client
    assert 'self._connect_snapshot = (ip, port, _password, expected_fp)' in legacy_client
    assert 'self._pending_mouse_move' in legacy_client and 'packet[0] == MOUSE_MOVE' in legacy_client
    assert 'threading.BoundedSemaphore(16)' in mac_server
    settings_py = (ROOT / 'settings.py').read_text(encoding='utf-8')
    assert '50_000 <= iterations <= 2_000_000' in settings_py
    assert "('credential', credential_token, selected_id, saved_password)" in legacy_client
    assert 'processed < 120' in legacy_client and 'time.monotonic() - started' in legacy_client
    assert 'select.select([fd]' in mac_server and 'native streamer pipe stalled' in mac_server
    assert 'Thread(target=worker, daemon=True).start()' in mac_gui
    assert 'Could not save logs' in gtk

    assert 'LOCAL_IDENTITY_TOKEN=' in installer
    assert '/var/tmp/zmb-systemsetup.XXXXXX' in installer
    assert '/tmp/zmb-systemsetup.out' not in installer
    assert 'run_app_for_root_update' in root_admin
    assert '/usr/sbin/chown -R "$user":staff "$work"' not in root_admin
    assert 'ZMB_LOCAL_IDENTITY_TOKEN="$token"' in root_admin
    assert '/usr/bin/sudo -u "$user" /bin/sh -c' in root_admin
    assert '.zmb-authorized_keys.XXXXXX' in root_admin
    assert '/var/tmp/zmb-launchagent.XXXXXX' in root_admin
    assert '/usr/local/sbin/zmb-admin repair-launch-agent' in installer
    assert '/usr/bin/install -m 0600 "$PLIST_TMP" "$TARGET_HOME/Library/LaunchAgents' not in installer
    assert '/usr/bin/sudo -u "$TARGET_USER" /bin/sh -c' in installer
    assert '.zmb-uninstall-keys.XXXXXX' in uninstaller
    assert '/usr/bin/sudo -u "$TARGET_USER" /bin/sh -c' in uninstaller
    assert '$TARGET_HOME/.ssh/authorized_keys' not in uninstaller
    assert "os.environ.get('ZMB_LOCAL_IDENTITY_TOKEN'" in local_signing
    assert 'CFBundleShortVersionString' in release and 'CFBundleVersion' in release
    assert 'expected_version=info.latest' in updates_py
    assert 'hard transfer deadline' in updates_py
    assert 'asset.size is not None and received != asset.size' in updates_py

    print('P0 admin/source integration tests: OK')


if __name__ == '__main__':
    main()

# Terminal installer must configure SSH/P0 and clearly separate both password types.
terminal_setup = (ROOT / "mac_terminal_setup.py").read_text(encoding="utf-8")
install_macos = (ROOT / "install-macos.sh").read_text(encoding="utf-8")
mac_gui = (ROOT / "mac_server_gui.py").read_text(encoding="utf-8")
linux_gui = (ROOT / "linux_client_gtk.py").read_text(encoding="utf-8")
system_page = (ROOT / "zmb_system_page.py").read_text(encoding="utf-8")
admin_installer = (ROOT / "native/macos/admin/install-admin.sh").read_text(encoding="utf-8")
assert "--terminal-setup" in mac_gui
assert "--terminal-setup" in install_macos
assert "systemsetup -setremotelogin on" in admin_installer
assert "exit 78" in admin_installer
assert "ZorinMacBridge session password" in terminal_setup
assert "macOS account password" in terminal_setup
assert "Install automatically" in terminal_setup
assert "set-auto-update" in terminal_setup
assert "ZMB session password" in linux_gui
assert "macOS account password (login/unlock)" in system_page
