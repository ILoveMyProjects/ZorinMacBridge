from __future__ import annotations

import getpass
import subprocess
import sys

from mac_admin_setup import admin_component_state, current_short_user, install_unattended_access, ssh_host_fingerprints
from mac_requirements import evaluate_setup_requirements, missing_setup_requirements, prelogin_readiness_state
from mac_server import accessibility_permission_status, screen_capture_permission_status
from settings import load_server_password_verifier, save_server_password, update_server_settings

MIN_SESSION_PASSWORD = 12


def _required(message: str) -> None:
    prefix = '\033[1;31mREQUIRED\033[0m' if sys.stderr.isatty() else 'REQUIRED'
    print(f'{prefix}: {message}', file=sys.stderr)


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
    remote_login_ready = state.get('remote_login') == 'on'
    if not remote_login_ready:
        print()
        _required('macOS Remote Login (SSH) is OFF.')
        _required('Open System Settings > General > Sharing > Remote Login and turn it ON.')
        print('You do NOT need to reinstall P0 after enabling it; the helpers and pairing state are already prepared.')

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
        _required('No SSH host fingerprints were found. Pairing cannot be completed yet.')
    else:
        for item in fps:
            print('  ' + item)

    # Use the same requirement policy as the macOS GUI. The installer should
    # never end with a misleading all-good banner while a mandatory component
    # is missing.
    state = admin_component_state()
    requirements = evaluate_setup_requirements(
        screen_recording=screen_capture_permission_status(),
        accessibility=accessibility_permission_status(),
        session_password_configured=load_server_password_verifier() is not None,
        admin_state=state,
        launch_at_login=True,
        auto_start_server=True,
    )
    missing = missing_setup_requirements(requirements)

    print()
    print('SETUP_COMPLETE=' + ('1' if not missing else '0'))
    print(f'MACOS_SHORT_USER={user}')
    print('REMOTE_LOGIN=' + ('on' if remote_login_ready else 'manual_required'))
    print(f'AUTO_UPDATE_MODE={auto_update_mode}')
    prelogin_state = prelogin_readiness_state(state)
    print(f'PRELOGIN_AFTER_REBOOT={prelogin_state}')
    if prelogin_state == 'VERIFIED':
        print('PRELOGIN_NOTE=LoginWindow capture/input proof is current for this Mac/build and applies to all paired Zorin clients.')
    elif prelogin_state == 'UNVERIFIED':
        print('PRELOGIN_NOTE=LoginWindow remote login is not verified for this build; normal Aqua operation is unaffected. Run capability-probe once from any paired Zorin client if you want to verify reboot-to-login.')
    elif prelogin_state == 'NOT_READY':
        print('PRELOGIN_NOTE=LoginWindow agent is not ready; normal Aqua operation is unaffected. Use Enable / Repair if pre-login-after-reboot is required.')
    if missing:
        print('REQUIRED_SETUP_BEGIN')
        for item in missing:
            print(f'REQUIRED={item.key}|{item.label}|{item.impact}')
        print('REQUIRED_SETUP_END')
        print()
        _required('Setup is incomplete. Open ZorinMacBridge Server on this Mac and resolve every red REQUIRED item before relying on normal Aqua operation.')
    else:
        print('SETUP_OK=1')
    if not remote_login_ready:
        try:
            subprocess.Popen(['/usr/bin/open', 'x-apple.systempreferences:com.apple.Sharing-Settings.extension'], cwd='/')
        except Exception:
            pass
        print('After turning Remote Login on, continue on Zorin; rerunning the installer is optional.')
    print('Next on Zorin: select this Mac -> System -> Pair / Configure.')
    print('For "macOS account password", enter the same password you use to log in/unlock this Mac.')
    print('For the normal Desktop connection, use the separate ZorinMacBridge session password you created above.')
    return 0
