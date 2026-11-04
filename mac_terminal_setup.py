from __future__ import annotations

import getpass
import subprocess
import sys

from mac_admin_setup import admin_component_state, current_short_user, install_unattended_access, ssh_host_fingerprints
from settings import load_server_password_verifier, save_server_password, update_server_settings

MIN_SESSION_PASSWORD = 12


def _prompt_auto_update_mode() -> str:
    print()
    print('Automatic ZorinMacBridge updates on this Mac')
    print('  1) Off                 — never check in the background')
    print('  2) Notify only         — check every 6 hours; install only from Zorin')
    print('  3) Install automatically — check every 6 hours and install verified official releases')
    while True:
        answer = input('Choose [1/2/3, default 3]: ').strip() or '3'
        mode = {'1': 'off', '2': 'notify', '3': 'install'}.get(answer)
        if mode:
            proc = subprocess.run(
                ['/usr/bin/sudo', '-n', '/usr/local/sbin/zmb-admin', 'set-auto-update', mode],
                text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False, timeout=15,
            )
            if proc.returncode != 0:
                raise RuntimeError((proc.stderr or proc.stdout or 'Could not save automatic update mode').strip())
            print(f'Automatic update mode saved: {mode}')
            return mode
        print('Enter 1, 2 or 3.')


def _prompt_session_password() -> None:
    print()
    print('ZorinMacBridge session password')
    print('  This is an APP-SPECIFIC password used by the Zorin client to connect to the desktop.')
    print('  It is NOT your macOS account/login password and should be different from it.')
    print(f'  Minimum length: {MIN_SESSION_PASSWORD} characters.')
    while True:
        first = getpass.getpass('Create ZorinMacBridge session password: ')
        if len(first) < MIN_SESSION_PASSWORD:
            print(f'Password is too short; use at least {MIN_SESSION_PASSWORD} characters.', file=sys.stderr)
            continue
        second = getpass.getpass('Repeat ZorinMacBridge session password: ')
        if first != second:
            print('Passwords do not match. Try again.', file=sys.stderr)
            continue
        save_server_password(first)
        print('Session password verifier saved. The plaintext password was not stored on this Mac.')
        return


def run_terminal_setup() -> int:
    user = current_short_user()
    print()
    print('=== ZorinMacBridge unattended setup ===')
    print(f'macOS short username: {user}')
    print()
    print('Two different credentials are used:')
    print('  1) macOS account password: your normal Mac login/unlock password.')
    print('     It is used only during first SSH pairing from Zorin and is not saved there.')
    print('  2) ZorinMacBridge session password: a separate password for the remote desktop session.')
    print('     You will create it below; only a salted verifier is stored on the Mac.')
    print()
    print('Enabling/repairing P0 and macOS Remote Login (SSH)…')
    output = install_unattended_access()
    if output:
        print(output)

    state = admin_component_state()
    if state.get('remote_login') != 'on':
        print('', file=sys.stderr)
        print('ERROR: macOS Remote Login (SSH) is not confirmed as enabled.', file=sys.stderr)
        print('Open System Settings > General > Sharing > Remote Login, enable it for this user,', file=sys.stderr)
        print('then run the installer again.', file=sys.stderr)
        return 2

    # The user-session app must launch and start its desktop server after login.
    update_server_settings(launch_at_login=True, auto_start_server=True)
    auto_update_mode = _prompt_auto_update_mode()

    existing = load_server_password_verifier()
    if existing is None:
        _prompt_session_password()
    else:
        answer = input('A ZorinMacBridge session password is already configured. Replace it? [y/N]: ').strip().lower()
        if answer in {'y', 'yes'}:
            _prompt_session_password()
        else:
            print('Keeping the existing ZorinMacBridge session password verifier.')

    print()
    print('SSH host fingerprint(s) — compare one of these when pairing from Zorin:')
    fps = ssh_host_fingerprints()
    if not fps:
        print('  WARNING: no SSH host fingerprints were found.', file=sys.stderr)
    else:
        for item in fps:
            print('  ' + item)

    print()
    print('SETUP_OK=1')
    print(f'MACOS_SHORT_USER={user}')
    print('REMOTE_LOGIN=on')
    print(f'AUTO_UPDATE_MODE={auto_update_mode}')
    print('Next on Zorin: select this Mac -> System -> Pair / Configure.')
    print('For "macOS account password", enter the same password you use to log in/unlock this Mac.')
    print('For the normal Desktop connection, use the separate ZorinMacBridge session password you created above.')
    return 0
