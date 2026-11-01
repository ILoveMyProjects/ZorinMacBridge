from __future__ import annotations

import importlib.util
import os
from pathlib import Path
import plistlib
import subprocess

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

    ready = backend.parse_status('protocol=2\nadmin_daemon_loaded=1\nadmin_heartbeat_age_seconds=4\nuser_session_available=1\nzmb_app_running=1\nzmb_port_listening=1\n')
    assert backend.status_summary(ready).startswith('READY')
    blocked, notes = backend.reboot_readiness({'filevault': 'on', 'filevault_ssh_unlock_eligible': '0'})
    assert blocked is False and notes

    gate = ROOT / 'native/macos/admin/zmb-admin-ssh'
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

    gtk = (ROOT / 'linux_client_gtk.py').read_text(encoding='utf-8')
    system_page = (ROOT / 'zmb_system_page.py').read_text(encoding='utf-8')
    mac_gui = (ROOT / 'mac_server_gui.py').read_text(encoding='utf-8')
    release = (ROOT / '.github/workflows/release.yml').read_text(encoding='utf-8')
    installer = (ROOT / 'native/macos/admin/install-admin.sh').read_text(encoding='utf-8')

    assert 'self.system_controller = SystemController(self)' in gtk
    assert "self.stack.add_titled(self.system_page, 'system', 'System')" in gtk
    assert '_merge_paired_admin_servers' in gtk and 'paired/offline' in (ROOT / 'discovery.py').read_text(encoding='utf-8')
    assert 'scanned_host_keys=[result["preferred"]["line"]]' in system_page
    assert 'host_entry.set_editable(False)' in system_page
    assert 'Enable / Repair' in mac_gui and 'install_unattended_access()' in mac_gui
    assert "--add-data 'native/macos/admin:native/macos/admin'" in release
    assert 'zmb_admin_backend.py zmb_system_page.py' in release
    assert 'openssh-client' in release
    assert 'systemsetup -setremotelogin on' in installer
    assert '/usr/local/libexec/zmb-admin-ssh' in installer

    print('P0 admin/source integration tests: OK')


if __name__ == '__main__':
    main()
