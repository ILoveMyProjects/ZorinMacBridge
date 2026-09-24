#!/usr/bin/env python3
from __future__ import annotations

import re
import subprocess
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from mac_server import DEFAULT_SHARE, ensure_certificate, is_lan_ip, serve
from discovery import LanAdvertiser
from resources import resource_path, set_tk_icon
from tray_icon import TrayController
from updates import check_for_updates, current_version, open_release_page


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
        self.bind_var = tk.StringVar(value=ips[0] if ips else '192.168.1.50')
        self.port_var = tk.StringVar(value='45950')
        self.share_var = tk.StringVar(value=str(DEFAULT_SHARE))
        self.password_var = tk.StringVar()
        self.show_password_var = tk.BooleanVar(value=False)
        self.advertise_var = tk.BooleanVar(value=True)
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
                ('Check for updates', self.check_updates),
                ('Quit', self.on_close),
            ],
        )
        self.tray.start()
        try:
            self.fp_var.set(ensure_certificate())
        except Exception as exc:
            self._log(f'Certificate error: {exc}')

    def _build_menu(self) -> None:
        menu = tk.Menu(self.root)
        server_menu = tk.Menu(menu, tearoff=False)
        server_menu.add_command(label='Start server', command=self.start)
        server_menu.add_command(label='Stop server', command=self.stop)
        server_menu.add_separator()
        server_menu.add_command(label='Exit', command=self.on_close)
        menu.add_cascade(label='Server', menu=server_menu)

        help_menu = tk.Menu(menu, tearoff=False)
        help_menu.add_command(label='Check for updates…', command=self.check_updates)
        help_menu.add_command(label='About', command=self.show_about)
        menu.add_cascade(label='Help', menu=help_menu)
        self.root.configure(menu=menu)

    def show_window(self) -> None:
        self.root.deiconify()
        self.root.lift()
        try:
            self.root.focus_force()
        except Exception:
            pass

    def check_updates(self) -> None:
        self.status_var.set('Checking for updates…')

        def worker() -> None:
            try:
                info = check_for_updates()
                self.root.after(0, lambda: self._show_update_result(info))
            except Exception as exc:
                self.root.after(0, lambda error=str(exc): self._show_update_error(error))

        threading.Thread(target=worker, daemon=True).start()

    def _show_update_result(self, info) -> None:
        if info.available:
            if messagebox.askyesno(
                'ZorinMacBridge update',
                f'Version {info.latest} is available.\nInstalled: {info.current}\n\nOpen the release page?',
            ):
                open_release_page(info.page_url)
        else:
            messagebox.showinfo('ZorinMacBridge update', f'You are up to date (version {info.current}).')
        self.status_var.set('Running' if self.thread and self.thread.is_alive() else 'Stopped')

    def _show_update_error(self, error: str) -> None:
        messagebox.showerror(
            'Update check failed',
            'The manual update check could not reach GitHub Releases.\n\n' + error,
        )
        self.status_var.set('Running' if self.thread and self.thread.is_alive() else 'Stopped')

    def show_about(self) -> None:
        messagebox.showinfo(
            'About ZorinMacBridge',
            f'ZorinMacBridge Server {current_version()}\n\n'
            'The server runs only after you manually start it.\n'
            'There is no daemon, login item, or background update checker.',
        )

    def _build(self, ips: list[str]) -> None:
        outer = ttk.Frame(self.root, padding=16)
        outer.pack(fill='both', expand=True)

        ttk.Label(outer, text='ZorinMacBridge Server', font=('', 20, 'bold')).grid(row=0, column=0, columnspan=3, sticky='w')
        ttk.Label(outer, text='LAN-only macOS server for the Zorin/Linux client. No cloud or relay is used at runtime.').grid(row=1, column=0, columnspan=3, sticky='w', pady=(4, 16))

        ttk.Label(outer, text='Mac LAN IP').grid(row=2, column=0, sticky='w', pady=4)
        self.ip_box = ttk.Combobox(outer, textvariable=self.bind_var, values=ips, width=28)
        self.ip_box.grid(row=2, column=1, sticky='ew', pady=4)

        ttk.Label(outer, text='Port').grid(row=3, column=0, sticky='w', pady=4)
        ttk.Entry(outer, textvariable=self.port_var, width=12).grid(row=3, column=1, sticky='w', pady=4)

        ttk.Label(outer, text='Shared folder').grid(row=4, column=0, sticky='w', pady=4)
        ttk.Entry(outer, textvariable=self.share_var).grid(row=4, column=1, sticky='ew', pady=4)
        ttk.Button(outer, text='Choose…', command=self.choose_share).grid(row=4, column=2, padx=(8, 0), pady=4)

        ttk.Label(outer, text='Session password').grid(row=5, column=0, sticky='w', pady=4)
        self.password_entry = ttk.Entry(outer, textvariable=self.password_var, show='•')
        self.password_entry.grid(row=5, column=1, sticky='ew', pady=4)
        ttk.Checkbutton(outer, text='Show', variable=self.show_password_var, command=self.toggle_password).grid(row=5, column=2, padx=(8, 0), pady=4)

        ttk.Label(outer, text='TLS fingerprint').grid(row=6, column=0, sticky='nw', pady=(12, 4))
        fp = ttk.Entry(outer, textvariable=self.fp_var, state='readonly')
        fp.grid(row=6, column=1, sticky='ew', pady=(12, 4))
        ttk.Button(outer, text='Copy', command=self.copy_fp).grid(row=6, column=2, padx=(8, 0), pady=(12, 4))

        ttk.Checkbutton(
            outer,
            text='Advertise this Mac to ZorinMacBridge clients on the local LAN while the server is running',
            variable=self.advertise_var,
        ).grid(row=7, column=0, columnspan=3, sticky='w', pady=(10, 2))

        buttons = ttk.Frame(outer)
        buttons.grid(row=8, column=0, columnspan=3, sticky='ew', pady=(8, 8))
        self.start_btn = ttk.Button(buttons, text='Start server', command=self.start)
        self.start_btn.pack(side='left')
        self.stop_btn = ttk.Button(buttons, text='Stop server', command=self.stop, state='disabled')
        self.stop_btn.pack(side='left', padx=(8, 0))
        ttk.Label(buttons, textvariable=self.status_var).pack(side='right')

        note = ('macOS permissions required: Privacy & Security → Screen Recording and Accessibility.\n'
                'The app is manual: closing it stops the server. No service or login item is installed.\n'
                'Use only on a trusted LAN. Do not expose port 45950 to the public Internet.')
        ttk.Label(outer, text=note, wraplength=660).grid(row=9, column=0, columnspan=3, sticky='w', pady=(4, 8))

        self.log = tk.Text(outer, height=12, wrap='word', state='disabled')
        self.log.grid(row=10, column=0, columnspan=3, sticky='nsew')
        outer.columnconfigure(1, weight=1)
        outer.rowconfigure(10, weight=1)

        self.root.protocol('WM_DELETE_WINDOW', self.on_close)

    def toggle_password(self) -> None:
        self.password_entry.configure(show='' if self.show_password_var.get() else '•')

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
        if len(password) < 12:
            messagebox.showerror('Weak password', 'Use a session password with at least 12 characters.')
            return
        try:
            port = int(self.port_var.get())
        except ValueError:
            messagebox.showerror('Invalid port', 'Port must be a number.')
            return

        advertise = bool(self.advertise_var.get())
        share = Path(self.share_var.get())
        self.stop_event = threading.Event()
        self.start_btn.configure(state='disabled')
        self.stop_btn.configure(state='normal')
        self.status_var.set('Starting…')

        def ready(fp: str) -> None:
            self.root.after(0, lambda: self.fp_var.set(fp))
            self.root.after(0, lambda: self.status_var.set('Running'))
            if advertise and self.stop_event is not None and not self.stop_event.is_set():
                try:
                    self.advertiser = LanAdvertiser(bind, port, current_version())
                    self.advertiser.start()
                    self._log(f'LAN discovery: advertised by mDNS/Bonjour on {bind}:{port}')
                except Exception as exc:
                    self._log(f'LAN discovery unavailable: {exc}')

        def worker() -> None:
            try:
                serve(bind, port, share, 10.0, 1920, 72, password,
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
    root = tk.Tk()
    ServerGUI(root)
    root.mainloop()


if __name__ == '__main__':
    main()
