#!/usr/bin/env python3
from __future__ import annotations

import errno
import pwd
import re
import subprocess
import os
import sys
import threading
import time
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from mac_server import (
    CapabilityProbeLibrary, NativeStreamerLibrary,
    DEFAULT_SHARE, accessibility_permission_status, ensure_certificate, is_lan_ip,
    permission_api_self_test, request_accessibility_permission, request_screen_capture_permission,
    screen_capture_permission_status, serve, configure_runtime_home, request_prelogin_handoff,
)
from discovery import LanAdvertiser
from resources import resource_path, set_tk_icon
from mac_admin_setup import (admin_component_state, clean_uninstall_zmb, finalize_virtual_hid,
                             install_unattended_access, prepare_virtual_hid, request_virtual_hid_activation,
                             ssh_host_fingerprints, uninstall_unattended_access, virtual_hid_state)
from mac_requirements import (evaluate_prelogin_requirements, evaluate_setup_requirements, missing_setup_requirements, prelogin_readiness_state)
from tray_icon import TrayController
from updates import check_for_updates, current_version, install_update, updater_tls_self_test
from settings import (PasswordVerifier, configure_server_home, load_server_password_verifier, load_server_settings,
                      persistent_server_id, save_server_password, update_server_settings)




_PRELOGIN_ADMIN_CONFIG = Path('/Library/Application Support/ZorinMacBridge/Admin/config')


def _prelogin_config_value(key: str) -> str:
    try:
        for raw in _PRELOGIN_ADMIN_CONFIG.read_text(encoding='utf-8').splitlines():
            name, sep, value = raw.partition('=')
            if sep and name == key:
                return value.strip()
    except OSError:
        pass
    return ''


def _console_session_user() -> str:
    """Return the user owning /dev/console; root/loginwindow means pre-login."""
    try:
        uid = os.stat('/dev/console').st_uid
        return pwd.getpwuid(uid).pw_name
    except Exception:
        return ''


def _gui_session_available(user: str) -> bool:
    """Return True when launchd has created the target Aqua bootstrap domain."""
    try:
        uid = int(pwd.getpwnam(str(user)).pw_uid)
    except Exception:
        return False
    try:
        result = subprocess.run(
            ['/bin/launchctl', 'print', f'gui/{uid}'],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=1.0,
            check=False,
        )
    except Exception:
        return False
    return result.returncode == 0


def _detect_aqua_session_user(target_user: str, *, probe_gui_domain: bool = True) -> tuple[str, str]:
    """Return (user, source) once LoginWindow has transitioned into Aqua."""
    console_user = _console_session_user()
    if console_user and console_user not in {'root', 'loginwindow', '_mbsetupuser'}:
        return console_user, '/dev/console'
    if probe_gui_domain and _gui_session_available(target_user):
        try:
            uid = pwd.getpwnam(str(target_user)).pw_uid
        except Exception:
            uid = '?'
        return str(target_user), f'launchctl gui/{uid}'
    return '', ''


def _serve_aqua_with_handoff_retry(*args, stop_event, log, ready_callback, retry_seconds: float = 8.0, **kwargs) -> None:
    """Retry only EADDRINUSE while LoginWindow releases the shared Desktop port."""
    deadline = time.monotonic() + max(0.0, float(retry_seconds))
    attempt = 0
    while True:
        try:
            serve(*args, stop_event=stop_event, log=log, ready_callback=ready_callback, **kwargs)
            return
        except OSError as exc:
            if exc.errno != errno.EADDRINUSE or time.monotonic() >= deadline:
                raise
            attempt += 1
            log(f'[handoff] Desktop port still owned by LoginWindow; Aqua bind retry {attempt}')
            if stop_event.wait(0.25):
                return


def _prelogin_target_home() -> tuple[str, Path]:
    user = _prelogin_config_value('TARGET_USER')
    if not re.fullmatch(r'[A-Za-z0-9._-]+', user or '') or user in {'root', 'loginwindow', '_mbsetupuser'}:
        raise RuntimeError('P0 target user is missing or invalid')
    output = subprocess.check_output(
        ['/usr/bin/dscl', '.', '-read', f'/Users/{user}', 'NFSHomeDirectory'],
        text=True, stderr=subprocess.STDOUT, timeout=5,
    ).strip()
    parts = output.split(None, 1)
    if len(parts) != 2 or not parts[1].startswith('/'):
        raise RuntimeError(f'Could not resolve home directory for {user}')
    return user, Path(parts[1]).resolve()


def _prelogin_server_cli() -> int:
    if os.geteuid() != 0:
        raise RuntimeError('pre-login server must run as root in the LoginWindow session')
    user, home = _prelogin_target_home()
    configure_server_home(home)
    configure_runtime_home(home)
    saved = load_server_settings()
    verifier = load_server_password_verifier()
    if verifier is None:
        raise RuntimeError('No saved ZorinMacBridge password verifier is available for pre-login authentication')
    ips = private_ipv4_addresses()
    bind = str(saved.get('bind') or '')
    if bind not in ips:
        bind = ips[0] if ips else ''
    if not bind:
        raise RuntimeError('No private IPv4 address is available for pre-login Desktop')
    try:
        port = int(saved.get('port', 45950))
    except (TypeError, ValueError):
        port = 45950
    server_id = str(saved.get('server_id') or _prelogin_config_value('SERVER_ID')).strip()
    if not re.fullmatch(r'[A-Za-z0-9._:-]{8,128}', server_id):
        raise RuntimeError('Existing ZorinMacBridge server id is unavailable for pre-login reuse')
    identity_dir = home / '.zorin-mac-bridge'
    cert = identity_dir / 'server-cert.pem'
    key = identity_dir / 'server-key.pem'
    if not cert.is_file() or not key.is_file():
        raise RuntimeError('Existing ZorinMacBridge TLS identity is unavailable for pre-login reuse')
    share = Path(saved.get('share') or (home / 'ZorinMac-Share'))

    def log(message: str) -> None:
        print(str(message), flush=True)

    log(f'[prelogin] target_user={user} home={home}')
    log(f'[prelogin] starting LoginWindow Desktop on {bind}:{port}')
    advertiser = LanAdvertiser(bind, port, current_version(), server_id)
    stop_event = threading.Event()

    def watch_loginwindow_handoff() -> None:
        next_gui_probe_at = 0.0
        while not stop_event.wait(0.25):
            now = time.monotonic()
            probe_gui = now >= next_gui_probe_at
            if probe_gui:
                next_gui_probe_at = now + 0.75
            aqua_user, source = _detect_aqua_session_user(user, probe_gui_domain=probe_gui)
            if not aqua_user:
                continue
            log(f'[prelogin/handoff] Aqua session detected user={aqua_user} source={source}')
            lease = request_prelogin_handoff(aqua_user, log=log)
            if lease is not None:
                lease.handoff_sent_event.wait(timeout=0.75)
            log(f'[prelogin/handoff] stopping LoginWindow listener for Aqua user={aqua_user}')
            stop_event.set()
            return

    monitor = threading.Thread(target=watch_loginwindow_handoff, name='zmb-prelogin-handoff', daemon=True)
    monitor.start()
    try:
        try:
            advertiser.start()
        except Exception as exc:
            log(f'[prelogin] WARNING: Bonjour advertisement failed: {type(exc).__name__}: {exc}')
        serve(
            bind, port, share, 30.0, 1920, 8_000_000, verifier,
            prelogin=True, stop_event=stop_event, log=log,
        )
    finally:
        stop_event.set()
        try:
            advertiser.stop()
        except Exception:
            pass
    return 0


def private_ipv4_addresses() -> list[str]:
    try:
        text = subprocess.check_output(['/sbin/ifconfig'], text=True, stderr=subprocess.DEVNULL, timeout=3)
    except Exception:
        return []
    found = []
    for ip in re.findall(r'\binet (\d+\.\d+\.\d+\.\d+)\b', text):
        if is_lan_ip(ip) and not ip.startswith('127.') and not ip.startswith('169.254.') and ip not in found:
            found.append(ip)
    return found


class _SetupAssistant:
    """Guided local setup UI. It orchestrates existing setup actions only."""

    def __init__(self, gui: "ServerGUI") -> None:
        self.gui = gui
        self.window = tk.Toplevel(gui.root)
        self.window.title('ZorinMacBridge Setup Assistant')
        self.window.geometry('760x560')
        self.window.minsize(700, 520)
        self.window.transient(gui.root)
        set_tk_icon(self.window, 'assets/server.png')
        self.window.protocol('WM_DELETE_WINDOW', self.close)
        self.step = 0
        self._auto_services_started = False
        self._auto_services_completed = False
        self.steps = [
            ('Welcome', self._welcome),
            ('Session password', self._password),
            ('macOS permissions', self._permissions),
            ('System services', self._system_services),
            ('Startup', self._startup),
            ('Finish', self._finish),
        ]

        shell = ttk.Frame(self.window, padding=18)
        shell.pack(fill='both', expand=True)
        ttk.Label(shell, text='Setup Assistant', font=('', 20, 'bold')).pack(anchor='w')
        self.step_var = tk.StringVar()
        ttk.Label(shell, textvariable=self.step_var).pack(anchor='w', pady=(3, 12))
        ttk.Separator(shell).pack(fill='x', pady=(0, 12))
        self.body = ttk.Frame(shell)
        self.body.pack(fill='both', expand=True)
        nav = ttk.Frame(shell)
        nav.pack(fill='x', pady=(12, 0))
        self.back_btn = ttk.Button(nav, text='Back', command=self.back)
        self.back_btn.pack(side='left')
        ttk.Button(nav, text='Check again', command=self.refresh).pack(side='left', padx=(8, 0))
        self.next_btn = ttk.Button(nav, text='Continue', command=self.next)
        self.next_btn.pack(side='right')
        self.show_step()

    def close(self) -> None:
        try:
            self.window.destroy()
        finally:
            self.gui._setup_assistant = None

    def _clear(self) -> None:
        for child in self.body.winfo_children():
            child.destroy()

    def _title(self, text: str, subtitle: str = '') -> None:
        ttk.Label(self.body, text=text, font=('', 16, 'bold')).pack(anchor='w')
        if subtitle:
            ttk.Label(self.body, text=subtitle, wraplength=680, justify='left').pack(anchor='w', pady=(6, 16))

    def _status_label(self, variable: tk.StringVar) -> None:
        ttk.Label(self.body, textvariable=variable, wraplength=680, justify='left').pack(fill='x', anchor='w', pady=(4, 10))

    def _welcome(self) -> None:
        self._title(
            'Welcome to ZorinMacBridge',
            'This assistant configures only ZorinMacBridge. It will guide you through the session password, '
            'macOS permissions, unattended access, Virtual HID readiness and startup settings.',
        )
        ttk.Label(
            self.body,
            text='You can close this assistant at any time and return later from Maintenance → Run Setup Assistant.',
            wraplength=680,
        ).pack(anchor='w')

    def _password(self) -> None:
        self._title(
            'ZorinMacBridge session password',
            'This is the password used by the Zorin client to authenticate to ZMB. It is separate from your macOS login password.',
        )
        status = 'Configured — you may leave the field blank.' if self.gui.saved_password is not None else 'Not configured yet.'
        ttk.Label(self.body, text=status, font=('', 11, 'bold')).pack(anchor='w', pady=(0, 10))
        row = ttk.Frame(self.body)
        row.pack(fill='x')
        entry = ttk.Entry(row, textvariable=self.gui.password_var, show='•')
        entry.pack(side='left', fill='x', expand=True)
        ttk.Checkbutton(row, text='Show', variable=self.gui.show_password_var, command=self.gui.toggle_password).pack(side='left', padx=(8, 0))
        ttk.Checkbutton(
            self.body,
            text='Remember a salted password verifier on this Mac',
            variable=self.gui.remember_password_var,
        ).pack(anchor='w', pady=(8, 8))
        ttk.Button(self.body, text='Save password', command=self.gui.save_setup_password).pack(anchor='w')

    def _permissions(self) -> None:
        self._title(
            'macOS permissions',
            'Grant Screen Recording for video and Accessibility/input permission for normal logged-in mouse and keyboard control.',
        )
        self._status_label(self.gui.screen_permission_var)
        actions = ttk.Frame(self.body)
        actions.pack(fill='x', pady=(0, 12))
        ttk.Button(actions, text='Request Screen Recording', command=self.gui.request_screen_recording_access).pack(side='left')
        ttk.Button(actions, text='Open settings', command=self.gui.open_screen_settings).pack(side='left', padx=(8, 0))
        self._status_label(self.gui.input_permission_var)
        actions2 = ttk.Frame(self.body)
        actions2.pack(fill='x')
        ttk.Button(actions2, text='Request Mouse/Keyboard Access', command=self.gui.request_input_access).pack(side='left')
        ttk.Button(actions2, text='Open settings', command=self.gui.open_accessibility_settings).pack(side='left', padx=(8, 0))
        ttk.Label(self.body, text='macOS may require you to quit and reopen the app after changing a permission.', wraplength=680).pack(anchor='w', pady=(16, 0))

    def _system_services(self) -> None:
        self._title(
            'Unattended access and LoginWindow support',
            'Install or repair the ZMB-owned system helper. Administrator authorization is requested by macOS. '
            'Remote Login remains a normal macOS setting and the helper opens no custom administration port.',
        )
        self._status_label(self.gui.admin_status_var)
        actions = ttk.Frame(self.body)
        actions.pack(fill='x')
        ttk.Button(actions, text='Install / Repair ZMB services', command=self.gui.enable_unattended_access).pack(side='left')
        ttk.Button(actions, text='Open Remote Login Settings', command=self.gui.open_remote_login_settings).pack(side='left', padx=(8, 0))
        ttk.Button(actions, text='Copy SSH fingerprint', command=self.gui.copy_ssh_fingerprint).pack(side='left', padx=(8, 0))
        ttk.Label(
            self.body,
            text='Setup Assistant installs the bundled Virtual HID package, requests Driver Extension activation '
                 'from this logged-in Aqua session, keeps checking automatically, and finishes the daemon/self-test '
                 'as soon as macOS reports the extension enabled. The only manual step macOS may require is the '
                 'one-time Driver Extension approval.',
            wraplength=680,
        ).pack(anchor='w', pady=(16, 0))
        if not self._auto_services_started:
            self._auto_services_started = True
            self.window.after(250, self._auto_system_services)

    def _auto_system_services(self) -> None:
        if not self.window.winfo_exists():
            return
        self.gui.auto_repair_setup_services(on_complete=self._auto_services_complete)

    def _auto_services_complete(self, ready: bool, detail: str) -> None:
        self._auto_services_completed = bool(ready)
        if not self.window.winfo_exists():
            return
        if self.step in {3, 5}:
            self.show_step()

    def _startup(self) -> None:
        self._title(
            'Startup behavior',
            'For unattended operation, ZMB should launch when you log in and start its server automatically.',
        )
        ttk.Checkbutton(
            self.body,
            text='Launch ZorinMacBridge Server when this macOS user logs in',
            variable=self.gui.launch_at_login_var,
            command=self.gui._startup_settings_changed,
        ).pack(anchor='w', pady=(4, 8))
        ttk.Checkbutton(
            self.body,
            text='Automatically start the server when the app launches',
            variable=self.gui.auto_start_var,
            command=self.gui._startup_settings_changed,
        ).pack(anchor='w')
        ttk.Button(self.body, text='Use recommended startup settings', command=self.gui.enable_recommended_startup).pack(anchor='w', pady=(16, 0))

    def _finish(self) -> None:
        self._title(
            'Final verification',
            'The status below is generated by the same existing readiness checks used by the main application.',
        )
        self._status_label(self.gui.required_setup_var)
        ttk.Label(
            self.body,
            text=self.gui._prelogin_installation_status_text(),
            wraplength=680,
            justify='left',
            font=('', 11, 'bold'),
        ).pack(fill='x', anchor='w', pady=(4, 10))
        actions = ttk.Frame(self.body)
        actions.pack(fill='x')
        ttk.Button(actions, text='Refresh status', command=self.gui.refresh_required_setup).pack(side='left')
        ttk.Button(actions, text='Fix next REQUIRED item', command=self.gui.fix_next_required_item).pack(side='left', padx=(8, 0))
        ttk.Button(actions, text='Start server', command=self.gui.start).pack(side='left', padx=(8, 0))

    def refresh(self) -> None:
        self.gui.refresh_required_setup()
        self.show_step(rebuild=True)

    def show_step(self, rebuild: bool = True) -> None:
        if rebuild:
            self._clear()
        title, builder = self.steps[self.step]
        self.step_var.set(f'Step {self.step + 1} of {len(self.steps)} · {title}')
        builder()
        self.back_btn.configure(state='normal' if self.step else 'disabled')
        self.next_btn.configure(text='Finish' if self.step == len(self.steps) - 1 else 'Continue')
        if self.step in {2, 3, 5}:
            self.gui.refresh_required_setup()

    def back(self) -> None:
        if self.step > 0:
            self.step -= 1
            self.show_step()

    def next(self) -> None:
        if self.step == 1 and self.gui.password_var.get().strip():
            if not self.gui.save_setup_password(show_success=False):
                return
        if self.step == 3 and not self.gui._setup_services_ready():
            self.gui.auto_repair_setup_services(on_complete=self._auto_services_complete)
            return
        if self.step >= len(self.steps) - 1:
            if not self.gui._setup_assistant_can_finish():
                self.gui.auto_repair_setup_services(on_complete=self._auto_services_complete)
                messagebox.showwarning(
                    'Setup is not complete',
                    'Setup Assistant will not close while required ZMB services or Virtual HID input are not ready.\n\n'
                    'If macOS opened Driver Extensions or Remote Login settings, complete that one system approval/switch; '
                    'ZorinMacBridge will keep checking and continue automatically.',
                )
                return
            self.close()
            return
        self.step += 1
        self.show_step()


class _UninstallDialog:
    def __init__(self, gui: "ServerGUI") -> None:
        self.gui = gui
        self.window = tk.Toplevel(gui.root)
        self.window.title('Uninstall ZorinMacBridge')
        self.window.geometry('620x410')
        self.window.resizable(False, False)
        self.window.transient(gui.root)
        self.window.grab_set()
        set_tk_icon(self.window, 'assets/server.png')
        self.mode = tk.StringVar(value='full-clean')
        frame = ttk.Frame(self.window, padding=18)
        frame.pack(fill='both', expand=True)
        ttk.Label(frame, text='Uninstall ZorinMacBridge', font=('', 18, 'bold')).pack(anchor='w')
        ttk.Label(
            frame,
            text='Choose whether a future reinstall should start completely fresh or reuse this Mac’s ZMB configuration.',
            wraplength=560,
        ).pack(anchor='w', pady=(6, 16))
        ttk.Radiobutton(
            frame,
            text='Full clean uninstall — remove ZMB application, services, pairing state and local ZMB configuration',
            variable=self.mode,
            value='full-clean',
        ).pack(anchor='w', pady=(4, 8))
        ttk.Radiobutton(
            frame,
            text='Remove application and services, but keep ZMB user configuration for reinstall',
            variable=self.mode,
            value='keep-config',
        ).pack(anchor='w', pady=(4, 14))
        ttk.Label(
            frame,
            text='Remote Login, unrelated SSH keys and the shared Karabiner DriverKit VirtualHIDDevice package are not removed.',
            wraplength=560,
        ).pack(anchor='w', pady=(0, 16))
        ttk.Separator(frame).pack(fill='x', pady=(0, 14))
        buttons = ttk.Frame(frame)
        buttons.pack(fill='x')
        ttk.Button(buttons, text='Cancel', command=self.window.destroy).pack(side='right')
        ttk.Button(buttons, text='Uninstall…', command=self.confirm).pack(side='right', padx=(0, 8))

    def confirm(self) -> None:
        full_clean = self.mode.get() == 'full-clean'
        warning = (
            'This will remove all ZorinMacBridge application data from this Mac. A future install will start fresh.'
            if full_clean else
            'This will remove the ZorinMacBridge application and system services, but keep the current user configuration.'
        )
        if not messagebox.askyesno('Confirm uninstall', warning + '\n\nContinue?', parent=self.window):
            return
        self.window.grab_release()
        self.window.destroy()
        self.gui.clean_uninstall(full_clean=full_clean)


class ServerGUI:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        root.title('ZorinMacBridge Server')
        root.geometry('720x560')
        root.minsize(680, 520)
        set_tk_icon(root, 'assets/server.png')

        ips = private_ipv4_addresses()
        saved = load_server_settings()
        default_ip = saved.get('bind') if saved.get('bind') in ips else (ips[0] if ips else '192.168.1.50')
        self.bind_var = tk.StringVar(value=default_ip)
        self.port_var = tk.StringVar(value=str(saved.get('port', 45950)))
        self.share_var = tk.StringVar(value=str(saved.get('share', DEFAULT_SHARE)))
        self.password_var = tk.StringVar()
        self.show_password_var = tk.BooleanVar(value=False)
        self.remember_password_var = tk.BooleanVar(value=True)
        self.advertise_var = tk.BooleanVar(value=bool(saved.get('advertise', True)))
        self.launch_at_login_var = tk.BooleanVar(value=bool(saved.get('launch_at_login', False)))
        self.auto_start_var = tk.BooleanVar(value=bool(saved.get('auto_start_server', False)))
        self.server_id = persistent_server_id()
        self.saved_password = load_server_password_verifier()
        self.status_var = tk.StringVar(value='Stopped')
        self.fp_var = tk.StringVar(value='')
        self.screen_permission_var = tk.StringVar(value='Screen Recording: checking…')
        self.input_permission_var = tk.StringVar(value='Mouse/keyboard control: checking…')
        self.signing_var = tk.StringVar(value='Code signing: checking…')
        self.admin_status_var = tk.StringVar(value='Unattended access: checking…')
        self.required_setup_var = tk.StringVar(value='Checking REQUIRED setup…')
        self._required_setup_label: tk.Label | None = None
        self._last_admin_state: dict[str, object] | None = None
        self._last_screen_permission: bool | None = None
        self._last_input_permission: bool | None = None
        self._admin_checked = False
        self._permissions_checked = False
        self._required_startup_alert_shown = False
        self._admin_status_serial = 0
        self._signing_status_serial = 0
        self._virtual_hid_repair_serial = 0
        self._virtual_hid_repair_active = False
        self._virtual_hid_force_requested = False
        self._virtual_hid_settings_opened = False
        self.stop_event: threading.Event | None = None
        self.thread: threading.Thread | None = None
        self.advertiser: LanAdvertiser | None = None
        self.tray = None
        self._setup_assistant: _SetupAssistant | None = None

        self._build(ips)
        self._build_menu()
        self.root.after(100, self.refresh_permission_status)
        self.root.after(180, self.refresh_admin_status)
        self.tray = TrayController(
            self.root,
            'ZorinMacBridge Server',
            resource_path('assets/server.png'),
            [
                ('Show window', self.show_window),
                ('Start server', self.start),
                ('Stop server', self.stop),
                ('Open Shared Folder', self.open_shared_folder),
                ('Check for updates', self.check_updates),
                ('Quit', self.on_close),
            ],
        )
        self.tray.start()
        try:
            self.fp_var.set(ensure_certificate())
        except Exception as exc:
            self._log(f'Certificate error: {exc}')
        # Recreate the per-user login item if the saved preference says it
        # should exist (for example after restoring config onto a new install).
        if self.launch_at_login_var.get() and not self.launch_agent_path.exists():
            self._startup_settings_changed()
        if self.auto_start_var.get():
            self.root.after(1200, self.start)

    def _build_menu(self) -> None:
        menu = tk.Menu(self.root)
        server_menu = tk.Menu(menu, tearoff=False)
        server_menu.add_command(label='Start server', command=self.start)
        server_menu.add_command(label='Stop server', command=self.stop)
        server_menu.add_separator()
        server_menu.add_command(label='Open Shared Folder', command=self.open_shared_folder, accelerator='⌘⇧O')
        server_menu.add_separator()
        server_menu.add_command(label='Exit', command=self.on_close)
        menu.add_cascade(label='Server', menu=server_menu)

        help_menu = tk.Menu(menu, tearoff=False)
        help_menu.add_command(label='Check for updates…', command=self.check_updates)
        help_menu.add_command(label='About', command=self.show_about)
        menu.add_cascade(label='Help', menu=help_menu)
        self.root.configure(menu=menu)
        self.root.bind_all('<Command-Shift-o>', lambda _e: self.open_shared_folder())
        self.root.bind_all('<Meta-Shift-o>', lambda _e: self.open_shared_folder())

    def show_window(self) -> None:
        self.root.deiconify()
        self.root.lift()
        try:
            self.root.focus_force()
        except Exception:
            pass

    def check_updates(self) -> None:
        self.status_var.set('Checking for updates…')
        self._log('Manual update check requested.')

        def worker() -> None:
            try:
                info = check_for_updates()
                self.root.after(0, lambda: self._show_update_result(info))
            except Exception as exc:
                self.root.after(0, lambda error=str(exc): self._show_update_error(error))

        threading.Thread(target=worker, daemon=True).start()

    def _show_update_result(self, info) -> None:
        if not info.available:
            messagebox.showinfo('ZorinMacBridge update', f'You are up to date (version {info.current}).')
            self.status_var.set('Running' if self.thread and self.thread.is_alive() else 'Stopped')
            return

        if not messagebox.askyesno(
            'ZorinMacBridge update',
            f'Version {info.latest} is available.\nInstalled: {info.current}\n\nDownload, verify, and install it now?',
        ):
            self.status_var.set('Running' if self.thread and self.thread.is_alive() else 'Stopped')
            return

        if self.thread and self.thread.is_alive():
            if not messagebox.askyesno(
                'Stop server for update',
                'The remote-desktop server must be stopped while the application is updated.\n\nStop it and continue?',
            ):
                self.status_var.set('Running')
                return
            self.stop()

        self.status_var.set(f'Installing ZorinMacBridge {info.latest}…')
        self._log(f'User approved update {info.current} → {info.latest}.')

        def progress(message: str) -> None:
            self._log('Updater: ' + message)
            self.root.after(0, lambda m=message: self.status_var.set(m))

        def worker() -> None:
            try:
                result = install_update(info, progress=progress)
                self.root.after(0, lambda: self._update_installed(result))
            except Exception as exc:
                self.root.after(0, lambda error=str(exc): self._show_update_install_error(error))

        threading.Thread(target=worker, daemon=True).start()

    def _update_installed(self, result) -> None:
        self.status_var.set(f'Updated to {result.installed}. Restart recommended.')
        self._log(f'Update installed successfully: {result.current} → {result.installed}')
        if messagebox.askyesno(
            'Update installed',
            f'ZorinMacBridge {result.installed} was installed successfully.\n\nQuit and reopen the app now?',
        ):
            self._restart_after_update()

    def _restart_after_update(self) -> None:
        # Replace this process with LaunchServices' `open` tool. This avoids
        # waiting for Tk/tray teardown and guarantees the old app process is gone
        # before LaunchServices starts the updated bundle.
        try:
            self.stop()
        except Exception:
            pass
        try:
            if self.tray is not None:
                self.tray.stop()
        except Exception:
            pass
        try:
            self.root.withdraw()
        except Exception:
            pass
        self._log('Restarting updated ZorinMacBridge Server through LaunchServices.')
        os.execv('/usr/bin/open', ['/usr/bin/open', '-a', 'ZorinMacBridge Server'])

    def _show_update_install_error(self, error: str) -> None:
        self._log('Update installation failed: ' + error)
        messagebox.showerror(
            'Update installation failed',
            'The update was not installed.\n\n' + error,
        )
        self.status_var.set('Stopped')

    def _show_update_error(self, error: str) -> None:
        self._log('Update check failed: ' + error)
        messagebox.showerror(
            'Update check failed',
            'The manual update check could not reach GitHub Releases.\n\n' + error,
        )
        self.status_var.set('Running' if self.thread and self.thread.is_alive() else 'Stopped')

    def show_about(self) -> None:
        messagebox.showinfo(
            'About ZorinMacBridge',
            f'ZorinMacBridge Server {current_version()}\n\n'
            'LAN-only macOS host with H.264 screen streaming.\n'
            'Full unattended operation requires macOS Remote Login plus the local P0 system helper.\n'
            'No background update checker is installed.',
        )

    def _build(self, ips: list[str]) -> None:
        self.root.geometry('920x680')
        self.root.minsize(820, 600)
        outer = ttk.Frame(self.root, padding=14)
        outer.pack(fill='both', expand=True)

        header = ttk.Frame(outer)
        header.pack(fill='x', pady=(0, 10))
        title_box = ttk.Frame(header)
        title_box.pack(side='left', fill='x', expand=True)
        ttk.Label(title_box, text='ZorinMacBridge Server', font=('', 20, 'bold')).pack(anchor='w')
        ttk.Label(title_box, text=f'macOS host · version {current_version()}').pack(anchor='w', pady=(2, 0))
        header_actions = ttk.Frame(header)
        header_actions.pack(side='right')
        self.start_btn = ttk.Button(header_actions, text='Start', command=self.start)
        self.start_btn.pack(side='left')
        self.stop_btn = ttk.Button(header_actions, text='Stop', command=self.stop, state='disabled')
        self.stop_btn.pack(side='left', padx=(6, 0))
        ttk.Label(header_actions, textvariable=self.status_var, font=('', 10, 'bold')).pack(side='left', padx=(10, 0))

        self.notebook = ttk.Notebook(outer)
        self.notebook.pack(fill='both', expand=True)
        self.overview_tab = ttk.Frame(self.notebook, padding=14)
        self.connection_tab = ttk.Frame(self.notebook, padding=14)
        self.setup_tab = ttk.Frame(self.notebook, padding=14)
        self.recovery_tab = ttk.Frame(self.notebook, padding=14)
        self.logs_tab = ttk.Frame(self.notebook, padding=10)
        self.maintenance_tab = ttk.Frame(self.notebook, padding=14)
        self.notebook.add(self.overview_tab, text='Overview')
        self.notebook.add(self.connection_tab, text='Connection')
        self.notebook.add(self.setup_tab, text='Setup')
        self.notebook.add(self.recovery_tab, text='Recovery')
        self.notebook.add(self.logs_tab, text='Logs')
        self.notebook.add(self.maintenance_tab, text='Maintenance')

        # Overview — only status and the next useful actions.
        overview = self.overview_tab
        ttk.Label(overview, text='Server overview', font=('', 15, 'bold')).pack(anchor='w')
        ttk.Label(
            overview,
            text='Use Setup Assistant for first installation. Detailed network and maintenance controls live in their own tabs.',
            wraplength=780,
        ).pack(anchor='w', pady=(4, 12))
        summary = ttk.LabelFrame(overview, text='Required setup for normal Aqua operation', padding=12)
        summary.pack(fill='x', pady=(0, 12))
        self._required_setup_label = tk.Label(
            summary,
            textvariable=self.required_setup_var,
            justify='left',
            anchor='w',
            fg='#c62828',
            font=('', 10, 'bold'),
            wraplength=760,
        )
        self._required_setup_label.pack(fill='x')
        overview_actions = ttk.Frame(summary)
        overview_actions.pack(fill='x', pady=(10, 0))
        ttk.Button(overview_actions, text='Run Setup Assistant', command=self.open_setup_assistant).pack(side='left')
        ttk.Button(overview_actions, text='Fix next REQUIRED item', command=self.fix_next_required_item).pack(side='left', padx=(8, 0))
        ttk.Button(overview_actions, text='Refresh', command=self.refresh_required_setup).pack(side='left', padx=(8, 0))
        server_box = ttk.LabelFrame(overview, text='Quick actions', padding=12)
        server_box.pack(fill='x')
        ttk.Button(server_box, text='Open Shared Folder', command=self.open_shared_folder).pack(side='left')
        ttk.Button(server_box, text='Check for updates', command=self.check_updates).pack(side='left', padx=(8, 0))
        ttk.Button(server_box, text='Show Logs', command=lambda: self.notebook.select(self.logs_tab)).pack(side='left', padx=(8, 0))

        # Connection.
        connection = self.connection_tab
        connection.columnconfigure(1, weight=1)
        ttk.Label(connection, text='Connection', font=('', 15, 'bold')).grid(row=0, column=0, columnspan=3, sticky='w', pady=(0, 12))
        ttk.Label(connection, text='Mac LAN IP').grid(row=1, column=0, sticky='w', pady=5)
        self.ip_box = ttk.Combobox(connection, textvariable=self.bind_var, values=ips, width=28)
        self.ip_box.grid(row=1, column=1, sticky='ew', pady=5)
        ttk.Label(connection, text='Port').grid(row=2, column=0, sticky='w', pady=5)
        ttk.Entry(connection, textvariable=self.port_var, width=12).grid(row=2, column=1, sticky='w', pady=5)
        ttk.Label(connection, text='Shared folder').grid(row=3, column=0, sticky='w', pady=5)
        ttk.Entry(connection, textvariable=self.share_var).grid(row=3, column=1, sticky='ew', pady=5)
        share_actions = ttk.Frame(connection)
        share_actions.grid(row=3, column=2, padx=(8, 0), pady=5)
        ttk.Button(share_actions, text='Open', command=self.open_shared_folder).pack(side='left')
        ttk.Button(share_actions, text='Choose…', command=self.choose_share).pack(side='left', padx=(6, 0))
        ttk.Label(connection, text='ZMB session password').grid(row=4, column=0, sticky='w', pady=(12, 5))
        self.password_entry = ttk.Entry(connection, textvariable=self.password_var, show='•')
        self.password_entry.grid(row=4, column=1, sticky='ew', pady=(12, 5))
        ttk.Checkbutton(connection, text='Show', variable=self.show_password_var, command=self.toggle_password).grid(row=4, column=2, padx=(8, 0), pady=(12, 5))
        password_note = ('Saved verifier available — leave blank to reuse it.' if self.saved_password else 'Set a separate ZMB password of at least 12 characters; your macOS password is not used here.')
        ttk.Label(connection, text=password_note, wraplength=650).grid(row=5, column=1, columnspan=2, sticky='w')
        ttk.Checkbutton(connection, text='Remember password verifier on this Mac', variable=self.remember_password_var).grid(row=6, column=1, columnspan=2, sticky='w', pady=(5, 8))
        ttk.Button(connection, text='Save password', command=self.save_setup_password).grid(row=7, column=1, sticky='w', pady=(0, 10))
        ttk.Label(connection, text='TLS fingerprint').grid(row=8, column=0, sticky='w', pady=5)
        ttk.Entry(connection, textvariable=self.fp_var, state='readonly').grid(row=8, column=1, sticky='ew', pady=5)
        ttk.Button(connection, text='Copy', command=self.copy_fp).grid(row=8, column=2, padx=(8, 0), pady=5)
        ttk.Checkbutton(
            connection,
            text='Advertise this Mac to ZMB clients on the local LAN while the server is running',
            variable=self.advertise_var,
        ).grid(row=9, column=0, columnspan=3, sticky='w', pady=(14, 5))
        ttk.Checkbutton(
            connection,
            text='Launch ZorinMacBridge Server when this macOS user logs in',
            variable=self.launch_at_login_var,
            command=self._startup_settings_changed,
        ).grid(row=10, column=0, columnspan=3, sticky='w', pady=5)
        ttk.Checkbutton(
            connection,
            text='Automatically start the server when the app launches',
            variable=self.auto_start_var,
            command=self._startup_settings_changed,
        ).grid(row=11, column=0, columnspan=3, sticky='w', pady=5)

        # Setup.
        setup = self.setup_tab
        ttk.Label(setup, text='Setup', font=('', 15, 'bold')).pack(anchor='w')
        ttk.Label(setup, text='Permissions and system components required by ZorinMacBridge.', wraplength=780).pack(anchor='w', pady=(4, 12))
        ttk.Button(setup, text='Run Setup Assistant', command=self.open_setup_assistant).pack(anchor='w', pady=(0, 12))
        perms = ttk.LabelFrame(setup, text='macOS permissions', padding=10)
        perms.pack(fill='x', pady=(0, 12))
        ttk.Label(perms, textvariable=self.screen_permission_var).grid(row=0, column=0, sticky='w')
        ttk.Button(perms, text='Request Screen Recording Access', command=self.request_screen_recording_access).grid(row=0, column=1, padx=(12, 0))
        ttk.Button(perms, text='Settings', command=self.open_screen_settings).grid(row=0, column=2, padx=(8, 0))
        ttk.Label(perms, textvariable=self.input_permission_var).grid(row=1, column=0, sticky='w', pady=(8, 0))
        ttk.Button(perms, text='Request Mouse/Keyboard Access', command=self.request_input_access).grid(row=1, column=1, padx=(12, 0), pady=(8, 0))
        ttk.Button(perms, text='Settings', command=self.open_accessibility_settings).grid(row=1, column=2, padx=(8, 0), pady=(8, 0))
        ttk.Label(perms, textvariable=self.signing_var).grid(row=2, column=0, columnspan=3, sticky='w', pady=(10, 0))
        perms.columnconfigure(0, weight=1)
        system = ttk.LabelFrame(setup, text='System services', padding=10)
        system.pack(fill='x')
        ttk.Label(system, textvariable=self.admin_status_var, wraplength=750).pack(anchor='w')
        sys_actions = ttk.Frame(system)
        sys_actions.pack(fill='x', pady=(10, 0))
        ttk.Button(sys_actions, text='Install / Repair', command=self.enable_unattended_access).pack(side='left')
        ttk.Button(sys_actions, text='Remote Login Settings', command=self.open_remote_login_settings).pack(side='left', padx=(8, 0))
        ttk.Button(sys_actions, text='Refresh', command=self.refresh_admin_status).pack(side='left', padx=(8, 0))

        # Recovery.
        recovery = self.recovery_tab
        ttk.Label(recovery, text='Recovery', font=('', 15, 'bold')).pack(anchor='w')
        ttk.Label(
            recovery,
            text='Repair ZMB-owned unattended/startup components without changing capture, TLS, wake or input backends.',
            wraplength=780,
        ).pack(anchor='w', pady=(4, 12))
        admin = ttk.LabelFrame(recovery, text='Unattended access / recovery', padding=10)
        admin.pack(fill='x')
        ttk.Label(admin, textvariable=self.admin_status_var, wraplength=750).pack(anchor='w')
        recovery_actions = ttk.Frame(admin)
        recovery_actions.pack(fill='x', pady=(10, 0))
        ttk.Button(recovery_actions, text='Enable / Repair', command=self.enable_unattended_access).pack(side='left')
        ttk.Button(recovery_actions, text='Copy SSH fingerprint', command=self.copy_ssh_fingerprint).pack(side='left', padx=(8, 0))
        ttk.Button(recovery_actions, text='Disable P0 only', command=self.disable_unattended_access).pack(side='left', padx=(8, 0))
        ttk.Button(recovery_actions, text='Refresh', command=self.refresh_admin_status).pack(side='left', padx=(8, 0))
        ttk.Label(
            recovery,
            text='“Disable P0 only” leaves the desktop application and normal macOS Remote Login unchanged.',
            wraplength=780,
        ).pack(anchor='w', pady=(12, 0))

        # Logs.
        logs = self.logs_tab
        log_actions = ttk.Frame(logs)
        log_actions.pack(fill='x', pady=(0, 8))
        ttk.Label(log_actions, text='Logs', font=('', 15, 'bold')).pack(side='left')
        ttk.Button(log_actions, text='Clear', command=self.clear_logs).pack(side='right')
        ttk.Button(log_actions, text='Copy all', command=self.copy_logs).pack(side='right', padx=(0, 6))
        ttk.Button(log_actions, text='Save…', command=self.save_logs).pack(side='right', padx=(0, 6))
        self.log = tk.Text(logs, height=20, wrap='word', state='disabled')
        self.log.pack(fill='both', expand=True)

        # Maintenance.
        maintenance = self.maintenance_tab
        ttk.Label(maintenance, text='Maintenance', font=('', 15, 'bold')).pack(anchor='w')
        ttk.Label(
            maintenance,
            text='Update, rerun setup, or remove ZorinMacBridge from this Mac.',
            wraplength=780,
        ).pack(anchor='w', pady=(4, 14))
        maintenance_actions = ttk.Frame(maintenance)
        maintenance_actions.pack(fill='x')
        ttk.Button(maintenance_actions, text='Check for updates', command=self.check_updates).pack(side='left')
        ttk.Button(maintenance_actions, text='Run Setup Assistant', command=self.open_setup_assistant).pack(side='left', padx=(8, 0))
        ttk.Button(maintenance_actions, text='Repair unattended access', command=self.enable_unattended_access).pack(side='left', padx=(8, 0))
        danger = ttk.LabelFrame(maintenance, text='Uninstall', padding=12)
        danger.pack(fill='x', pady=(22, 0))
        ttk.Label(
            danger,
            text='Full clean uninstall removes the ZMB app, ZMB-owned launch jobs, helpers, pairing state and local ZMB configuration. '
                 'Remote Login, unrelated SSH keys and the shared Virtual HID driver package are preserved.',
            wraplength=750,
        ).pack(anchor='w')
        ttk.Button(danger, text='Uninstall ZorinMacBridge…', command=self.open_uninstall_dialog).pack(anchor='w', pady=(12, 0))

        footer = ttk.Label(
            outer,
            text='Use only on a trusted LAN. ZorinMacBridge and SSH should not be exposed directly to the public Internet.',
            wraplength=850,
        )
        footer.pack(fill='x', pady=(8, 0))
        self.root.protocol('WM_DELETE_WINDOW', self.on_close)

    def open_setup_assistant(self) -> None:
        if self._setup_assistant is not None:
            try:
                self._setup_assistant.window.deiconify()
                self._setup_assistant.window.lift()
                self._setup_assistant.window.focus_force()
                return
            except Exception:
                self._setup_assistant = None
        self._setup_assistant = _SetupAssistant(self)

    def save_setup_password(self, *, show_success: bool = True) -> bool:
        password = self.password_var.get()
        if not password:
            if self.saved_password is not None:
                if show_success:
                    messagebox.showinfo('Session password', 'A ZorinMacBridge session password verifier is already configured.')
                return True
            messagebox.showerror('Session password', 'Enter a ZorinMacBridge session password first.')
            return False
        if len(password) < 12:
            messagebox.showerror('Session password', 'Use a session password with at least 12 characters.')
            return False
        if not self.remember_password_var.get():
            messagebox.showwarning(
                'Session password',
                'Setup requires a saved verifier for unattended/reboot operation. Enable “Remember password verifier on this Mac”.',
            )
            return False
        self.saved_password = save_server_password(password)
        self.password_var.set('')
        self._log('Session password verifier saved by Setup Assistant; plaintext password was not stored.')
        self._update_required_setup()
        if show_success:
            messagebox.showinfo('Session password', 'ZorinMacBridge session password verifier saved.')
        return True

    def enable_recommended_startup(self) -> None:
        self.launch_at_login_var.set(True)
        self.auto_start_var.set(True)
        self._startup_settings_changed()
        self._update_required_setup()

    def clear_logs(self) -> None:
        self.log.configure(state='normal')
        self.log.delete('1.0', 'end')
        self.log.configure(state='disabled')

    def copy_logs(self) -> None:
        text = self.log.get('1.0', 'end-1c')
        self.root.clipboard_clear()
        self.root.clipboard_append(text)

    def save_logs(self) -> None:
        path = filedialog.asksaveasfilename(
            title='Save ZorinMacBridge logs',
            defaultextension='.txt',
            filetypes=[('Text files', '*.txt'), ('All files', '*.*')],
            initialfile='ZorinMacBridge-Server.log.txt',
        )
        if not path:
            return
        Path(path).write_text(self.log.get('1.0', 'end-1c') + '\n', encoding='utf-8')

    def open_uninstall_dialog(self) -> None:
        _UninstallDialog(self)

    def clean_uninstall(self, *, full_clean: bool) -> None:
        self.status_var.set('Uninstalling…')
        keep_config = not full_clean

        def worker() -> None:
            try:
                output = clean_uninstall_zmb(keep_user_config=keep_config)
                self.root.after(0, lambda out=output: done(out, None))
            except Exception as exc:
                self.root.after(0, lambda err=str(exc): done('', err))

        def done(output: str, error: str | None) -> None:
            if error:
                self.status_var.set('Stopped' if not (self.thread and self.thread.is_alive()) else 'Running')
                self._log('Full uninstall failed: ' + error)
                messagebox.showerror('Uninstall ZorinMacBridge', 'Uninstall failed.\n\n' + error)
                return
            if output:
                self._log(output)
            messagebox.showinfo(
                'ZorinMacBridge removed',
                'ZorinMacBridge was removed from this Mac.\n\n'
                + ('Local ZMB configuration was also removed.' if full_clean else 'Local ZMB configuration was kept for reinstall.')
                + '\n\nRemote Login and the shared Virtual HID driver were left unchanged.',
            )
            try:
                self.root.destroy()
            except Exception:
                pass

        threading.Thread(target=worker, daemon=True).start()

    def _current_required_setup(self):
        return evaluate_setup_requirements(
            screen_recording=self._last_screen_permission,
            accessibility=self._last_input_permission,
            session_password_configured=self.saved_password is not None,
            admin_state=self._last_admin_state,
            launch_at_login=bool(self.launch_at_login_var.get()),
            auto_start_server=bool(self.auto_start_var.get()),
        )

    def _current_prelogin_setup(self):
        return evaluate_prelogin_requirements(self._last_admin_state)

    def _prelogin_status_text(self) -> str:
        state = prelogin_readiness_state(self._last_admin_state)
        admin = self._last_admin_state or {}
        if state == 'VERIFIED':
            return 'Pre-login after reboot: VERIFIED for this Mac/build; the proof applies to all paired Zorin clients.'
        if state == 'NOT_READY':
            if not admin.get('virtual_hid_input_ready'):
                return (
                    'Pre-login after reboot: SETUP INCOMPLETE — virtual HID keyboard/mouse is not active. '
                    'Setup Assistant will install/activate it automatically and keep checking for the one-time macOS approval.'
                )
            return 'Pre-login after reboot: SETUP INCOMPLETE — Setup Assistant will repair the LoginWindow agent automatically.'
        if state == 'UNKNOWN':
            return 'Pre-login after reboot: status unavailable. Normal Aqua use is evaluated separately.'
        if admin.get('prelogin_probe_present') and not admin.get('prelogin_probe_current'):
            return 'Pre-login after reboot: UNVERIFIED — the previous proof belongs to another app build. Normal Aqua use is unaffected.'
        overall = str(admin.get('prelogin_probe_overall') or 'never')
        if overall not in {'never', 'PASS'}:
            return f'Pre-login after reboot: UNVERIFIED — last capability probe result: {overall}. Normal Aqua use is unaffected.'
        return 'Pre-login after reboot: UNVERIFIED — LoginWindow capture has not been physically verified for this build.'

    def _update_required_setup(self) -> None:
        if not (self._permissions_checked and self._admin_checked):
            self.required_setup_var.set('Checking REQUIRED setup…')
            return

        missing = missing_setup_requirements(self._current_required_setup())
        if missing:
            lines = ['REQUIRED: setup incomplete — fix every item below before relying on normal Aqua operation:']
            lines.extend(f'• {item.label} — {item.impact}' for item in missing)
            self.required_setup_var.set('\n'.join(lines))
            if self._required_setup_label is not None:
                self._required_setup_label.configure(fg='#c62828')
            if not self._required_startup_alert_shown:
                self._required_startup_alert_shown = True
                messagebox.showwarning(
                    'Required setup incomplete',
                    'ZorinMacBridge is installed, but the Mac is NOT fully ready.\n\n'
                    + '\n'.join(f'REQUIRED: {item.label} — {item.impact}' for item in missing)
                    + '\n\nUse “Fix next REQUIRED item” until this panel becomes READY.',
                )
        else:
            self.required_setup_var.set(
                'READY — normal Aqua/Desktop/Files/System/Terminal operation is available.\n'
                + self._prelogin_status_text()
            )
            if self._required_setup_label is not None:
                self._required_setup_label.configure(fg='#2e7d32')

    def refresh_required_setup(self) -> None:
        self.required_setup_var.set('Checking REQUIRED setup…')
        self.refresh_permission_status()
        self.refresh_admin_status()

    def open_remote_login_settings(self) -> None:
        try:
            subprocess.Popen(
                ['/usr/bin/open', 'x-apple.systempreferences:com.apple.Sharing-Settings.extension'],
                cwd='/',
            )
        except Exception as exc:
            messagebox.showerror('Remote Login settings', f'Could not open Sharing settings.\n\n{exc}')

    def fix_next_required_item(self) -> None:
        if not (self._permissions_checked and self._admin_checked):
            self.refresh_required_setup()
            return
        missing = missing_setup_requirements(self._current_required_setup())
        if not missing:
            messagebox.showinfo(
                'Required setup',
                'Normal Aqua operation is READY.\n\n' + self._prelogin_status_text(),
            )
            return
        keys = {item.key for item in missing}

        if keys & {'p0_status', 'p0_installed', 'p0_daemon', 'update_daemon'}:
            self.notebook.select(self.setup_tab)
            self.enable_unattended_access()
            return
        if 'remote_login' in keys or 'ssh_transport' in keys:
            self.notebook.select(self.setup_tab)
            self.open_remote_login_settings()
            messagebox.showwarning(
                'REQUIRED: Remote Login',
                'Turn ON System Settings → General → Sharing → Remote Login.\n\n'
                'This is required for pairing, System, Terminal and Project actions. '
                'Then click “Refresh REQUIRED setup”.',
            )
            return
        if 'ssh_host_key' in keys:
            # Enable / Repair runs the safe ssh-keygen -A repair when Remote Login is on.
            self.enable_unattended_access()
            return
        if 'screen_recording' in keys:
            self.notebook.select(self.setup_tab)
            self.request_screen_recording_access()
            return
        if 'accessibility' in keys:
            self.notebook.select(self.setup_tab)
            self.request_input_access()
            return
        if 'session_password' in keys:
            self.notebook.select(self.connection_tab)
            try:
                self.password_entry.focus_set()
            except Exception:
                pass
            messagebox.showwarning(
                'REQUIRED: ZMB session password',
                'Enter a ZorinMacBridge session password of at least 12 characters and start the server once with '
                '“Remember password verifier on this Mac” enabled.',
            )
            return
        if keys & {'launch_at_login', 'auto_start_server'}:
            self.launch_at_login_var.set(True)
            self.auto_start_var.set(True)
            self._startup_settings_changed()
            self._update_required_setup()
            messagebox.showinfo('Required setup', 'Launch-at-login and automatic server start were enabled.')


    def _prelogin_components_ready(self) -> bool:
        state = self._last_admin_state or {}
        return bool(
            state.get('installed')
            and state.get('prelogin_agent_contract_ready')
            and state.get('virtual_hid_input_ready')
        )

    def _setup_services_ready(self) -> bool:
        state = self._last_admin_state or {}
        return bool(
            self._prelogin_components_ready()
            and state.get('daemon_loaded')
            and state.get('updater_loaded')
            and state.get('remote_login') == 'on'
        )

    def _setup_assistant_can_finish(self) -> bool:
        if not (self._permissions_checked and self._admin_checked):
            return False
        if missing_setup_requirements(self._current_required_setup()):
            return False
        return self._prelogin_components_ready()

    def _prelogin_installation_status_text(self) -> str:
        state = self._last_admin_state or {}
        if self._prelogin_components_ready():
            if state.get('prelogin_capture_proof_ready'):
                return 'Pre-login components: READY and current reboot/LoginWindow capture proof is present.'
            return (
                'Pre-login components: READY — Virtual HID keyboard/mouse and the LoginWindow agent are installed/active. '
                'The reboot capture proof remains UNVERIFIED until the next physical reboot/LoginWindow test.'
            )
        if self._virtual_hid_repair_active:
            return 'Pre-login components: REPAIR IN PROGRESS — Setup Assistant is installing/activating Virtual HID automatically.'
        return 'Pre-login components: NOT READY — Setup Assistant will repair them automatically before it can finish.'

    def open_driver_extensions_settings(self) -> None:
        targets = [
            'x-apple.systempreferences:com.apple.LoginItems-Settings.extension',
            '/System/Applications/System Settings.app',
        ]
        last_error = None
        for target in targets:
            try:
                subprocess.Popen(['/usr/bin/open', target], cwd='/')
                return
            except Exception as exc:
                last_error = exc
        if last_error is not None:
            messagebox.showerror('Driver Extensions', f'Could not open System Settings.\n\n{last_error}')

    def auto_repair_setup_services(self, *, on_complete=None) -> None:
        """Bring P0 + LoginWindow agent + Virtual HID as far as macOS permits automatically."""
        def worker() -> None:
            try:
                state = admin_component_state()
                self.root.after(0, lambda st=state: decide(st))
            except Exception as exc:
                self.root.after(0, lambda err=str(exc): deliver(False, err))

        def decide(state: dict[str, object]) -> None:
            self._last_admin_state = dict(state)
            self._admin_checked = True
            core_ready = bool(
                state.get('installed')
                and state.get('daemon_loaded')
                and state.get('updater_loaded')
                and state.get('prelogin_agent_contract_ready')
            )
            if not core_ready:
                self.enable_unattended_access(automatic=True, on_complete=after_p0)
                return
            if state.get('virtual_hid_input_ready'):
                verify_and_finish('already-ready')
                return
            self.ensure_virtual_hid_ready(on_complete=after_hid)

        def after_p0(ok: bool, detail: str) -> None:
            if not ok:
                deliver(False, detail)
                return
            verify_and_finish(detail)

        def after_hid(ok: bool, detail: str) -> None:
            if not ok:
                deliver(False, detail)
                return
            verify_and_finish(detail)

        def verify_and_finish(detail: str) -> None:
            def state_worker() -> None:
                try:
                    state = admin_component_state()
                    self.root.after(0, lambda st=state: verified(st, detail))
                except Exception as exc:
                    self.root.after(0, lambda err=str(exc): deliver(False, err))
            threading.Thread(target=state_worker, daemon=True).start()

        def verified(state: dict[str, object], detail: str) -> None:
            self._last_admin_state = dict(state)
            self._admin_checked = True
            self._update_required_setup()
            services_ready = bool(
                state.get('installed')
                and state.get('daemon_loaded')
                and state.get('updater_loaded')
                and state.get('prelogin_agent_contract_ready')
                and state.get('virtual_hid_input_ready')
            )
            if not services_ready:
                deliver(False, 'system-services-not-ready')
                return
            if state.get('remote_login') != 'on':
                self.open_remote_login_settings()
                self.admin_status_var.set(
                    'ZMB services + Virtual HID are ready · ACTION REQUIRED: turn on macOS Remote Login; Setup Assistant will detect it automatically.'
                )
                deliver(False, 'remote-login-required')
                return
            deliver(True, detail)

        def deliver(ok: bool, detail: str) -> None:
            if on_complete is not None:
                try:
                    on_complete(bool(ok), str(detail))
                except Exception:
                    pass

        threading.Thread(target=worker, daemon=True).start()

    def ensure_virtual_hid_ready(self, *, on_complete=None) -> None:
        if self._virtual_hid_repair_active:
            return
        self._virtual_hid_repair_serial += 1
        token = self._virtual_hid_repair_serial
        self._virtual_hid_repair_active = True
        self._virtual_hid_force_requested = False
        self._virtual_hid_settings_opened = False
        self.admin_status_var.set('Preparing Virtual HID keyboard/mouse…')

        def complete(ok: bool, detail: str) -> None:
            if token != self._virtual_hid_repair_serial:
                return
            self._virtual_hid_repair_active = False
            self.refresh_admin_status()
            if on_complete is not None:
                try:
                    on_complete(bool(ok), str(detail))
                except Exception:
                    pass

        def prepare_worker() -> None:
            try:
                state = virtual_hid_state()
                output = ''
                if not state.get('virtual_hid_installed'):
                    output = prepare_virtual_hid()
                    state = virtual_hid_state()
                self.root.after(0, lambda st=state, out=output: prepared(st, out))
            except Exception as exc:
                self.root.after(0, lambda err=str(exc): failed('Virtual HID preparation failed: ' + err))

        def prepared(state: dict[str, object], output: str) -> None:
            if token != self._virtual_hid_repair_serial:
                return
            if output:
                self._log(output)
            if state.get('virtual_hid_extension_ready'):
                finalize_async()
                return
            self.admin_status_var.set('Virtual HID installed · waiting for one-time macOS Driver Extension approval…')
            try:
                request_virtual_hid_activation(force=False)
                self._log('Virtual HID activation requested from the logged-in Aqua session.')
            except Exception as exc:
                failed('Could not request Virtual HID activation: ' + str(exc))
                return
            if not self._virtual_hid_settings_opened:
                self._virtual_hid_settings_opened = True
                self.open_driver_extensions_settings()
            self.root.after(1200, poll_activation)

        def poll_activation() -> None:
            if token != self._virtual_hid_repair_serial or not self._virtual_hid_repair_active:
                return

            def worker() -> None:
                try:
                    state = virtual_hid_state()
                    self.root.after(0, lambda st=state: polled(st))
                except Exception as exc:
                    self.root.after(0, lambda err=str(exc): failed('Virtual HID status check failed: ' + err))

            threading.Thread(target=worker, daemon=True).start()

        def polled(state: dict[str, object]) -> None:
            if token != self._virtual_hid_repair_serial:
                return
            if state.get('virtual_hid_extension_ready'):
                self.admin_status_var.set('Virtual HID Driver Extension enabled · completing keyboard/mouse self-test…')
                finalize_async()
                return
            if not self._virtual_hid_force_requested:
                self._virtual_hid_force_requested = True
                try:
                    request_virtual_hid_activation(force=True)
                    self._log('Virtual HID forceActivate requested from the logged-in Aqua session.')
                except Exception as exc:
                    self._log('Virtual HID forceActivate request failed: ' + str(exc))
            self.admin_status_var.set(
                'Virtual HID approval required · enable the ZorinMacBridge/Karabiner Driver Extension in System Settings. '
                'Setup Assistant is checking automatically…'
            )
            self.root.after(1500, poll_activation)

        def finalize_async() -> None:
            def worker() -> None:
                last_error = ''
                for attempt in range(2):
                    try:
                        output = finalize_virtual_hid()
                        self.root.after(0, lambda out=output: finalized(out))
                        return
                    except Exception as exc:
                        last_error = str(exc)
                        if attempt == 0:
                            time.sleep(2.0)
                self.root.after(0, lambda err=last_error: failed('Virtual HID finalization failed: ' + err))

            threading.Thread(target=worker, daemon=True).start()

        def finalized(output: str) -> None:
            if token != self._virtual_hid_repair_serial:
                return
            if output:
                self._log(output)
            self.admin_status_var.set('Virtual HID keyboard/mouse: READY')
            complete(True, 'virtual-hid-ready')

        def failed(detail: str) -> None:
            if token != self._virtual_hid_repair_serial:
                return
            self._log(detail)
            self.admin_status_var.set(detail)
            complete(False, detail)

        threading.Thread(target=prepare_worker, daemon=True).start()


    def refresh_admin_status(self) -> None:
        # launchctl/systemsetup/nc are external commands with timeouts. Never run
        # them on Tk's main thread or the Mac window can look frozen.
        self._admin_status_serial += 1
        token = self._admin_status_serial
        self.admin_status_var.set('Unattended access: checking…')

        def worker() -> None:
            try:
                state = admin_component_state()
                error = None
            except Exception as exc:
                state = None
                error = str(exc)
            try:
                self.root.after(0, lambda: done(token, state, error))
            except Exception:
                pass

        def done(job_token: int, state, error: str | None) -> None:
            if job_token != self._admin_status_serial:
                return
            self._admin_checked = True
            if error or state is None:
                self._last_admin_state = None
                self.admin_status_var.set(f'Unattended access status unavailable: {error or "unknown error"}')
                self._update_required_setup()
                return
            self._last_admin_state = dict(state)
            installed = bool(state.get('installed'))
            loaded = bool(state.get('daemon_loaded'))
            updater_loaded = bool(state.get('updater_loaded'))
            remote = str(state.get('remote_login') or 'unknown')
            if installed and loaded and updater_loaded:
                text = f'P0 system service: ready · Update service: ready · Remote Login: {remote}'
            elif installed:
                text = f'P0 files installed but a system service is not loaded · Remote Login: {remote}'
            else:
                text = f'P0 unattended access is not installed · Remote Login: {remote}'
            if installed:
                agent = 'ready' if state.get('prelogin_agent_contract_ready') else 'not ready'
                signing = str(state.get('release_signing_mode') or 'unknown')
                if state.get('prelogin_probe_ready'):
                    probe_text = 'VERIFIED/current'
                elif state.get('prelogin_probe_present') and not state.get('prelogin_probe_current'):
                    probe_text = 'UNVERIFIED/stale'
                else:
                    probe_text = 'UNVERIFIED/' + str(state.get('prelogin_probe_overall') or 'never')
                vhid = 'ready' if state.get('virtual_hid_input_ready') else 'not-ready'
                text += f' · Pre-login after reboot: virtual-hid={vhid}, agent={agent}, proof={probe_text} · Signing: {signing}'
            self.admin_status_var.set(text)
            self._update_required_setup()

        threading.Thread(target=worker, daemon=True).start()

    def enable_unattended_access(self, *, automatic: bool = False, on_complete=None) -> None:
        if not automatic and not messagebox.askyesno(
            'Enable unattended access',
            'Install/repair the ZorinMacBridge P0 system helper and enable macOS Remote Login?\n\n'
            'macOS will ask for an administrator password. The system helper opens no custom network port.',
        ):
            return
        self.admin_status_var.set('Installing unattended access…')

        def worker() -> None:
            try:
                output = install_unattended_access()
                self.root.after(0, lambda out=output: done(out, None))
            except Exception as exc:
                self.root.after(0, lambda err=str(exc): done('', err))

        def done(output: str, error: str | None) -> None:
            if error:
                self._log('P0 installation failed: ' + error)
                messagebox.showerror('Unattended access', 'P0 installation failed.\n\n' + error)
                self.refresh_admin_status()
                if on_complete is not None:
                    on_complete(False, error)
                return
            self.launch_at_login_var.set(True)
            self.auto_start_var.set(True)
            update_server_settings(launch_at_login=True, auto_start_server=True)
            self._log('P0 unattended access installed/repaired.')
            if output:
                self._log(output)
            self.refresh_admin_status()

            def after_hid(ok: bool, detail: str) -> None:
                def state_worker() -> None:
                    try:
                        state = admin_component_state()
                    except Exception:
                        state = {}
                    self.root.after(0, lambda st=state: after_state(st, ok, detail))

                threading.Thread(target=state_worker, daemon=True).start()

            def after_state(state: dict[str, object], hid_ok: bool, detail: str) -> None:
                self._last_admin_state = dict(state)
                self._admin_checked = True
                self._update_required_setup()
                if state.get('remote_login') != 'on':
                    self.open_remote_login_settings()
                    messagebox.showwarning(
                        'REQUIRED: Remote Login is OFF',
                        'ZorinMacBridge installed the P0 helper and Virtual HID components, but macOS Remote Login (SSH) is OFF.\n\n'
                        'Turn on System Settings → General → Sharing → Remote Login. Setup Assistant will detect it on the next check; '
                        'you do not need to reinstall P0.',
                    )
                elif hid_ok:
                    self.copy_ssh_fingerprint(show_dialog=not automatic)
                if on_complete is not None:
                    on_complete(bool(hid_ok), detail)

            self.ensure_virtual_hid_ready(on_complete=after_hid)

        threading.Thread(target=worker, daemon=True).start()

    def disable_unattended_access(self) -> None:
        if not messagebox.askyesno(
            'Disable P0 unattended access',
            'Remove the ZorinMacBridge system helper and all ZorinMacBridge-Admin SSH pairing keys from this Mac?\n\n'
            'Normal Remote Login and the desktop application are left unchanged.',
        ):
            return
        self.admin_status_var.set('Removing P0 unattended access…')

        def worker() -> None:
            try:
                output = uninstall_unattended_access()
                self.root.after(0, lambda out=output: done(out, None))
            except Exception as exc:
                self.root.after(0, lambda err=str(exc): done('', err))

        def done(output: str, error: str | None) -> None:
            if error:
                self._log('P0 removal failed: ' + error)
                messagebox.showerror('Unattended access', 'P0 removal failed.\n\n' + error)
            else:
                self._log('P0 unattended access removed.')
                if output:
                    self._log(output)
            self.refresh_admin_status()

        threading.Thread(target=worker, daemon=True).start()

    def copy_ssh_fingerprint(self, show_dialog: bool = False) -> None:
        def worker() -> None:
            try:
                fingerprints = ssh_host_fingerprints()
                if not fingerprints:
                    raise RuntimeError(
                        'REQUIRED: no macOS SSH host public keys were found. '
                        'Turn on Remote Login, then use Enable / Repair to regenerate missing host keys.'
                    )
                text = '\n'.join(fingerprints)
                error = None
            except Exception as exc:
                text = ''
                error = str(exc)
            try:
                self.root.after(0, lambda: done(text, error))
            except Exception:
                pass

        def done(text: str, error: str | None) -> None:
            if error:
                self._log('Could not read SSH host fingerprint: ' + error)
                messagebox.showerror('REQUIRED: SSH fingerprint unavailable', error)
                return
            self.root.clipboard_clear()
            self.root.clipboard_append(text)
            self._log('Copied macOS SSH host fingerprint(s) for P0 pairing.')
            if show_dialog:
                messagebox.showinfo(
                    'Unattended access ready',
                    'P0 is installed. The SSH fingerprint below is needed only for FIRST enrollment or if macOS SSH host keys genuinely change. Already-paired Zorin clients remember and verify the key automatically.\n\n' + text,
                )

        threading.Thread(target=worker, daemon=True).start()

    def open_shared_folder(self) -> None:
        """Open the configured transfer folder in Finder."""
        try:
            path = Path(self.share_var.get()).expanduser().resolve()
            path.mkdir(parents=True, exist_ok=True)
            subprocess.Popen(['/usr/bin/open', str(path)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            self._log(f'Opened shared folder in Finder: {path}')
        except Exception as exc:
            self._log(f'Could not open shared folder: {exc}')
            messagebox.showerror('Open Shared Folder', f'Could not open the shared folder.\n\n{exc}')

    def toggle_password(self) -> None:
        self.password_entry.configure(show='' if self.show_password_var.get() else '•')

    @property
    def launch_agent_path(self) -> Path:
        return Path.home() / 'Library' / 'LaunchAgents' / 'com.ilovemyprojects.zorinmacbridge.server.plist'

    def _startup_settings_changed(self) -> None:
        update_server_settings(launch_at_login=bool(self.launch_at_login_var.get()),
                               auto_start_server=bool(self.auto_start_var.get()))
        path = self.launch_agent_path
        if self.launch_at_login_var.get():
            path.parent.mkdir(parents=True, exist_ok=True)
            plist = """<?xml version=\"1.0\" encoding=\"UTF-8\"?>
<!DOCTYPE plist PUBLIC \"-//Apple//DTD PLIST 1.0//EN\" \"http://www.apple.com/DTDs/PropertyList-1.0.dtd\">
<plist version=\"1.0\"><dict>
<key>Label</key><string>com.ilovemyprojects.zorinmacbridge.server</string>
<key>ProgramArguments</key><array><string>/usr/bin/open</string><string>-a</string><string>ZorinMacBridge Server</string></array>
<key>RunAtLoad</key><true/>
</dict></plist>
"""
            path.write_text(plist, encoding='utf-8')
            os.chmod(path, 0o600)
            self._log(f'Launch-at-login enabled: {path}')
        else:
            try:
                path.unlink()
            except FileNotFoundError:
                pass
            self._log('Launch-at-login disabled.')
        self._update_required_setup()

    def _app_bundle_path(self) -> Path | None:
        try:
            exe = Path(sys.executable).resolve()
        except Exception:
            return None
        for parent in (exe, *exe.parents):
            if parent.suffix == '.app' and parent.is_dir():
                return parent
        return None

    def _code_signing_mode(self) -> str:
        app = self._app_bundle_path()
        if app is None:
            return 'source/unknown'
        try:
            from mac_release_signing import app_has_release_identity, release_identity_summary
            if app_has_release_identity(app):
                return release_identity_summary(app)
        except Exception:
            pass
        try:
            from mac_local_signing import app_has_local_identity, identity_summary
            if app_has_local_identity(app):
                return 'legacy stable local DR · ' + identity_summary()
        except Exception:
            pass
        try:
            proc = subprocess.run(
                ['/usr/bin/codesign', '-dv', '--verbose=4', str(app)],
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, timeout=4, check=False,
            )
            text = proc.stdout or ''
            if 'Signature=adhoc' in text:
                return 'transport/legacy ad-hoc'
            for line in text.splitlines():
                if line.startswith('Authority='):
                    return 'release transport · ' + line.split('Authority=', 1)[1].strip()
            return 'signed/unknown identity' if proc.returncode == 0 else 'unknown'
        except Exception:
            return 'unknown'

    def restart_app(self) -> None:
        try:
            self.stop()
        except Exception:
            pass
        try:
            if self.tray is not None:
                self.tray.stop()
        except Exception:
            pass
        try:
            self.root.withdraw()
        except Exception:
            pass
        self._log('Restarting ZorinMacBridge Server through LaunchServices.')
        os.execv('/usr/bin/open', ['/usr/bin/open', '-a', 'ZorinMacBridge Server'])

    def refresh_permission_status(self) -> None:
        screen = screen_capture_permission_status()
        input_access = accessibility_permission_status()
        self._last_screen_permission = screen
        self._last_input_permission = input_access
        self._permissions_checked = True
        screen_text = 'Granted' if screen is True else ('Not granted' if screen is False else 'Unknown')
        input_text = 'Granted' if input_access is True else ('Not granted' if input_access is False else 'Unknown')
        self.screen_permission_var.set(f'Screen Recording: {screen_text}')
        self.input_permission_var.set(f'Mouse/keyboard control: {input_text}')
        self._update_required_setup()

        # codesign can take seconds on a PyInstaller bundle. Keep it off Tk.
        self._signing_status_serial += 1
        token = self._signing_status_serial
        self.signing_var.set('Code signing: checking…')

        def worker() -> None:
            signing = self._code_signing_mode()
            try:
                self.root.after(0, lambda: done(token, signing))
            except Exception:
                pass

        def done(job_token: int, signing: str) -> None:
            if job_token == self._signing_status_serial:
                self.signing_var.set(f'Code signing: {signing}')

        threading.Thread(target=worker, daemon=True).start()

    def request_screen_recording_access(self) -> None:
        if screen_capture_permission_status() is True:
            self._log('Screen Recording permission is already granted to this running build.')
            self.refresh_permission_status()
            return
        self._log('Local user requested the macOS Screen Recording permission prompt.')
        result = request_screen_capture_permission()
        if result is None:
            self._log('CGRequestScreenCaptureAccess is unavailable; opening Screen Recording settings instead.')
            self.open_screen_settings()
        else:
            self._log(f'CGRequestScreenCaptureAccess returned {result}. macOS may require the app to be fully quit and reopened after approval.')
        self.root.after(700, self.refresh_permission_status)

    def request_input_access(self) -> None:
        if accessibility_permission_status() is True:
            self._log('Mouse/keyboard control permission is already granted to this running build.')
            self.refresh_permission_status()
            return
        self._log('Local user requested macOS permission for posting mouse/keyboard events.')
        result = request_accessibility_permission()
        if result is None:
            self._log('CGRequestPostEventAccess is unavailable; opening Accessibility settings instead.')
            self.open_accessibility_settings()
        else:
            self._log(f'CGRequestPostEventAccess returned {result}. If macOS shows the Accessibility pane, enable ZorinMacBridge Server there.')
            if result is False:
                self.root.after(350, self.open_accessibility_settings)
        self.root.after(900, self.refresh_permission_status)

    def open_screen_settings(self) -> None:
        subprocess.Popen(['/usr/bin/open', 'x-apple.systempreferences:com.apple.preference.security?Privacy_ScreenCapture'])

    def open_accessibility_settings(self) -> None:
        subprocess.Popen(['/usr/bin/open', 'x-apple.systempreferences:com.apple.preference.security?Privacy_Accessibility'])

    def choose_share(self) -> None:
        selected = filedialog.askdirectory(initialdir=self.share_var.get() or str(Path.home()))
        if selected:
            self.share_var.set(selected)

    def copy_fp(self) -> None:
        self.root.clipboard_clear()
        self.root.clipboard_append(self.fp_var.get())

    def _log(self, msg: str) -> None:
        def write():
            self.log.configure(state='normal')
            self.log.insert('end', msg.rstrip() + '\n')
            try:
                lines = int(self.log.index('end-1c').split('.')[0])
                if lines > 5000:
                    self.log.delete('1.0', '1001.0')
            except Exception:
                pass
            self.log.see('end')
            self.log.configure(state='disabled')
        try:
            self.root.after(0, write)
        except Exception:
            pass

    def start(self) -> None:
        if self.thread is not None and self.thread.is_alive():
            return
        bind = self.bind_var.get().strip()
        password = self.password_var.get()
        if not is_lan_ip(bind):
            messagebox.showerror('Invalid IP', 'Enter a literal private LAN IP address of this Mac.')
            return
        if password:
            if len(password) < 12:
                messagebox.showerror('Weak password', 'Use a session password with at least 12 characters.')
                return
            auth = save_server_password(password) if self.remember_password_var.get() else PasswordVerifier.from_password(password)
            if self.remember_password_var.get():
                self.saved_password = auth
                self.password_var.set('')
                self._log('Session password verifier saved; plaintext password is not stored on the Mac.')
                self._update_required_setup()
        elif self.saved_password is not None:
            auth = self.saved_password
        else:
            messagebox.showerror('Password required', 'Enter a session password once. It can then be remembered as a salted verifier.')
            return
        try:
            port = int(self.port_var.get())
        except ValueError:
            messagebox.showerror('Invalid port', 'Port must be a number.')
            return

        # Never request Screen Recording automatically from Start Server or Connect.
        # The H.264 video channel checks this same-process preflight before it
        # touches ScreenCaptureKit, so a remote connection cannot summon a TCC prompt.
        self.refresh_permission_status()
        screen_permission = screen_capture_permission_status()
        if screen_permission is True:
            self._log('Screen Recording preflight: granted')
        elif screen_permission is False:
            self._log('WARNING: Screen Recording is not granted to this exact running build. Remote Connect will not trigger a permission prompt; grant it locally in System Settings and restart the app.')
        else:
            self._log('Screen Recording preflight API unavailable; native video channel will report its own status.')

        accessibility = accessibility_permission_status()
        if accessibility is False:
            self._log('WARNING: Accessibility permission is not granted; remote mouse/keyboard input may not work.')
        elif accessibility is True:
            self._log('Accessibility permission: granted')

        advertise = bool(self.advertise_var.get())
        share = Path(self.share_var.get())
        update_server_settings(bind=bind, port=port, share=str(share), advertise=advertise,
                               launch_at_login=bool(self.launch_at_login_var.get()),
                               auto_start_server=bool(self.auto_start_var.get()))
        self.stop_event = threading.Event()
        self.start_btn.configure(state='disabled')
        self.stop_btn.configure(state='normal')
        self.status_var.set('Starting…')

        def ready(fp: str) -> None:
            self.root.after(0, lambda: self.fp_var.set(fp))
            self.root.after(0, lambda: self.status_var.set('Running'))
            if advertise and self.stop_event is not None and not self.stop_event.is_set():
                try:
                    self.advertiser = LanAdvertiser(bind, port, current_version(), self.server_id)
                    self.advertiser.start()
                    self._log(f'LAN discovery: advertised by mDNS/Bonjour on {bind}:{port}')
                except Exception as exc:
                    self._log(f'LAN discovery unavailable: {exc}')

        def worker() -> None:
            try:
                _serve_aqua_with_handoff_retry(
                    bind, port, share, 30.0, 2560, 8_000_000, auth,
                    stop_event=self.stop_event, log=self._log, ready_callback=ready,
                )
            except Exception as exc:
                self._log(f'ERROR: {exc}')
                self.root.after(0, lambda error=str(exc): messagebox.showerror('Server error', error))
            finally:
                self.root.after(0, self._stopped_ui)

        self.thread = threading.Thread(target=worker, daemon=True)
        self.thread.start()

    def _stop_advertiser(self) -> None:
        advertiser, self.advertiser = self.advertiser, None
        if advertiser is not None:
            try:
                advertiser.stop()
            except Exception:
                pass

    def stop(self) -> None:
        self._stop_advertiser()
        if self.stop_event is not None:
            self.stop_event.set()
            self.status_var.set('Stopping…')
            self.stop_btn.configure(state='disabled')

    def _stopped_ui(self) -> None:
        self._stop_advertiser()
        self.status_var.set('Stopped')
        self.start_btn.configure(state='normal')
        self.stop_btn.configure(state='disabled')

    def on_close(self) -> None:
        self._stop_advertiser()
        if self.stop_event is not None:
            self.stop_event.set()
        if self.tray is not None:
            self.tray.stop()
        self.root.destroy()


def _capability_probe_cli(output_path: Path, image_path: Path) -> int:
    if os.geteuid() != 0:
        raise RuntimeError('capability probe must run as root in the LoginWindow session')
    probe = CapabilityProbeLibrary()
    rc = probe.run(output_path, image_path)
    if rc != 0:
        raise RuntimeError(f'native capability probe failed with code {rc}')
    return 0


def _argv_path(flag: str, default: str) -> Path:
    if flag not in sys.argv:
        return Path(default)
    idx = sys.argv.index(flag)
    if idx + 1 >= len(sys.argv):
        raise RuntimeError(f'{flag} requires a path')
    return Path(sys.argv[idx + 1])


def _p0_update_check_cli() -> int:
    from updates import check_for_updates
    info = check_for_updates(timeout=12.0)
    print(f'current={info.current}')
    print(f'latest={info.latest}')
    print(f'available={1 if info.available else 0}')
    print(f'page_url={info.page_url}')
    return 0


def _p0_update_stage_cli(stage_root: Path) -> int:
    from updates import prepare_macos_update_for_root
    def progress(message: str) -> None:
        print('progress=' + str(message).replace('\n', ' '), flush=True)
    result = prepare_macos_update_for_root(stage_root, progress=progress, timeout=45.0)
    print(f'current={result.current}')
    print(f'latest={result.latest}')
    print(f'available={1 if result.available else 0}')
    if result.staged_app:
        print(f'staged_app={result.staged_app}')
    return 0


def main() -> None:
    if '--capability-probe' in sys.argv:
        try:
            output = _argv_path('--probe-output', '/var/tmp/zmb-capability-probe.result')
            image = _argv_path('--probe-image', '/var/tmp/zmb-capability-probe.png')
            raise SystemExit(_capability_probe_cli(output, image))
        except SystemExit:
            raise
        except Exception as exc:
            print(f'CAPABILITY PROBE FAILED: {type(exc).__name__}: {exc}', file=sys.stderr, flush=True)
            raise SystemExit(1)
    if '--prelogin-server' in sys.argv:
        try:
            raise SystemExit(_prelogin_server_cli())
        except SystemExit:
            raise
        except Exception as exc:
            print(f'PRELOGIN SERVER FAILED: {type(exc).__name__}: {exc}', file=sys.stderr, flush=True)
            raise SystemExit(1)
    if '--p0-update-check' in sys.argv:
        try:
            raise SystemExit(_p0_update_check_cli())
        except SystemExit:
            raise
        except Exception as exc:
            print(f'P0 UPDATE CHECK FAILED: {type(exc).__name__}: {exc}', file=sys.stderr)
            raise SystemExit(1)
    if '--p0-update-stage' in sys.argv:
        try:
            idx = sys.argv.index('--p0-update-stage')
            target = Path(sys.argv[idx + 1])
            raise SystemExit(_p0_update_stage_cli(target))
        except SystemExit:
            raise
        except Exception as exc:
            print(f'P0 UPDATE STAGE FAILED: {type(exc).__name__}: {exc}', file=sys.stderr)
            raise SystemExit(1)
    if '--terminal-setup' in sys.argv:
        try:
            from mac_terminal_setup import run_terminal_setup
            raise SystemExit(run_terminal_setup())
        except SystemExit:
            raise
        except Exception as exc:
            print(f'TERMINAL SETUP FAILED: {type(exc).__name__}: {exc}', file=sys.stderr)
            raise SystemExit(1)
    if '--prepare-local-signing' in sys.argv:
        try:
            from mac_local_signing import ensure_local_identity
            identity = ensure_local_identity()
            print(f'IDENTITY_SHA256={identity.cert_sha256}')
            print(f'REQUIREMENT={identity.requirement}')
            raise SystemExit(0)
        except Exception as exc:
            print(f'LOCAL SIGNING PREP FAILED: {type(exc).__name__}: {exc}', file=sys.stderr)
            raise SystemExit(1)
    if '--local-sign-app' in sys.argv:
        try:
            idx = sys.argv.index('--local-sign-app')
            target = Path(sys.argv[idx + 1])
            from mac_local_signing import sign_app_locally
            identity = sign_app_locally(target)
            print(f'LOCAL SIGN OK: {target} · stable DR {identity.cert_sha256}')
            raise SystemExit(0)
        except Exception as exc:
            print(f'LOCAL SIGN FAILED: {type(exc).__name__}: {exc}', file=sys.stderr)
            raise SystemExit(1)
    if '--self-test-local-signing-identity' in sys.argv:
        try:
            from mac_local_signing import ensure_local_identity
            identity = ensure_local_identity()
            print(f'SELFTEST OK: stable local designated requirement available: {identity.cert_sha256}')
            raise SystemExit(0)
        except Exception as exc:
            print(f'SELFTEST FAILED: {type(exc).__name__}: {exc}', file=sys.stderr)
            raise SystemExit(1)
    if '--verify-release-app' in sys.argv:
        try:
            idx = sys.argv.index('--verify-release-app')
            target = Path(sys.argv[idx + 1])
            from mac_release_signing import verify_release_app
            info = verify_release_app(target)
            print(f'RELEASE SIGNING OK: team={info.team_identifier} profile={info.name}')
            raise SystemExit(0)
        except SystemExit:
            raise
        except Exception as exc:
            print(f'RELEASE SIGNING FAILED: {type(exc).__name__}: {exc}', file=sys.stderr)
            raise SystemExit(1)
    if '--self-test-virtual-hid' in sys.argv:
        try:
            from zmb_macos_virtual_hid import DriverKitVirtualHIDClient
            client = DriverKitVirtualHIDClient()
            try:
                wait_seconds = float(os.environ.get('ZMB_VHID_SELFTEST_WAIT', '2.5'))
            except ValueError:
                wait_seconds = 2.5
            wait_seconds = max(0.1, min(15.0, wait_seconds))
            rc = client.open(wait_seconds=wait_seconds)
            if rc != 0:
                status = client.status()
                detail = (
                    f'rc={rc}; {client.last_error}; '
                    f'driver_activated={status.get("driver_activated")} '
                    f'driver_connected={status.get("driver_connected")} '
                    f'keyboard={status.get("keyboard_ready")} '
                    f'pointing={status.get("pointing_ready")}'
                )
                client.close()
                raise RuntimeError('virtual HID backend not ready: ' + detail)
            try:
                client.release_all()
                status = client.status()
                print(
                    'SELFTEST OK: DriverKit virtual HID ready: '
                    f'driver_activated={status.get("driver_activated")} '
                    f'driver_connected={status.get("driver_connected")} '
                    f'keyboard={status["keyboard_ready"]} pointing={status["pointing_ready"]} '
                    f'protocol={status["protocol_version"]}'
                )
            finally:
                client.close()
            raise SystemExit(0)
        except SystemExit:
            raise
        except Exception as exc:
            print(f'SELFTEST FAILED: {type(exc).__name__}: {exc}', file=sys.stderr)
            raise SystemExit(1)
    if '--self-test-permission-apis' in sys.argv:
        try:
            names = permission_api_self_test()
            print('SELFTEST OK: macOS permission APIs resolved: ' + ', '.join(names))
            raise SystemExit(0)
        except Exception as exc:
            print(f'SELFTEST FAILED: {type(exc).__name__}: {exc}', file=sys.stderr)
            raise SystemExit(1)
    if '--self-test-capability-probe-load' in sys.argv:
        try:
            probe = CapabilityProbeLibrary()
            print(f'SELFTEST OK: Stage-B capability probe library loaded from {probe.path}')
            raise SystemExit(0)
        except Exception as exc:
            print(f'SELFTEST FAILED: {type(exc).__name__}: {exc}', file=sys.stderr)
            raise SystemExit(1)
    if '--self-test-streamer-load' in sys.argv:
        try:
            native = NativeStreamerLibrary()
            native_version = native.build_version()
            expected_version = current_version()
            if native_version != expected_version:
                raise RuntimeError(f'native streamer version mismatch: app={expected_version} dylib={native_version}')
            print(f'SELFTEST OK: in-process streamer library loaded from {native.path}; version={native_version}')
            raise SystemExit(0)
        except Exception as exc:
            print(f'SELFTEST FAILED: {type(exc).__name__}: {exc}', file=sys.stderr)
            raise SystemExit(1)
    if '--self-test-update-tls' in sys.argv:
        try:
            cafile = updater_tls_self_test()
            print(f'SELFTEST OK: updater TLS trust store initialized from {cafile}')
            raise SystemExit(0)
        except Exception as exc:
            print(f'SELFTEST FAILED: {type(exc).__name__}: {exc}', file=sys.stderr)
            raise SystemExit(1)

    bootstrap_error = None
    try:
        from mac_local_signing import bootstrap_installed_app_identity
        bootstrap_installed_app_identity()
    except Exception as exc:
        bootstrap_error = f'{type(exc).__name__}: {exc}'

    root = tk.Tk()
    gui = ServerGUI(root)
    if '--setup-assistant' in sys.argv:
        root.after(300, gui.open_setup_assistant)
    if bootstrap_error:
        gui._log('Persistent local code-identity migration failed: ' + bootstrap_error)
        root.after(
            250,
            lambda: messagebox.showerror(
                'ZorinMacBridge code identity',
                'The one-time local code-identity migration could not be completed. '
                'The server will not silently reset permissions.\n\n' + bootstrap_error,
            ),
        )
    root.mainloop()


if __name__ == '__main__':
    main()
