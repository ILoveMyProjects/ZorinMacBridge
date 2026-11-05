from __future__ import annotations

import os
import pwd
import shlex
import socket
import subprocess
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
    return _privileged_shell(command)


def uninstall_unattended_access() -> str:
    user = current_short_user()
    script = _resource_script('uninstall-admin.sh')
    command = f'/bin/bash {shlex.quote(str(script))} {shlex.quote(user)}'
    return _privileged_shell(command)


def admin_component_state() -> dict[str, str | bool]:
    files_ok = all(path.is_file() for path in (ADMIN_PLIST, UPDATE_PLIST, ADMIN_HELPER, ADMIN_GATEWAY, ADMIN_DAEMON))
    launch = _run(['/bin/launchctl', 'print', f'system/{ADMIN_LABEL}'], timeout=5)
    updater = _run(['/bin/launchctl', 'print', f'system/{UPDATE_LABEL}'], timeout=5)
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
    return {
        'installed': files_ok,
        'daemon_loaded': launch.returncode == 0,
        'updater_loaded': updater.returncode == 0,
        'remote_login': remote_login,
        'ssh_port_ready': ssh_port_ready,
        'ssh_host_key_ready': bool(ssh_host_fingerprints()),
    }


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
