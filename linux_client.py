#!/usr/bin/env python3
from __future__ import annotations

import datetime
import hashlib
import hmac
import ipaddress
import os
import posixpath
import queue
import socket
import ssl
import struct
import threading
import traceback
import subprocess
import sys

import av
from pathlib import Path
import tkinter as tk
from tkinter import filedialog, messagebox, simpledialog, ttk
from tkinter.scrolledtext import ScrolledText

from PIL import Image, ImageTk
# Explicit import: Pillow loads this helper dynamically from ImageTk/_imagingtk.
# Keeping it visible here also ensures PyInstaller includes it in frozen Linux builds.
import PIL._tkinter_finder  # noqa: F401

from discovery import discover_servers
from resources import resource_path, set_tk_icon
from tray_icon import TrayController
from updates import check_for_updates, install_update
from settings import client_record, save_client_record
from secret_store import get_password as get_saved_password, set_password as save_password_secret

from protocol import (
    AUTH, AUTH_FAIL, AUTH_OK, CLIPBOARD_DATA, CLIPBOARD_GET, CLIPBOARD_SET,
    DOWNLOAD_BEGIN, DOWNLOAD_CHUNK, DOWNLOAD_END, DOWNLOAD_REQ, ERROR, VIDEO_H264,
    KEY, LIST_REQ, LIST_RESP, MKDIR_OK, MKDIR_REQ, MOUSE_BUTTON, MOUSE_MOVE,
    PacketReader, SCROLL, TEXT, UPLOAD_BEGIN, UPLOAD_CHUNK, UPLOAD_END,
    pack_json, pack_packet, recv_one_blocking, unpack_json,
)

CHUNK = 256 * 1024
MAX_CLIPBOARD = 2 * 1024 * 1024
V4_ALLOWED = tuple(ipaddress.ip_network(n) for n in (
    '10.0.0.0/8', '172.16.0.0/12', '192.168.0.0/16',
    '127.0.0.0/8', '169.254.0.0/16',
))
V6_ALLOWED = tuple(ipaddress.ip_network(n) for n in (
    '::1/128', 'fc00::/7', 'fe80::/10',
))

MODIFIER_KEYS = {
    'Shift_L', 'Shift_R', 'Control_L', 'Control_R', 'Alt_L', 'Alt_R',
    'Meta_L', 'Meta_R', 'Super_L', 'Super_R', 'Caps_Lock',
}
SPECIAL_KEYS = {
    'Return', 'Tab', 'BackSpace', 'Escape', 'Delete', 'Left', 'Right', 'Up',
    'Down', 'Home', 'End', 'Prior', 'Next', 'F1', 'F2', 'F3', 'F4', 'F5',
    'F6', 'F7', 'F8', 'F9', 'F10', 'F11', 'F12', 'space',
}


def is_lan_ip(text: str) -> bool:
    try:
        ip = ipaddress.ip_address(text.split('%', 1)[0])
    except ValueError:
        return False
    nets = V4_ALLOWED if ip.version == 4 else V6_ALLOWED
    return any(ip in net for net in nets)


def normalize_fp(text: str) -> str:
    return ''.join(ch for ch in text.upper() if ch in '0123456789ABCDEF')


def format_fp(text: str) -> str:
    text = normalize_fp(text)
    return ':'.join(text[i:i + 2] for i in range(0, len(text), 2))


def human_size(n: int) -> str:
    value = float(n)
    for unit in ('B', 'KiB', 'MiB', 'GiB', 'TiB'):
        if value < 1024 or unit == 'TiB':
            return f'{value:.1f} {unit}' if unit != 'B' else f'{int(value)} B'
        value /= 1024
    return f'{n} B'


def remote_join(parent: str, name: str) -> str:
    if not parent:
        return name
    return posixpath.join(parent, name)


class ClientApp:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        root.title('ZorinMacBridge — Linux → macOS')
        root.geometry('1280x900')
        root.minsize(900, 650)
        set_tk_icon(root, 'assets/client.png')

        self.ip_var = tk.StringVar(value='192.168.1.50')
        self.port_var = tk.StringVar(value='45950')
        self.password_var = tk.StringVar()
        self.fp_var = tk.StringVar()
        self.status_var = tk.StringVar(value='Disconnected')
        self.remote_path_var = tk.StringVar(value='/')
        self.linux_shortcuts_var = tk.BooleanVar(value=True)
        self.machine_var = tk.StringVar(value='')
        self.discovered_by_label = {}
        self.selected_server_id = ''
        self.selected_server_name = ''
        self.remember_credentials_var = tk.BooleanVar(value=True)
        self.remember_credentials_active = True

        self.outgoing: queue.Queue[bytes] = queue.Queue(maxsize=1000)
        self.ui_queue: queue.Queue[tuple] = queue.Queue()
        self.stop_event = threading.Event()
        self.net_thread: threading.Thread | None = None
        self.video_thread: threading.Thread | None = None
        self.video_frames: queue.Queue[Image.Image] = queue.Queue(maxsize=2)
        self.connected = False
        self.last_photo = None
        self.remote_image_size = (1, 1)
        self.display_rect = (0, 0, 1, 1)
        self.modifiers_down: set[str] = set()
        self.keys_down: set[str] = set()
        self.remote_path = ''
        self.file_entries: dict[str, dict] = {}
        self.tray = None
        self.disconnect_requested = False

        self._build_ui()
        self._build_menu()
        self.tray = TrayController(
            self.root,
            'ZorinMacBridge Client',
            resource_path('assets/client.png'),
            [
                ('Show window', self.show_window),
                ('Find Macs on LAN', self.discover_macs),
                ('Connect', self.connect),
                ('Disconnect', self.disconnect),
                ('Check for updates', self.check_updates),
                ('Quit', self.on_close),
            ],
        )
        self.tray.start()
        self.root.after(20, self._process_ui_queue)
        self.root.after(700, self.discover_macs)
        self.root.protocol('WM_DELETE_WINDOW', self.on_close)
        self._log('INFO', 'ZorinMacBridge Client started.')

    def _build_menu(self) -> None:
        menu = tk.Menu(self.root)
        connection = tk.Menu(menu, tearoff=False)
        connection.add_command(label='Find Macs on LAN', command=self.discover_macs)
        connection.add_command(label='Connect', command=self.connect)
        connection.add_command(label='Disconnect', command=self.disconnect)
        connection.add_separator()
        connection.add_command(label='Exit', command=self.on_close)
        menu.add_cascade(label='Connection', menu=connection)

        help_menu = tk.Menu(menu, tearoff=False)
        help_menu.add_command(label='Check for updates…', command=self.check_updates)
        help_menu.add_command(label='About', command=self.show_about)
        menu.add_cascade(label='Help', menu=help_menu)
        self.root.configure(menu=menu)

    def _build_ui(self) -> None:
        top = ttk.Frame(self.root, padding=8)
        top.pack(fill='x')

        ttk.Label(top, text='Discovered Mac:').grid(row=0, column=0, sticky='w')
        self.machine_box = ttk.Combobox(top, textvariable=self.machine_var, state='readonly', width=38)
        self.machine_box.grid(row=0, column=1, columnspan=3, sticky='ew', padx=(4, 8))
        self.machine_box.bind('<<ComboboxSelected>>', self._machine_selected)
        ttk.Button(top, text='Find Macs', command=self.discover_macs).grid(row=0, column=4, padx=4)
        ttk.Label(top, text='LAN discovery only works while the Mac server is running.').grid(
            row=0, column=5, columnspan=3, sticky='w', padx=(8, 0)
        )

        ttk.Label(top, text='Mac IP:').grid(row=1, column=0, sticky='w', pady=(8, 0))
        ttk.Entry(top, textvariable=self.ip_var, width=16).grid(row=1, column=1, padx=(4, 10), pady=(8, 0))
        ttk.Label(top, text='Port:').grid(row=1, column=2, sticky='w', pady=(8, 0))
        ttk.Entry(top, textvariable=self.port_var, width=7).grid(row=1, column=3, padx=(4, 10), pady=(8, 0))
        ttk.Label(top, text='Password:').grid(row=1, column=4, sticky='w', pady=(8, 0))
        ttk.Entry(top, textvariable=self.password_var, show='•', width=18).grid(row=1, column=5, padx=(4, 10), pady=(8, 0))
        ttk.Button(top, text='Connect', command=self.connect).grid(row=1, column=6, padx=4, pady=(8, 0))
        ttk.Button(top, text='Disconnect', command=self.disconnect).grid(row=1, column=7, padx=4, pady=(8, 0))

        ttk.Label(top, text='TLS SHA-256 fingerprint:').grid(row=2, column=0, columnspan=2, sticky='w', pady=(8, 0))
        ttk.Entry(top, textvariable=self.fp_var).grid(row=2, column=2, columnspan=6, sticky='ew', padx=(4, 0), pady=(8, 0))
        ttk.Label(
            top,
            text='First connection: verify the Mac fingerprint once. Saved Macs reload fingerprint/password automatically.',
        ).grid(row=3, column=0, columnspan=8, sticky='w', pady=(4, 0))
        top.columnconfigure(1, weight=1)
        top.columnconfigure(5, weight=1)

        options = ttk.Frame(self.root, padding=(8, 0, 8, 6))
        options.pack(fill='x')
        ttk.Checkbutton(
            options,
            text='Linux shortcuts: left Ctrl → macOS Command (right Ctrl stays real Control)',
            variable=self.linux_shortcuts_var,
            command=self._shortcut_mode_changed,
        ).pack(side='left')
        ttk.Checkbutton(options, text='Remember this Mac (fingerprint + password in system keyring)',
                        variable=self.remember_credentials_var).pack(side='left', padx=(12, 0))
        ttk.Button(options, text='Clipboard Linux → Mac', command=self.push_clipboard).pack(side='right', padx=(4, 0))
        ttk.Button(options, text='Clipboard Mac → Linux', command=self.pull_clipboard).pack(side='right')

        ttk.Label(self.root, textvariable=self.status_var, padding=(8, 0, 8, 6)).pack(fill='x')

        pane = ttk.Panedwindow(self.root, orient='vertical')
        pane.pack(fill='both', expand=True, padx=8, pady=(0, 8))

        desk_frame = ttk.Frame(pane)
        pane.add(desk_frame, weight=5)
        self.canvas = tk.Canvas(desk_frame, bg='black', highlightthickness=0, takefocus=True)
        self.canvas.pack(fill='both', expand=True)
        self.canvas.bind('<Configure>', lambda e: self._redraw())
        self.canvas.bind('<Motion>', self._mouse_move)
        for b in (1, 2, 3):
            self.canvas.bind(f'<ButtonPress-{b}>', lambda e, b=b: self._mouse_button(b, True, e))
            self.canvas.bind(f'<ButtonRelease-{b}>', lambda e, b=b: self._mouse_button(b, False, e))
        self.canvas.bind('<Button-4>', lambda e: self._send_scroll(0, 3))
        self.canvas.bind('<Button-5>', lambda e: self._send_scroll(0, -3))
        self.canvas.bind('<MouseWheel>', lambda e: self._send_scroll(0, int(e.delta / 120) * 3))
        self.canvas.bind('<KeyPress>', self._key_press)
        self.canvas.bind('<KeyRelease>', self._key_release)
        self.canvas.bind('<Button-1>', lambda e: self.canvas.focus_set(), add='+')
        self.canvas.bind('<FocusOut>', lambda e: self._release_local_modifiers())

        lower_tabs = ttk.Notebook(pane)
        pane.add(lower_tabs, weight=2)

        files = ttk.Frame(lower_tabs, padding=4)
        lower_tabs.add(files, text='Files')

        nav = ttk.Frame(files)
        nav.pack(fill='x', pady=(0, 4))
        ttk.Button(nav, text='↑', width=3, command=self.remote_up).pack(side='left')
        ttk.Button(nav, text='Refresh', command=self.refresh_files).pack(side='left', padx=4)
        ttk.Label(nav, text='Mac: ~/ZorinMac-Share').pack(side='left', padx=(8, 4))
        ttk.Entry(nav, textvariable=self.remote_path_var, state='readonly').pack(side='left', fill='x', expand=True)

        file_area = ttk.Frame(files)
        file_area.pack(fill='both', expand=True)
        self.file_list = ttk.Treeview(file_area, columns=('kind', 'size', 'mtime'), show='tree headings', height=8)
        self.file_list.heading('#0', text='Name')
        self.file_list.heading('kind', text='Type')
        self.file_list.heading('size', text='Size')
        self.file_list.heading('mtime', text='Modified')
        self.file_list.column('#0', width=450, anchor='w')
        self.file_list.column('kind', width=80, anchor='w')
        self.file_list.column('size', width=110, anchor='e')
        self.file_list.column('mtime', width=160, anchor='w')
        self.file_list.pack(side='left', fill='both', expand=True)
        self.file_list.bind('<Double-1>', self._file_double_click)

        btns = ttk.Frame(file_area, padding=(8, 0))
        btns.pack(side='right', fill='y')
        ttk.Button(btns, text='Upload file(s) → Mac', command=self.upload_files).pack(fill='x', pady=2)
        ttk.Button(btns, text='Upload folder → Mac', command=self.upload_folder).pack(fill='x', pady=2)
        ttk.Button(btns, text='Download ← Mac', command=self.download_selected).pack(fill='x', pady=2)
        ttk.Button(btns, text='New folder on Mac', command=self.create_remote_folder).pack(fill='x', pady=2)
        ttk.Label(
            btns,
            text='All file access is restricted to\n~/ZorinMac-Share.\nSymlinks are skipped.',
            justify='left',
        ).pack(pady=(10, 0))

        logs = ttk.Frame(lower_tabs, padding=6)
        lower_tabs.add(logs, text='Logs')
        log_toolbar = ttk.Frame(logs)
        log_toolbar.pack(fill='x', pady=(0, 6))
        ttk.Button(log_toolbar, text='Copy all', command=self.copy_logs).pack(side='left')
        ttk.Button(log_toolbar, text='Save…', command=self.save_logs).pack(side='left', padx=(6, 0))
        ttk.Button(log_toolbar, text='Clear', command=self.clear_logs).pack(side='left', padx=(6, 0))
        ttk.Label(
            log_toolbar,
            text='Passwords are never written to this log.',
        ).pack(side='right')
        self.log_text = ScrolledText(logs, wrap='word', height=10, state='disabled')
        self.log_text.pack(fill='both', expand=True)

    def _log(self, level: str, message: str) -> None:
        timestamp = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S.%f')[:-3]
        clean = str(message).replace('\r', '\\r')
        self.ui_queue.put(('log', f'{timestamp} [{level}] {clean}'))

    def _append_log(self, line: str) -> None:
        self.log_text.configure(state='normal')
        self.log_text.insert('end', line.rstrip() + '\n')
        self.log_text.see('end')
        self.log_text.configure(state='disabled')

    def clear_logs(self) -> None:
        self.log_text.configure(state='normal')
        self.log_text.delete('1.0', 'end')
        self.log_text.configure(state='disabled')

    def copy_logs(self) -> None:
        text = self.log_text.get('1.0', 'end-1c')
        self.root.clipboard_clear()
        self.root.clipboard_append(text)
        self.status_var.set('Logs copied to clipboard.')

    def save_logs(self) -> None:
        target = filedialog.asksaveasfilename(
            title='Save ZorinMacBridge client logs',
            defaultextension='.log',
            filetypes=[('Log files', '*.log'), ('Text files', '*.txt'), ('All files', '*.*')],
            initialfile='zorinmacbridge-client.log',
        )
        if not target:
            return
        Path(target).write_text(self.log_text.get('1.0', 'end-1c'), encoding='utf-8')
        self.status_var.set(f'Logs saved to {target}')

    def show_window(self) -> None:
        self.root.deiconify()
        self.root.lift()
        try:
            self.root.focus_force()
        except Exception:
            pass

    def _machine_selected(self, _event=None) -> None:
        server = self.discovered_by_label.get(self.machine_var.get())
        if server is None:
            return
        self.ip_var.set(server.ip)
        self.port_var.set(str(server.port))
        self.selected_server_id = server.server_id or f'{server.ip}:{server.port}'
        self.selected_server_name = server.name
        record = client_record(self.selected_server_id)
        saved_fp = str(record.get('fingerprint', '')).strip()
        if saved_fp:
            self.fp_var.set(saved_fp)
        saved_password = get_saved_password(self.selected_server_id)
        if saved_password:
            self.password_var.set(saved_password)
        trust = 'Saved identity loaded.' if saved_fp else 'Verify the TLS fingerprint once.'
        self.status_var.set(f'Selected {server.name} at {server.ip}:{server.port}. {trust}')
        self._log('INFO', f'Discovery selection: {server.name} at {server.ip}:{server.port}; server_id={self.selected_server_id}')

    def discover_macs(self) -> None:
        self.status_var.set('Searching the local network for running Macs…')
        self._log('INFO', 'Starting LAN discovery scan.')

        def worker() -> None:
            try:
                servers = discover_servers(2.5)
                self.ui_queue.put(('discovery', servers))
            except Exception as exc:
                self._log('ERROR', f'LAN discovery failed: {type(exc).__name__}: {exc}')
                self.ui_queue.put(('discovery_error', str(exc)))

        threading.Thread(target=worker, daemon=True).start()

    def check_updates(self) -> None:
        self.status_var.set('Checking GitHub Releases for updates…')
        self._log('INFO', 'Manual update check requested.')

        def worker() -> None:
            try:
                self.ui_queue.put(('update', check_for_updates()))
            except Exception as exc:
                self._log('ERROR', f'Update check failed: {type(exc).__name__}: {exc}')
                self.ui_queue.put(('update_error', str(exc)))

        threading.Thread(target=worker, daemon=True).start()

    def _install_update(self, info) -> None:
        self.status_var.set(f'Installing ZorinMacBridge {info.latest}…')
        self._log('INFO', f'User approved update {info.current} → {info.latest}.')

        def progress(message: str) -> None:
            self._log('INFO', f'Updater: {message}')
            self.ui_queue.put(('update_progress', message))

        def worker() -> None:
            try:
                result = install_update(info, progress=progress)
                self.ui_queue.put(('update_installed', result))
            except Exception as exc:
                self._log('ERROR', f'Update install failed: {type(exc).__name__}: {exc}')
                self.ui_queue.put(('update_install_error', str(exc)))

        threading.Thread(target=worker, daemon=True).start()

    def _restart_after_update(self) -> None:
        if not getattr(sys, 'frozen', False):
            messagebox.showinfo(
                'Restart required',
                'The update was installed system-wide. This copy is running from source, so close it and launch ZorinMacBridge Client from the application menu.',
            )
            return
        exe = sys.executable
        pid = os.getpid()
        command = f'while kill -0 {pid} 2>/dev/null; do sleep 0.2; done; exec "$1"'
        subprocess.Popen(
            ['/bin/sh', '-c', command, 'zorinmacbridge-restart', exe],
            start_new_session=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        self.on_close()

    def show_about(self) -> None:
        from updates import current_version
        messagebox.showinfo(
            'About ZorinMacBridge',
            f'ZorinMacBridge Client {current_version()}\n\n'
            'LAN-first Linux client for a manually started macOS development server.\n'
            'No background update checks are performed.',
        )

    def _connection_params(self):
        ip = self.ip_var.get().strip()
        if not is_lan_ip(ip):
            raise ValueError('Only a literal LAN/loopback/link-local IP address is allowed. Hostnames and public IPs are blocked.')
        port = int(self.port_var.get().strip())
        if not (1 <= port <= 65535):
            raise ValueError('Invalid port')
        password = self.password_var.get()
        expected_fp = normalize_fp(self.fp_var.get())
        if len(expected_fp) != 64:
            raise ValueError('Paste the full server SHA-256 fingerprint (64 hex characters; colons optional).')
        return ip, port, password, expected_fp

    def _secure_connect(self, role: str) -> ssl.SSLSocket:
        ip, port, password, expected_fp = self._connection_params()
        self._log('INFO', f'Opening {role} connection to {ip}:{port}')
        parsed_ip = ipaddress.ip_address(ip.split('%', 1)[0])
        family = socket.AF_INET6 if parsed_ip.version == 6 else socket.AF_INET
        raw = socket.socket(family, socket.SOCK_STREAM)
        raw.settimeout(8)
        self._log('DEBUG', 'Connecting TCP socket…')
        raw.connect((ip, port))
        self._log('DEBUG', 'TCP connection established; starting TLS handshake.')
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        ctx.minimum_version = ssl.TLSVersion.TLSv1_2
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        sock = ctx.wrap_socket(raw, server_hostname='ZorinMac-Bridge')
        self._log('INFO', f'TLS established: {sock.version()} / {sock.cipher()[0] if sock.cipher() else "unknown cipher"}')
        cert = sock.getpeercert(binary_form=True)
        actual_fp = hashlib.sha256(cert).hexdigest().upper()
        self._log('DEBUG', f'Server TLS fingerprint: {format_fp(actual_fp)}')
        if not hmac.compare_digest(actual_fp, expected_fp.upper()):
            sock.close()
            self._log('ERROR', 'TLS fingerprint mismatch. Connection rejected.')
            raise ConnectionError(
                f'TLS fingerprint DOES NOT MATCH.\nExpected: {format_fp(expected_fp)}\nReceived: {format_fp(actual_fp)}'
            )
        self._log('INFO', 'TLS fingerprint verified.')
        self._log('DEBUG', f'Sending authentication request for role={role!r}.')
        sock.sendall(pack_json(AUTH, {'password': password, 'role': role}))
        kind, payload = recv_one_blocking(sock)
        if kind == AUTH_FAIL:
            sock.close()
            self._log('ERROR', f'Authentication failed for role={role!r}: {payload.decode("utf-8", "replace")}')
            raise PermissionError(payload.decode('utf-8', 'replace'))
        if kind != AUTH_OK:
            sock.close()
            self._log('ERROR', f'Unexpected authentication response packet type: {kind}')
            raise ConnectionError('Invalid server response during authentication')
        self._log('INFO', f'Authentication successful for role={role!r}.')
        return sock

    def connect(self) -> None:
        if self.connected or (self.net_thread is not None and self.net_thread.is_alive()):
            return
        try:
            ip, port, _password, expected_fp = self._connection_params()
        except Exception as exc:
            self._log('ERROR', f'Connection parameters rejected: {exc}')
            messagebox.showerror('Connection', str(exc))
            return
        self.disconnect_requested = False
        self.remember_credentials_active = bool(self.remember_credentials_var.get())
        if not self.selected_server_id:
            self.selected_server_id = f'{ip}:{port}'
            self.selected_server_name = ip
        self.stop_event.clear()
        self.status_var.set('Connecting…')
        self._log('INFO', f'Connect requested: {ip}:{port}; expected fingerprint {format_fp(expected_fp)}')
        self.net_thread = threading.Thread(target=self._network_loop, daemon=True)
        self.net_thread.start()

    def disconnect(self) -> None:
        self._release_local_modifiers()
        self.disconnect_requested = True
        self.stop_event.set()
        self.connected = False
        self.status_var.set('Disconnected')
        self._log('INFO', 'Disconnect requested by user.')

    def _network_loop(self) -> None:
        sock = None
        disconnect_reason = ''
        try:
            sock = self._secure_connect('desktop')
            if self.remember_credentials_active:
                try:
                    ip, port, password, expected_fp = self._connection_params()
                    save_client_record(self.selected_server_id, name=self.selected_server_name or ip,
                                       ip=ip, port=port, fingerprint=format_fp(expected_fp))
                    if password and not save_password_secret(self.selected_server_id, password):
                        self._log('WARNING', 'Could not store password in the Linux desktop keyring; it will not be remembered.')
                    else:
                        self._log('INFO', 'Mac identity remembered; password stored in the Linux system keyring when available.')
                except Exception as exc:
                    self._log('WARNING', f'Could not remember credentials: {exc}')
            sock.settimeout(0.05)
            reader = PacketReader()
            last_server_error = ''
            self.ui_queue.put(('connected',))
            self.video_thread = threading.Thread(target=self._video_loop, daemon=True)
            self.video_thread.start()
            while not self.stop_event.is_set():
                sent = 0
                while sent < 60:
                    try:
                        packet = self.outgoing.get_nowait()
                    except queue.Empty:
                        break
                    sock.sendall(packet)
                    sent += 1
                try:
                    data = sock.recv(65536)
                    if not data:
                        if last_server_error:
                            raise ConnectionError(f'Server closed after reporting: {last_server_error}')
                        raise ConnectionError('Server closed the connection')
                    for kind, payload in reader.feed(data):
                        if kind == CLIPBOARD_DATA:
                            self.ui_queue.put(('clipboard', payload.decode('utf-8', 'replace')))
                        elif kind == ERROR:
                            server_error = payload.decode('utf-8', 'replace')
                            last_server_error = server_error
                            self._log('ERROR', f'Server reported: {server_error}')
                            self.ui_queue.put(('error', server_error))
                except (socket.timeout, ssl.SSLWantReadError):
                    pass
        except Exception as exc:
            disconnect_reason = f'{type(exc).__name__}: {exc}'
            self._log('ERROR', f'Desktop connection failed: {disconnect_reason}')
            self._log('DEBUG', traceback.format_exc().rstrip())
            self.ui_queue.put(('error', str(exc)))
        finally:
            try:
                if sock is not None:
                    sock.close()
            except Exception:
                pass
            if self.disconnect_requested and not disconnect_reason:
                disconnect_reason = 'Disconnected by user'
            elif not disconnect_reason:
                disconnect_reason = 'Desktop session ended'
            self.ui_queue.put(('disconnected', disconnect_reason))

    def _queue_video_image(self, image: Image.Image) -> None:
        try:
            self.video_frames.put_nowait(image)
        except queue.Full:
            try:
                self.video_frames.get_nowait()
            except queue.Empty:
                pass
            try:
                self.video_frames.put_nowait(image)
            except queue.Full:
                pass

    def _video_loop(self) -> None:
        sock = None
        decoder = None
        frames = 0
        try:
            self._log('INFO', 'Opening dedicated H.264 video connection.')
            sock = self._secure_connect('video')
            sock.settimeout(5.0)
            reader = PacketReader()
            decoder = av.CodecContext.create('h264', 'r')
            self._log('INFO', 'H.264 decoder initialized (PyAV/FFmpeg).')
            while not self.stop_event.is_set():
                data = sock.recv(262144)
                if not data:
                    raise ConnectionError('Video connection closed by server')
                for kind, payload in reader.feed(data):
                    if kind == VIDEO_H264:
                        for packet in decoder.parse(payload):
                            for frame in decoder.decode(packet):
                                img = frame.to_image().convert('RGB')
                                self.remote_image_size = (img.width, img.height)
                                self._queue_video_image(img)
                                frames += 1
                                if frames == 1:
                                    self._log('INFO', f'First H.264 frame decoded: {img.width}x{img.height}.')
                    elif kind == ERROR:
                        message = payload.decode('utf-8', 'replace')
                        raise RuntimeError('Video server error: ' + message)
        except Exception as exc:
            if not self.stop_event.is_set():
                reason = f'{type(exc).__name__}: {exc}'
                self._log('ERROR', f'Video stream failed: {reason}')
                self.ui_queue.put(('error', f'Video stream failed: {exc}'))
                self.stop_event.set()
        finally:
            if decoder is not None:
                try:
                    for frame in decoder.decode(None):
                        img = frame.to_image().convert('RGB')
                        self._queue_video_image(img)
                except Exception:
                    pass
            try:
                if sock is not None:
                    sock.close()
            except Exception:
                pass
            self._log('INFO', 'H.264 video connection closed.')

    def _process_ui_queue(self) -> None:
        try:
            while True:
                item = self.ui_queue.get_nowait()
                if item[0] == 'log':
                    self._append_log(item[1])
                elif item[0] == 'connected':
                    self.connected = True
                    self.status_var.set('Connected — click the desktop image to control the Mac')
                    self._log('INFO', 'Desktop session connected.')
                    self.refresh_files()
                elif item[0] == 'disconnected':
                    self.connected = False
                    reason = item[1] if len(item) > 1 else 'Session ended'
                    if reason == 'Disconnected by user':
                        self.status_var.set('Disconnected')
                    else:
                        self.status_var.set(f'Disconnected — {reason}')
                    self._log('INFO', f'Desktop session disconnected: {reason}')
                elif item[0] == 'error':
                    self.status_var.set(f'Error: {item[1]}')
                    self._log('ERROR', item[1])
                elif item[0] == 'files':
                    self._show_files(item[1])
                elif item[0] == 'info':
                    self.status_var.set(item[1])
                elif item[0] == 'clipboard':
                    self._set_local_clipboard(item[1])
                elif item[0] == 'discovery':
                    servers = item[1]
                    self.discovered_by_label = {server.label: server for server in servers}
                    values = list(self.discovered_by_label)
                    self.machine_box.configure(values=values)
                    if servers:
                        self.machine_var.set(values[0])
                        self._machine_selected()
                        self.status_var.set(f'Found {len(servers)} running Mac server(s) on the LAN.')
                        self._log('INFO', f'LAN discovery found {len(servers)} server(s): ' + ', '.join(s.label for s in servers))
                    else:
                        self.machine_var.set('')
                        self.status_var.set('No running ZorinMacBridge server found on the LAN.')
                        self._log('INFO', 'LAN discovery found no running servers.')
                elif item[0] == 'discovery_error':
                    self.status_var.set(f'LAN discovery error: {item[1]}')
                    self._log('ERROR', f'LAN discovery error: {item[1]}')
                elif item[0] == 'update':
                    info = item[1]
                    self._log('INFO', f'Update check completed: installed={info.current}, latest={info.latest}, available={info.available}')
                    if info.available:
                        if messagebox.askyesno(
                            'ZorinMacBridge update',
                            f'Version {info.latest} is available.\nInstalled: {info.current}\n\nDownload, verify, and install it now?',
                        ):
                            self._install_update(info)
                        else:
                            self.status_var.set('Update cancelled.')
                    else:
                        messagebox.showinfo('ZorinMacBridge update', f'You are up to date (version {info.current}).')
                        self.status_var.set('Update check completed.')
                elif item[0] == 'update_progress':
                    self.status_var.set(item[1])
                elif item[0] == 'update_installed':
                    result = item[1]
                    self.status_var.set(f'Updated to {result.installed}. Restart recommended.')
                    self._log('INFO', f'Update installed successfully: {result.current} → {result.installed}')
                    if messagebox.askyesno(
                        'Update installed',
                        f'ZorinMacBridge {result.installed} was installed successfully.\n\nRestart the client now?',
                    ):
                        self._restart_after_update()
                elif item[0] == 'update_install_error':
                    self.status_var.set('Update installation failed.')
                    messagebox.showerror(
                        'Update installation failed',
                        'The update was not installed.\n\n' + item[1],
                    )
                elif item[0] == 'update_error':
                    self.status_var.set('Update check failed.')
                    messagebox.showerror(
                        'Update check failed',
                        'The manual update check could not reach GitHub Releases.\n\n' + item[1],
                    )
        except queue.Empty:
            pass

        latest_image = None
        try:
            while True:
                latest_image = self.video_frames.get_nowait()
        except queue.Empty:
            pass
        if latest_image is not None:
            self._last_image = latest_image
            self._redraw()
        self.root.after(16, self._process_ui_queue)

    def _redraw(self) -> None:
        img = getattr(self, '_last_image', None)
        if img is None:
            return
        cw = max(1, self.canvas.winfo_width())
        ch = max(1, self.canvas.winfo_height())
        scale = min(cw / img.width, ch / img.height)
        dw = max(1, int(img.width * scale))
        dh = max(1, int(img.height * scale))
        x = (cw - dw) // 2
        y = (ch - dh) // 2
        disp = img.resize((dw, dh), Image.Resampling.BILINEAR) if (dw, dh) != img.size else img
        photo = ImageTk.PhotoImage(disp)
        self.canvas.delete('all')
        self.canvas.create_image(x, y, image=photo, anchor='nw')
        self.last_photo = photo
        self.display_rect = (x, y, dw, dh)

    def _norm_xy(self, event) -> tuple[float, float] | None:
        x, y, w, h = self.display_rect
        if w <= 0 or h <= 0:
            return None
        if event.x < x or event.y < y or event.x > x + w or event.y > y + h:
            return None
        return ((event.x - x) / w, (event.y - y) / h)

    def _enqueue(self, packet: bytes) -> None:
        if not self.connected:
            return
        try:
            self.outgoing.put_nowait(packet)
        except queue.Full:
            pass

    def _mouse_move(self, event) -> None:
        pos = self._norm_xy(event)
        if pos:
            self._enqueue(pack_packet(MOUSE_MOVE, struct.pack('!ff', *pos)))

    def _mouse_button(self, button: int, down: bool, event) -> None:
        self.canvas.focus_set()
        pos = self._norm_xy(event)
        if pos:
            self._enqueue(pack_packet(MOUSE_BUTTON, struct.pack('!BBff', button, int(down), pos[0], pos[1])))

    def _send_scroll(self, dx: int, dy: int) -> None:
        self._enqueue(pack_packet(SCROLL, struct.pack('!ii', dx, dy)))

    def _map_keysym(self, keysym: str) -> str:
        if self.linux_shortcuts_var.get() and keysym == 'Control_L':
            return 'Meta_L'
        if keysym in {'Super_L', 'Super_R'}:
            return 'Meta_L' if keysym.endswith('_L') else 'Meta_R'
        return keysym

    def _shortcut_mode_changed(self) -> None:
        self._release_local_modifiers()

    def _command_down(self) -> bool:
        return 'Meta_L' in self.modifiers_down or 'Meta_R' in self.modifiers_down

    def _has_non_text_modifier(self) -> bool:
        return any(k.startswith(('Meta_', 'Control_', 'Alt_')) for k in self.modifiers_down)

    def _key_press(self, event) -> str:
        keysym = self._map_keysym(event.keysym)
        if keysym in MODIFIER_KEYS or keysym in {'Meta_L', 'Meta_R'}:
            if keysym not in self.modifiers_down:
                self.modifiers_down.add(keysym)
                self._enqueue(pack_json(KEY, {'keysym': keysym, 'down': True}))
            return 'break'

        key_lower = keysym.lower() if len(keysym) == 1 else keysym
        if self._command_down() and key_lower == 'v':
            self._push_clipboard_silent()

        if keysym in SPECIAL_KEYS or self._has_non_text_modifier():
            self._enqueue(pack_json(KEY, {'keysym': keysym, 'down': True}))
            self.keys_down.add(keysym)
            return 'break'

        if event.char and event.char.isprintable():
            self._enqueue(pack_packet(TEXT, event.char.encode('utf-8')))
        else:
            self._enqueue(pack_json(KEY, {'keysym': keysym, 'down': True}))
            self.keys_down.add(keysym)
        return 'break'

    def _key_release(self, event) -> str:
        keysym = self._map_keysym(event.keysym)
        key_lower = keysym.lower() if len(keysym) == 1 else keysym
        command_was_down = self._command_down()

        if keysym in self.modifiers_down:
            self.modifiers_down.discard(keysym)
            self._enqueue(pack_json(KEY, {'keysym': keysym, 'down': False}))
        elif keysym in self.keys_down:
            self.keys_down.discard(keysym)
            self._enqueue(pack_json(KEY, {'keysym': keysym, 'down': False}))

        if command_was_down and key_lower in {'c', 'x'}:
            self.root.after(120, self._request_remote_clipboard)
        return 'break'

    def _release_local_modifiers(self) -> None:
        for key in list(self.keys_down):
            self._enqueue(pack_json(KEY, {'keysym': key, 'down': False}))
        self.keys_down.clear()
        for key in list(self.modifiers_down):
            self._enqueue(pack_json(KEY, {'keysym': key, 'down': False}))
        self.modifiers_down.clear()

    def _get_local_clipboard(self) -> str | None:
        try:
            text = self.root.clipboard_get()
        except tk.TclError:
            return None
        raw = text.encode('utf-8')
        if len(raw) > MAX_CLIPBOARD:
            self.status_var.set('Text clipboard is too large (>2 MiB).')
            return None
        return text

    def _set_local_clipboard(self, text: str) -> None:
        try:
            self.root.clipboard_clear()
            self.root.clipboard_append(text)
            self.status_var.set('Text clipboard copied Mac → Linux')
        except tk.TclError as exc:
            self.status_var.set(f'Linux clipboard error: {exc}')

    def _push_clipboard_silent(self) -> None:
        text = self._get_local_clipboard()
        if text is not None:
            self._enqueue(pack_packet(CLIPBOARD_SET, text.encode('utf-8')))

    def _request_remote_clipboard(self) -> None:
        self._enqueue(pack_packet(CLIPBOARD_GET))

    def push_clipboard(self) -> None:
        if not self.connected:
            return
        text = self._get_local_clipboard()
        if text is None:
            self.status_var.set('No text found in the Linux clipboard.')
            return
        self._enqueue(pack_packet(CLIPBOARD_SET, text.encode('utf-8')))
        self.status_var.set('Text clipboard sent Linux → Mac')

    def pull_clipboard(self) -> None:
        if self.connected:
            self._request_remote_clipboard()

    # ---------- Remote file browser ----------

    def refresh_files(self) -> None:
        if not self.connected:
            return
        threading.Thread(target=self._refresh_files_worker, args=(self.remote_path,), daemon=True).start()

    def _refresh_files_worker(self, remote_path: str) -> None:
        sock = None
        try:
            sock = self._secure_connect('file')
            reader = PacketReader()
            sock.sendall(pack_json(LIST_REQ, {'path': remote_path}))
            kind, payload = recv_one_blocking(sock, reader)
            if kind == ERROR:
                raise RuntimeError(payload.decode('utf-8', 'replace'))
            if kind != LIST_RESP:
                raise RuntimeError('Invalid file-list response')
            self.ui_queue.put(('files', unpack_json(payload)))
        except Exception as exc:
            self._log('ERROR', f'File list failed: {type(exc).__name__}: {exc}')
            self.ui_queue.put(('error', f'Files: {exc}'))
        finally:
            if sock:
                sock.close()

    def _show_files(self, response: dict) -> None:
        self.remote_path = str(response.get('path', ''))
        self.remote_path_var.set('/' + self.remote_path if self.remote_path else '/')
        self.file_entries.clear()
        for item in self.file_list.get_children():
            self.file_list.delete(item)
        for i, entry in enumerate(response.get('items', [])):
            iid = f'e{i}'
            self.file_entries[iid] = entry
            kind = 'folder' if entry.get('kind') == 'dir' else 'file'
            size = '' if kind == 'folder' else human_size(int(entry.get('size', 0)))
            ts = datetime.datetime.fromtimestamp(int(entry.get('mtime', 0))).strftime('%Y-%m-%d %H:%M')
            prefix = '📁 ' if kind == 'folder' else ''
            self.file_list.insert('', 'end', iid=iid, text=prefix + entry['name'], values=(kind, size, ts))

    def remote_up(self) -> None:
        if not self.remote_path:
            return
        self.remote_path = posixpath.dirname(self.remote_path)
        self.refresh_files()

    def _file_double_click(self, _event=None) -> None:
        sel = self.file_list.selection()
        if not sel:
            return
        entry = self.file_entries.get(sel[0])
        if entry and entry.get('kind') == 'dir':
            self.remote_path = remote_join(self.remote_path, entry['name'])
            self.refresh_files()

    def _expect(self, sock, reader, expected_kind: int):
        kind, payload = recv_one_blocking(sock, reader)
        if kind == ERROR:
            raise RuntimeError(payload.decode('utf-8', 'replace'))
        if kind != expected_kind:
            raise RuntimeError(f'Unexpected packet: {kind}, expected {expected_kind}')
        return payload

    def _mkdir_remote(self, sock, reader, path: str) -> None:
        sock.sendall(pack_json(MKDIR_REQ, {'path': path}))
        self._expect(sock, reader, MKDIR_OK)

    def _upload_one(self, sock, reader, local: Path, remote: str) -> None:
        size = local.stat().st_size
        self.ui_queue.put(('info', f'Uploading {local.name} ({human_size(size)})…'))
        sock.sendall(pack_json(UPLOAD_BEGIN, {'path': remote, 'size': size}))
        sent = 0
        with local.open('rb') as f:
            while True:
                block = f.read(CHUNK)
                if not block:
                    break
                sock.sendall(pack_packet(UPLOAD_CHUNK, block))
                sent += len(block)
                if size:
                    self.ui_queue.put(('info', f'Uploading {local.name}: {sent * 100 // size}%'))
        sock.sendall(pack_packet(UPLOAD_END))
        self._expect(sock, reader, UPLOAD_END)

    def upload_files(self) -> None:
        if not self.connected:
            return
        paths = filedialog.askopenfilenames(title='Select Linux files to upload to the Mac')
        if not paths:
            return
        threading.Thread(target=self._upload_files_worker, args=([Path(p) for p in paths], self.remote_path), daemon=True).start()

    def _upload_files_worker(self, paths: list[Path], remote_dir: str) -> None:
        sock = None
        try:
            self._log('INFO', f'Uploading {len(paths)} selected file(s) to Mac path /{remote_dir}.')
            sock = self._secure_connect('file')
            reader = PacketReader()
            for path in paths:
                if path.is_symlink() or not path.is_file():
                    continue
                self._upload_one(sock, reader, path, remote_join(remote_dir, path.name))
            self.ui_queue.put(('info', f'Uploaded {len(paths)} file(s).'))
            self._log('INFO', f'Upload completed: {len(paths)} selected file(s).')
            self._refresh_files_worker(remote_dir)
        except Exception as exc:
            self._log('ERROR', f'Upload failed: {type(exc).__name__}: {exc}')
            self.ui_queue.put(('error', f'Upload: {exc}'))
        finally:
            if sock:
                sock.close()

    def upload_folder(self) -> None:
        if not self.connected:
            return
        folder = filedialog.askdirectory(title='Select a Linux folder to upload to the Mac')
        if not folder:
            return
        threading.Thread(target=self._upload_folder_worker, args=(Path(folder), self.remote_path), daemon=True).start()

    def _upload_folder_worker(self, root: Path, remote_dir: str) -> None:
        sock = None
        try:
            self._log('INFO', f'Uploading folder {root} to Mac path /{remote_dir}.')
            if root.is_symlink() or not root.is_dir():
                raise ValueError('Invalid folder')
            sock = self._secure_connect('file')
            reader = PacketReader()
            base_remote = remote_join(remote_dir, root.name)
            self._mkdir_remote(sock, reader, base_remote)
            files_sent = 0
            for current, dirs, files in os.walk(root, followlinks=False):
                current_path = Path(current)
                dirs[:] = [d for d in dirs if not (current_path / d).is_symlink()]
                rel = current_path.relative_to(root)
                rel_posix = '' if str(rel) == '.' else rel.as_posix()
                current_remote = base_remote if not rel_posix else remote_join(base_remote, rel_posix)
                self._mkdir_remote(sock, reader, current_remote)
                for d in dirs:
                    self._mkdir_remote(sock, reader, remote_join(current_remote, d))
                for name in files:
                    local = current_path / name
                    if local.is_symlink() or not local.is_file():
                        continue
                    self._upload_one(sock, reader, local, remote_join(current_remote, name))
                    files_sent += 1
            self.ui_queue.put(('info', f'Folder {root.name}: uploaded {files_sent} file(s).'))
            self._log('INFO', f'Folder upload completed: {root.name} ({files_sent} file(s)).')
            self._refresh_files_worker(remote_dir)
        except Exception as exc:
            self._log('ERROR', f'Folder upload failed: {type(exc).__name__}: {exc}')
            self.ui_queue.put(('error', f'Folder upload: {exc}'))
        finally:
            if sock:
                sock.close()

    def create_remote_folder(self) -> None:
        if not self.connected:
            return
        name = simpledialog.askstring('New folder', 'Folder name on Mac:')
        if not name:
            return
        if '/' in name or '\\' in name or name in {'.', '..'}:
            messagebox.showerror('New folder', 'The name cannot contain / or \\ and cannot be . or ..')
            return
        threading.Thread(target=self._create_remote_folder_worker, args=(remote_join(self.remote_path, name),), daemon=True).start()

    def _create_remote_folder_worker(self, path: str) -> None:
        sock = None
        try:
            self._log('INFO', f'Creating remote folder /{path}.')
            sock = self._secure_connect('file')
            reader = PacketReader()
            self._mkdir_remote(sock, reader, path)
            self._refresh_files_worker(self.remote_path)
        except Exception as exc:
            self._log('ERROR', f'Create remote folder failed: {type(exc).__name__}: {exc}')
            self.ui_queue.put(('error', f'New folder: {exc}'))
        finally:
            if sock:
                sock.close()

    def download_selected(self) -> None:
        if not self.connected:
            return
        sel = self.file_list.selection()
        if not sel:
            messagebox.showinfo('Download', 'Select a file or folder.')
            return
        entry = self.file_entries.get(sel[0])
        if not entry:
            return
        remote = remote_join(self.remote_path, entry['name'])
        if entry.get('kind') == 'file':
            dest = filedialog.asksaveasfilename(initialfile=entry['name'])
            if not dest:
                return
            threading.Thread(target=self._download_file_worker, args=(remote, Path(dest)), daemon=True).start()
        else:
            parent = filedialog.askdirectory(title='Select a destination folder on Linux')
            if not parent:
                return
            local_root = Path(parent) / entry['name']
            threading.Thread(target=self._download_folder_worker, args=(remote, local_root), daemon=True).start()

    def _download_one(self, sock, reader, remote: str, dest: Path) -> None:
        sock.sendall(pack_json(DOWNLOAD_REQ, {'path': remote}))
        payload = self._expect(sock, reader, DOWNLOAD_BEGIN)
        meta = unpack_json(payload)
        size = int(meta['size'])
        dest.parent.mkdir(parents=True, exist_ok=True)
        tmp = dest.with_name(dest.name + '.part')
        received = 0
        try:
            with tmp.open('wb') as f:
                while True:
                    kind, payload = recv_one_blocking(sock, reader)
                    if kind == DOWNLOAD_CHUNK:
                        f.write(payload)
                        received += len(payload)
                        if size:
                            self.ui_queue.put(('info', f'Download {dest.name}: {received * 100 // size}%'))
                    elif kind == DOWNLOAD_END:
                        break
                    elif kind == ERROR:
                        raise RuntimeError(payload.decode('utf-8', 'replace'))
                    else:
                        raise RuntimeError(f'Unexpected packet during download: {kind}')
            if received != size:
                raise RuntimeError(f'Downloaded file size mismatch: {received}/{size}')
            os.replace(tmp, dest)
        except Exception:
            tmp.unlink(missing_ok=True)
            raise

    def _download_file_worker(self, remote: str, dest: Path) -> None:
        sock = None
        try:
            self._log('INFO', f'Downloading Mac file /{remote} to {dest}.')
            sock = self._secure_connect('file')
            reader = PacketReader()
            self._download_one(sock, reader, remote, dest)
            self.ui_queue.put(('info', f'Downloaded: {dest}'))
            self._log('INFO', f'File download completed: {dest}.')
        except Exception as exc:
            self._log('ERROR', f'Download failed: {type(exc).__name__}: {exc}')
            self.ui_queue.put(('error', f'Download: {exc}'))
        finally:
            if sock:
                sock.close()

    def _remote_list(self, sock, reader, remote_dir: str) -> list[dict]:
        sock.sendall(pack_json(LIST_REQ, {'path': remote_dir}))
        payload = self._expect(sock, reader, LIST_RESP)
        return list(unpack_json(payload).get('items', []))

    def _download_tree(self, sock, reader, remote_dir: str, local_dir: Path) -> int:
        local_dir.mkdir(parents=True, exist_ok=True)
        count = 0
        for entry in self._remote_list(sock, reader, remote_dir):
            name = entry['name']
            remote = remote_join(remote_dir, name)
            local = local_dir / name
            if entry.get('kind') == 'dir':
                count += self._download_tree(sock, reader, remote, local)
            else:
                self._download_one(sock, reader, remote, local)
                count += 1
        return count

    def _download_folder_worker(self, remote: str, local_root: Path) -> None:
        sock = None
        try:
            self._log('INFO', f'Downloading Mac folder /{remote} to {local_root}.')
            sock = self._secure_connect('file')
            reader = PacketReader()
            count = self._download_tree(sock, reader, remote, local_root)
            self.ui_queue.put(('info', f'Downloaded folder: {local_root} ({count} file(s))'))
            self._log('INFO', f'Folder download completed: {local_root} ({count} file(s)).')
        except Exception as exc:
            self._log('ERROR', f'Folder download failed: {type(exc).__name__}: {exc}')
            self.ui_queue.put(('error', f'Folder download: {exc}'))
        finally:
            if sock:
                sock.close()

    def on_close(self) -> None:
        self.disconnect()
        if self.tray is not None:
            self.tray.stop()
        self.root.destroy()


def main() -> None:
    root = tk.Tk()
    ClientApp(root)
    root.mainloop()


if __name__ == '__main__':
    main()
