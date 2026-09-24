#!/usr/bin/env python3
from __future__ import annotations

import re
import subprocess
import os
import sys
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from mac_server import (
    NativeStreamerLibrary,
    DEFAULT_SHARE, accessibility_permission_status, ensure_certificate, is_lan_ip,
    screen_capture_permission_status, serve,
)
from discovery import LanAdvertiser
from resources import resource_path, set_tk_icon
from tray_icon import TrayController
from updates import check_for_updates, current_version, install_update, updater_tls_self_test
from settings import (PasswordVerifier, load_server_password_verifier, load_server_settings,
                      persistent_server_id, save_server_password, update_server_settings)


def private_ipv4_addresses() -> list[str]:
    try:
        text = subprocess.check_output(['/sbin/ifconfig'], text=True, stderr=subprocess.DEVNULL)
    except Exception:
        return []
    found = []
    for ip in re.findall(r'\binet (\d+\.\d+\.\d+\.\d+)\b', text):
        if is_lan_ip(ip) and not ip.startswith('127.') and not ip.startswith('169.254.') and ip not in found:
            found.append(ip)
    return found


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
        self.stop_event: threading.Event | None = None
        self.thread: threading.Thread | None = None
        self.advertiser: LanAdvertiser | None = None
        self.tray = None

        self._build(ips)
        self._build_menu()
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
        pid = os.getpid()
        helper = f'while kill -0 {pid} 2>/dev/null; do sleep 0.2; done; exec /usr/bin/open -a "ZorinMacBridge Server"'
        subprocess.Popen(
            ['/bin/sh', '-c', helper],
            start_new_session=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        self.on_close()

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
            'Launch-at-login and auto-start are optional and disabled by default.\n'
            'No system daemon or background update checker is installed.',
        )

    def _build(self, ips: list[str]) -> None:
        self.root.geometry('760x700')
        outer = ttk.Frame(self.root, padding=16)
        outer.pack(fill='both', expand=True)

        ttk.Label(outer, text='ZorinMacBridge Server', font=('', 20, 'bold')).grid(row=0, column=0, columnspan=3, sticky='w')
        ttk.Label(outer, text='LAN-only macOS server with ScreenCaptureKit + H.264 streaming.').grid(row=1, column=0, columnspan=3, sticky='w', pady=(4, 16))

        ttk.Label(outer, text='Mac LAN IP').grid(row=2, column=0, sticky='w', pady=4)
        self.ip_box = ttk.Combobox(outer, textvariable=self.bind_var, values=ips, width=28)
        self.ip_box.grid(row=2, column=1, sticky='ew', pady=4)

        ttk.Label(outer, text='Port').grid(row=3, column=0, sticky='w', pady=4)
        ttk.Entry(outer, textvariable=self.port_var, width=12).grid(row=3, column=1, sticky='w', pady=4)

        ttk.Label(outer, text='Shared folder').grid(row=4, column=0, sticky='w', pady=4)
        ttk.Entry(outer, textvariable=self.share_var).grid(row=4, column=1, sticky='ew', pady=4)
        share_actions = ttk.Frame(outer)
        share_actions.grid(row=4, column=2, padx=(8, 0), pady=4)
        ttk.Button(share_actions, text='Open', command=self.open_shared_folder).pack(side='left')
        ttk.Button(share_actions, text='Choose…', command=self.choose_share).pack(side='left', padx=(6, 0))

        ttk.Label(outer, text='Session password').grid(row=5, column=0, sticky='w', pady=4)
        self.password_entry = ttk.Entry(outer, textvariable=self.password_var, show='•')
        self.password_entry.grid(row=5, column=1, sticky='ew', pady=4)
        ttk.Checkbutton(outer, text='Show', variable=self.show_password_var, command=self.toggle_password).grid(row=5, column=2, padx=(8, 0), pady=4)
        note = 'Saved verifier available — leave password blank to reuse it.' if self.saved_password else 'Enter once; only a salted verifier is stored on this Mac.'
        ttk.Label(outer, text=note).grid(row=6, column=1, columnspan=2, sticky='w', pady=(0, 2))
        ttk.Checkbutton(outer, text='Remember password verifier on this Mac', variable=self.remember_password_var).grid(row=7, column=1, columnspan=2, sticky='w', pady=(0, 6))

        ttk.Label(outer, text='TLS fingerprint').grid(row=8, column=0, sticky='nw', pady=(8, 4))
        fp = ttk.Entry(outer, textvariable=self.fp_var, state='readonly')
        fp.grid(row=8, column=1, sticky='ew', pady=(8, 4))
        ttk.Button(outer, text='Copy', command=self.copy_fp).grid(row=8, column=2, padx=(8, 0), pady=(8, 4))

        ttk.Checkbutton(
            outer,
            text='Advertise this Mac to ZorinMacBridge clients on the local LAN while the server is running',
            variable=self.advertise_var,
        ).grid(row=9, column=0, columnspan=3, sticky='w', pady=(8, 2))
        ttk.Checkbutton(
            outer, text='Launch ZorinMacBridge Server when this macOS user logs in',
            variable=self.launch_at_login_var, command=self._startup_settings_changed,
        ).grid(row=10, column=0, columnspan=3, sticky='w', pady=(2, 2))
        ttk.Checkbutton(
            outer, text='Automatically start the server when the app launches',
            variable=self.auto_start_var, command=self._startup_settings_changed,
        ).grid(row=11, column=0, columnspan=3, sticky='w', pady=(2, 6))

        perms = ttk.Frame(outer)
        perms.grid(row=12, column=0, columnspan=3, sticky='w', pady=(2, 6))
        ttk.Button(perms, text='Screen Recording settings', command=self.open_screen_settings).pack(side='left')
        ttk.Button(perms, text='Accessibility settings', command=self.open_accessibility_settings).pack(side='left', padx=(8, 0))

        buttons = ttk.Frame(outer)
        buttons.grid(row=13, column=0, columnspan=3, sticky='ew', pady=(8, 8))
        self.start_btn = ttk.Button(buttons, text='Start server', command=self.start)
        self.start_btn.pack(side='left')
        self.stop_btn = ttk.Button(buttons, text='Stop server', command=self.stop, state='disabled')
        self.stop_btn.pack(side='left', padx=(8, 0))
        ttk.Label(buttons, textvariable=self.status_var).pack(side='right')

        note = ('Screen Recording is required for video. Accessibility is required for mouse/keyboard control.\n'
                'Launch-at-login is a per-user login item, not a system daemon. It requires a logged-in macOS session.\n'
                'Use only on a trusted LAN. Do not expose port 45950 to the public Internet.')
        ttk.Label(outer, text=note, wraplength=700).grid(row=14, column=0, columnspan=3, sticky='w', pady=(4, 8))

        self.log = tk.Text(outer, height=12, wrap='word', state='disabled')
        self.log.grid(row=15, column=0, columnspan=3, sticky='nsew')
        outer.columnconfigure(1, weight=1)
        outer.rowconfigure(15, weight=1)

        self.root.protocol('WM_DELETE_WINDOW', self.on_close)


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
            self.log.see('end')
            self.log.configure(state='disabled')
        self.root.after(0, write)

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

        # Never request Screen Recording automatically from Start Server.
        # ScreenCaptureKit itself is used by the dedicated video process when a
        # client opens the video channel. This check is informational only.
        screen_permission = screen_capture_permission_status()
        if screen_permission is True:
            self._log('Screen Recording preflight: granted')
        elif screen_permission is False:
            self._log('WARNING: Screen Recording preflight is not granted for this running build. Video may require one-time approval.')
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
                serve(bind, port, share, 30.0, 2560, 8_000_000, auth,
                      stop_event=self.stop_event, log=self._log, ready_callback=ready)
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


def main() -> None:
    if '--self-test-streamer-load' in sys.argv:
        try:
            native = NativeStreamerLibrary()
            print(f'SELFTEST OK: in-process streamer library loaded from {native.path}')
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
    root = tk.Tk()
    ServerGUI(root)
    root.mainloop()


if __name__ == '__main__':
    main()
