from __future__ import annotations

import os
import plistlib
import pwd
import re
import shlex
import socket
import subprocess
import time
from pathlib import Path

from resources import resource_path
from settings import persistent_server_id

ADMIN_LABEL = 'com.ilovemyprojects.zorinmacbridge.admin'
ADMIN_PLIST = Path('/Library/LaunchDaemons') / f'{ADMIN_LABEL}.plist'
UPDATE_LABEL = 'com.ilovemyprojects.zorinmacbridge.update'
UPDATE_PLIST = Path('/Library/LaunchDaemons') / f'{UPDATE_LABEL}.plist'
ADMIN_HELPER = Path('/usr/local/sbin/zmb-admin')
ADMIN_GATEWAY = Path('/usr/local/libexec/zmb-admin-ssh')
ADMIN_DAEMON = Path('/usr/local/libexec/zmb-system-daemon')
PRELOGIN_LABEL = 'com.ilovemyprojects.zorinmacbridge.prelogin'
PRELOGIN_PLIST = Path('/Library/LaunchAgents') / f'{PRELOGIN_LABEL}.plist'
APP_PATH = Path('/Applications/ZorinMacBridge Server.app')
APP_BIN = APP_PATH / 'Contents/MacOS/ZorinMacBridge Server'
CAPABILITY_STATUS = Path('/Library/Application Support/ZorinMacBridge/Admin/prelogin-capability-status')
VIRTUAL_HID_LABEL = 'com.ilovemyprojects.zorinmacbridge.virtualhid'
VIRTUAL_HID_MANAGER = Path('/Applications/.Karabiner-VirtualHIDDevice-Manager.app/Contents/MacOS/Karabiner-VirtualHIDDevice-Manager')
VIRTUAL_HID_DAEMON = Path('/Library/Application Support/org.pqrs/Karabiner-DriverKit-VirtualHIDDevice/Applications/Karabiner-VirtualHIDDevice-Daemon.app/Contents/MacOS/Karabiner-VirtualHIDDevice-Daemon')
VIRTUAL_HID_EXTENSION_ID = 'org.pqrs.Karabiner-DriverKit-VirtualHIDDevice'


def current_short_user() -> str:
    return pwd.getpwuid(os.getuid()).pw_name


def _run(argv: list[str], timeout: int = 30, *, cwd: str | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        argv,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        timeout=timeout,
        check=False,
        cwd=cwd,
    )


def _privileged_shell(command: str, *, timeout: int = 120) -> str:
    applescript = 'on run argv\n do shell script (item 1 of argv) with administrator privileges\nend run'
    # Run privileged AppleScript from a stable directory. The app/installer may
    # have been launched from a project directory that macOS privacy controls
    # later deny to the elevated shell, which otherwise produces getcwd errors.
    proc = _run(['/usr/bin/osascript', '-e', applescript, '--', f'cd / && {command}'], timeout=timeout, cwd='/')
    if proc.returncode != 0:
        raise RuntimeError((proc.stderr or proc.stdout or 'Administrator command failed').strip())
    return (proc.stdout or '').strip()


def _resource_script(name: str) -> Path:
    path = resource_path(f'native/macos/admin/{name}')
    if not path.is_file():
        raise RuntimeError(f'Bundled administration resource is missing: {name}')
    return path


def install_unattended_access() -> str:
    user = current_short_user()
    server_id = persistent_server_id()
    host_name = socket.gethostname().split('.', 1)[0] or 'Mac'
    from mac_local_signing import ensure_local_identity
    identity = ensure_local_identity()
    script = _resource_script('install-admin.sh')
    command = (
        f'/bin/bash {shlex.quote(str(script))} {shlex.quote(user)} '
        f'{shlex.quote(server_id)} {shlex.quote(host_name)} {shlex.quote(identity.token)}'
    )
    return _privileged_shell(command, timeout=180)


def prepare_virtual_hid() -> str:
    """Install the pinned Virtual HID package/daemon without waiting for UI approval."""
    script = _resource_script('install-virtual-hid.sh')
    command = f'/bin/bash {shlex.quote(str(script))} --prepare'
    return _privileged_shell(command, timeout=90)


def finalize_virtual_hid() -> str:
    """Finish daemon startup + real keyboard/mouse self-test after extension approval."""
    script = _resource_script('install-virtual-hid.sh')
    command = f'/bin/bash {shlex.quote(str(script))} --finalize'
    return _privileged_shell(command, timeout=90)


def virtual_hid_state() -> dict[str, str | bool]:
    """Read only the fast Virtual HID state used by Setup Assistant polling."""
    virtual_hid_launch = _run(['/bin/launchctl', 'print', f'system/{VIRTUAL_HID_LABEL}'], timeout=5)
    pqrs_virtual_hid_launch = _run(
        ['/bin/launchctl', 'print', 'system/org.pqrs.service.daemon.Karabiner-VirtualHIDDevice-Daemon'],
        timeout=5,
    )
    sysext = _run(['/usr/bin/systemextensionsctl', 'list'], timeout=8)
    sysext_text = (sysext.stdout or '') + '\n' + (sysext.stderr or '')
    extension_line = ''
    for raw in sysext_text.splitlines():
        if VIRTUAL_HID_EXTENSION_ID in raw:
            extension_line = raw.strip()
    installed = VIRTUAL_HID_MANAGER.is_file() and VIRTUAL_HID_DAEMON.is_file()
    daemon_loaded = virtual_hid_launch.returncode == 0 or pqrs_virtual_hid_launch.returncode == 0
    extension_ready = bool(extension_line and 'activated enabled' in extension_line.lower())
    return {
        'virtual_hid_installed': installed,
        'virtual_hid_daemon_loaded': daemon_loaded,
        'virtual_hid_extension_ready': extension_ready,
        'virtual_hid_extension_state': extension_line,
    }


def request_virtual_hid_activation(*, force: bool = False) -> int:
    """Submit DriverKit activation from the logged-in Aqua user, never from root."""
    if not VIRTUAL_HID_MANAGER.is_file():
        raise RuntimeError('Virtual HID manager is not installed yet.')
    action = 'forceActivate' if force else 'activate'
    proc = subprocess.Popen(
        [str(VIRTUAL_HID_MANAGER), action],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        cwd='/',
        start_new_session=True,
    )
    return int(proc.pid)


def uninstall_unattended_access() -> str:
    user = current_short_user()
    script = _resource_script('uninstall-admin.sh')
    command = f'/bin/bash {shlex.quote(str(script))} {shlex.quote(user)}'
    return _privileged_shell(command)


def clean_uninstall_zmb(*, keep_user_config: bool = False) -> str:
    """Remove ZMB-owned macOS components while preserving unrelated SSH/system state."""
    user = current_short_user()
    script = _resource_script('uninstall-zmb.sh')
    mode = 'keep-config' if keep_user_config else 'full-clean'
    command = (
        f'/bin/bash {shlex.quote(str(script))} {shlex.quote(user)} {shlex.quote(mode)}'
    )
    return _privileged_shell(command, timeout=180)


def _read_key_value_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    try:
        text = path.read_text(encoding='utf-8', errors='replace')
    except OSError:
        return values
    for raw in text.splitlines():
        if '=' not in raw:
            continue
        key, value = raw.split('=', 1)
        key = key.strip()
        if key:
            values[key] = value.strip()
    return values


def _installed_app_identity() -> tuple[str, str]:
    version = ''
    cdhash = ''
    try:
        with (APP_PATH / 'Contents/Info.plist').open('rb') as handle:
            info = plistlib.load(handle)
        version = str(info.get('CFBundleShortVersionString') or '').strip()
    except Exception:
        pass
    if APP_PATH.is_dir():
        proc = _run(['/usr/bin/codesign', '-dvvv', str(APP_PATH)], timeout=10)
        details = (proc.stdout or '') + '\n' + (proc.stderr or '')
        match = re.search(r'(?m)^CDHash=([0-9A-Fa-f]+)\s*$', details)
        if match:
            cdhash = match.group(1).lower()
    return version, cdhash


def _prelogin_agent_contract_ready() -> bool:
    try:
        with PRELOGIN_PLIST.open('rb') as handle:
            data = plistlib.load(handle)
    except Exception:
        return False
    session_type = data.get('LimitLoadToSessionType')
    if isinstance(session_type, str):
        loginwindow = session_type == 'LoginWindow'
    elif isinstance(session_type, list):
        loginwindow = 'LoginWindow' in session_type
    else:
        loginwindow = False
    argv = data.get('ProgramArguments')
    return bool(
        data.get('Label') == PRELOGIN_LABEL
        and loginwindow
        and data.get('RunAtLoad') is True
        and data.get('KeepAlive') is True
        and data.get('ProcessType') == 'Interactive'
        and isinstance(argv, list)
        and len(argv) >= 2
        and argv[0] == str(APP_BIN)
        and '--prelogin-server' in argv[1:]
        and APP_BIN.is_file()
    )


def _release_signing_mode() -> str:
    if not APP_PATH.is_dir():
        return 'missing'
    try:
        from mac_release_signing import app_has_release_identity
        if app_has_release_identity(APP_PATH):
            return 'developer-id'
    except Exception:
        pass
    try:
        from mac_local_signing import app_has_local_identity
        if app_has_local_identity(APP_PATH):
            return 'local-adhoc'
    except Exception:
        pass
    return 'transport-adhoc'


def _prelogin_probe_state() -> dict[str, str | bool]:
    proof = _read_key_value_file(CAPABILITY_STATUS)
    version, cdhash = _installed_app_identity()
    current = bool(
        version and cdhash
        and proof.get('app_version') == version
        and proof.get('app_cdhash', '').lower() == cdhash.lower()
    )
    input_result = proof.get('input_result')
    if not input_result:
        # Backward compatibility with proof files created before the reboot
        # delivery matrix existed.
        input_result = 'PASS' if proof.get('input_corehid_result') == 'PASS' else 'FAIL'
    ready = bool(
        current
        and proof.get('overall') == 'PASS'
        and proof.get('capture_result') == 'PASS'
        and input_result == 'PASS'
    )
    return {
        'prelogin_probe_present': bool(proof),
        'prelogin_probe_current': current,
        'prelogin_probe_ready': ready,
        'prelogin_capture_proof_ready': bool(current and proof.get('capture_result') == 'PASS'),
        'prelogin_probe_overall': proof.get('overall', 'never'),
        'prelogin_probe_capture': proof.get('capture_result', 'unknown'),
        'prelogin_probe_input_result': input_result,
        'prelogin_probe_input_selected': proof.get('input_selected_backend', 'unknown'),
        'prelogin_probe_input_corehid': proof.get('input_corehid_result', 'unknown'),
        'prelogin_probe_input_userdevice': proof.get('input_userdevice_result', 'unknown'),
        'prelogin_probe_input_iohidsystem': proof.get('input_iohidsystem_result', 'unknown'),
        'prelogin_probe_input_quartz': proof.get('input_quartz_result', 'unknown'),
        'prelogin_probe_input_legacy': proof.get('input_legacy_result', 'unknown'),
    }


def admin_component_state() -> dict[str, str | bool]:
    files_ok = all(path.is_file() for path in (ADMIN_PLIST, UPDATE_PLIST, ADMIN_HELPER, ADMIN_GATEWAY, ADMIN_DAEMON))
    launch = _run(['/bin/launchctl', 'print', f'system/{ADMIN_LABEL}'], timeout=5)
    updater = _run(['/bin/launchctl', 'print', f'system/{UPDATE_LABEL}'], timeout=5)
    virtual_hid_launch = _run(['/bin/launchctl', 'print', f'system/{VIRTUAL_HID_LABEL}'], timeout=5)
    pqrs_virtual_hid_launch = _run(['/bin/launchctl', 'print', 'system/org.pqrs.service.daemon.Karabiner-VirtualHIDDevice-Daemon'], timeout=5)
    sysext = _run(['/usr/bin/systemextensionsctl', 'list'], timeout=8)
    sysext_text = (sysext.stdout or '') + '\n' + (sysext.stderr or '')
    virtual_hid_installed = VIRTUAL_HID_MANAGER.is_file() and VIRTUAL_HID_DAEMON.is_file()
    virtual_hid_daemon_loaded = virtual_hid_launch.returncode == 0 or pqrs_virtual_hid_launch.returncode == 0
    virtual_hid_extension_ready = bool(VIRTUAL_HID_EXTENSION_ID in sysext_text and 'activated enabled' in sysext_text.lower())
    virtual_hid_input_ready = bool(virtual_hid_installed and virtual_hid_daemon_loaded and virtual_hid_extension_ready)
    virtual_hid_input_detail = 'fast-state-only'
    if files_ok and ADMIN_HELPER.is_file():
        root_status = _run(['/usr/bin/sudo', '-n', str(ADMIN_HELPER), 'status'], timeout=15)
        if root_status.returncode == 0:
            root_values: dict[str, str] = {}
            for raw in (root_status.stdout or '').splitlines():
                key, sep, value = raw.partition('=')
                if sep and key:
                    root_values[key.strip()] = value.strip()
            if 'virtual_hid_input_ready' in root_values:
                virtual_hid_input_ready = root_values['virtual_hid_input_ready'] == '1'
                virtual_hid_input_detail = root_values.get('virtual_hid_input_detail', '') or 'root-selftest'
    # systemsetup itself requires administrator privileges even for queries on current macOS.
    # Try it first, then fall back to a localhost SSH reachability probe so the GUI status
    # does not prompt for an administrator password merely to refresh the page.
    remote = _run(['/usr/sbin/systemsetup', '-getremotelogin'], timeout=5)
    remote_text = (remote.stdout or remote.stderr or '').strip().lower()
    probe = _run(['/usr/bin/nc', '-G', '1', '-z', '127.0.0.1', '22'], timeout=3)
    ssh_port_ready = probe.returncode == 0
    if 'remote login: on' in remote_text:
        remote_login = 'on'
    elif 'remote login: off' in remote_text:
        remote_login = 'off'
    else:
        remote_login = 'on' if ssh_port_ready else 'unknown'
    probe = _prelogin_probe_state()
    signing_mode = _release_signing_mode()
    state: dict[str, str | bool] = {
        'installed': files_ok,
        'daemon_loaded': launch.returncode == 0,
        'updater_loaded': updater.returncode == 0,
        'remote_login': remote_login,
        'ssh_port_ready': ssh_port_ready,
        'ssh_host_key_ready': bool(ssh_host_fingerprints()),
        'prelogin_agent_exists': PRELOGIN_PLIST.is_file(),
        'prelogin_agent_contract_ready': _prelogin_agent_contract_ready(),
        'virtual_hid_installed': virtual_hid_installed,
        'virtual_hid_daemon_loaded': virtual_hid_daemon_loaded,
        'virtual_hid_extension_ready': virtual_hid_extension_ready,
        'virtual_hid_input_ready': virtual_hid_input_ready,
        'virtual_hid_input_detail': virtual_hid_input_detail,
        'release_signing_mode': signing_mode,
        # verify_release_app() only returns developer-id when the embedded
        # all-Macs provisioning profile and both managed pre-login entitlements
        # are present and match the signed application.
        'prelogin_release_identity_ready': signing_mode == 'developer-id',
    }
    state.update(probe)
    return state


def ssh_host_fingerprints() -> list[str]:
    result: list[str] = []
    for path in (
        Path('/etc/ssh/ssh_host_ed25519_key.pub'),
        Path('/etc/ssh/ssh_host_ecdsa_key.pub'),
        Path('/etc/ssh/ssh_host_rsa_key.pub'),
    ):
        if not path.is_file():
            continue
        proc = _run(['/usr/bin/ssh-keygen', '-lf', str(path), '-E', 'sha256'], timeout=5)
        if proc.returncode == 0 and proc.stdout.strip():
            result.append(proc.stdout.strip())
    return result
