from __future__ import annotations

import os
import pwd
import shlex
import subprocess
from pathlib import Path

from resources import resource_path

ADMIN_LABEL = 'com.ilovemyprojects.zorinmacbridge.admin'
ADMIN_PLIST = Path('/Library/LaunchDaemons') / f'{ADMIN_LABEL}.plist'
ADMIN_HELPER = Path('/usr/local/sbin/zmb-admin')
ADMIN_GATEWAY = Path('/usr/local/libexec/zmb-admin-ssh')
ADMIN_DAEMON = Path('/usr/local/libexec/zmb-system-daemon')


def current_short_user() -> str:
    return pwd.getpwuid(os.getuid()).pw_name


def _run(argv: list[str], timeout: int = 30) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        argv,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        timeout=timeout,
        check=False,
    )


def _privileged_shell(command: str, *, timeout: int = 120) -> str:
    applescript = 'on run argv\n do shell script (item 1 of argv) with administrator privileges\nend run'
    proc = _run(['/usr/bin/osascript', '-e', applescript, '--', command], timeout=timeout)
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
    script = _resource_script('install-admin.sh')
    command = f'/bin/bash {shlex.quote(str(script))} {shlex.quote(user)}'
    return _privileged_shell(command)


def uninstall_unattended_access() -> str:
    user = current_short_user()
    script = _resource_script('uninstall-admin.sh')
    command = f'/bin/bash {shlex.quote(str(script))} {shlex.quote(user)}'
    return _privileged_shell(command)


def admin_component_state() -> dict[str, str | bool]:
    files_ok = all(path.is_file() for path in (ADMIN_PLIST, ADMIN_HELPER, ADMIN_GATEWAY, ADMIN_DAEMON))
    launch = _run(['/bin/launchctl', 'print', f'system/{ADMIN_LABEL}'], timeout=5)
    remote = _run(['/usr/sbin/systemsetup', '-getremotelogin'], timeout=5)
    remote_text = (remote.stdout or remote.stderr or '').strip().lower()
    if 'on' in remote_text:
        remote_login = 'on'
    elif 'off' in remote_text:
        remote_login = 'off'
    else:
        remote_login = 'unknown'
    return {
        'installed': files_ok,
        'daemon_loaded': launch.returncode == 0,
        'remote_login': remote_login,
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
