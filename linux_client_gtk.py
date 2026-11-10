#!/usr/bin/env python3
from __future__ import annotations

"""Native Zorin/GNOME client UI for ZorinMacBridge.

The networking, TLS pinning, H.264 decode and file-transfer implementation is
reused from linux_client.ClientApp.  This module replaces the Tk UI with
GTK4/libadwaita so the installed application follows the Zorin/GNOME theme.
"""

import atexit
import datetime
import faulthandler
import os
import posixpath
import queue
import shutil
import subprocess
import sys
import threading
import traceback
import time
from pathlib import Path
from types import SimpleNamespace

import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
gi.require_version('Gdk', '4.0')
gi.require_version('GdkPixbuf', '2.0')
from gi.repository import Adw, Gdk, GdkPixbuf, Gio, GLib, Gtk, Pango

from linux_client import (
    ClientApp as CoreClient,
    MAX_CLIPBOARD,
    MODIFIER_KEYS,
    SPECIAL_KEYS,
    VIDEO_QUALITY_PRESETS,
    VIDEO_FPS_PRESETS,
    VIDEO_RESOLUTION_PRESETS,
    format_fp,
    human_size,
    is_lan_ip,
    normalize_fp,
    remote_join,
)
from protocol import CLIPBOARD_GET, CLIPBOARD_SET, KEY, MOUSE_BUTTON, MOUSE_MOVE, SCROLL, TEXT, pack_json, pack_packet
from discovery import DiscoveredServer, desktop_connect_strategy, discover_servers
from resources import resource_path
from settings import client_record
from zmb_pairing_state import DesktopTrustMismatch, ensure_desktop_trust, persist_desktop_trust, trusted_desktop_fingerprint
from secret_store import get_password as get_saved_password
from tray_icon import TrayController
from updates import current_version
from zmb_admin_backend import (
    get_profile,
    get_status_for_profile,
    list_profiles,
    neighbor_mac_for_host,
    normalize_mac_address,
    run_action_for_profile,
    save_profile,
    send_wake_on_lan,
)
from zmb_system_page import SystemController
from zmb_xcode_page import XcodeController
from zmb_terminal_page import TerminalController
from zmb_projects_page import ProjectsController
from zmb_shutdown import arm_hard_exit_watchdog
from zmb_session_lifecycle import reconnect_delay_seconds
from zmb_client_runtime import initialize_client_runtime_state

# GTK4 and the GTK3 AppIndicator backend cannot live in the same process.
# Prefer pystray's Xorg backend as a best-effort tray icon under X11/XWayland.
os.environ.setdefault('PYSTRAY_BACKEND', 'xorg')


def _client_state_dir() -> Path:
    root = os.environ.get('XDG_STATE_HOME')
    base = Path(root).expanduser() if root else (Path.home() / '.local' / 'state')
    return base / 'zorinmacbridge'


def _client_log_path() -> Path:
    return _client_state_dir() / 'client.log'


def _persistent_log(line: str) -> None:
    """Best-effort lifecycle/crash log that survives the GTK window disappearing."""
    try:
        path = _client_log_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open('a', encoding='utf-8') as handle:
            handle.write(str(line).rstrip() + '\n')
    except Exception:
        pass


def _install_crash_logging():
    """Keep evidence for silent Python/native exits without changing normal control flow."""
    try:
        path = _client_log_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        crash_file = path.open('a', encoding='utf-8', buffering=1)
        faulthandler.enable(crash_file, all_threads=True)
    except Exception:
        crash_file = None

    old_hook = sys.excepthook

    def excepthook(exc_type, exc, tb):
        stamp = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S.%f')[:-3]
        _persistent_log(f'{stamp} [FATAL] Unhandled exception: {exc_type.__name__}: {exc}')
        try:
            if crash_file is not None:
                traceback.print_exception(exc_type, exc, tb, file=crash_file)
        except Exception:
            pass
        old_hook(exc_type, exc, tb)

    sys.excepthook = excepthook

    old_thread_hook = getattr(threading, 'excepthook', None)
    if old_thread_hook is not None:
        def thread_hook(args):
            stamp = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S.%f')[:-3]
            _persistent_log(
                f'{stamp} [ERROR] Unhandled thread exception in {getattr(args.thread, "name", "thread")}: '
                f'{args.exc_type.__name__}: {args.exc_value}'
            )
            old_thread_hook(args)
        threading.excepthook = thread_hook

    def process_exit():
        stamp = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S.%f')[:-3]
        _persistent_log(f'{stamp} [INFO] Python process exiting normally (atexit).')
        try:
            if crash_file is not None:
                crash_file.flush()
        except Exception:
            pass

    atexit.register(process_exit)
    return crash_file


class ValueVar:
    def __init__(self, value=None, callback=None):
        self._value = value
        self._callback = callback

    def get(self):
        return self._value

    def set(self, value):
        self._value = value
        if self._callback is not None:
            self._callback(value)


class GtkRootAdapter:
    """Small compatibility layer for backend methods inherited from the Tk client."""

    def __init__(self, window: 'ClientWindow') -> None:
        self.window = window

    def after(self, ms: int, callback, *args):
        def run_once():
            callback(*args)
            return GLib.SOURCE_REMOVE
        return GLib.timeout_add(max(0, int(ms)), run_once)

    def destroy(self):
        # Legacy CoreClient paths must not bypass the audited GTK shutdown path.
        self.window.on_close(reason='legacy-root-destroy')


class ClientWindow(Adw.ApplicationWindow, CoreClient):
    def __init__(self, app: Adw.Application) -> None:
        Adw.ApplicationWindow.__init__(self, application=app)
        self.set_title('ZorinMacBridge')
        self.set_default_size(1320, 900)
        self.set_size_request(900, 620)

        self.root = GtkRootAdapter(self)
        self.ip_var = ValueVar('192.168.1.50')
        self.port_var = ValueVar('45950')
        self.password_var = ValueVar('')
        self.fp_var = ValueVar('')
        self.status_var = ValueVar('Disconnected', self._status_changed)
        self.remote_path_var = ValueVar('/')
        self.linux_shortcuts_var = ValueVar(True)
        self.capture_input_var = ValueVar(True)
        self.auto_reconnect_var = ValueVar(True)
        self.quality_var = ValueVar('Adaptive')
        self.fps_var = ValueVar('30')
        self.resolution_var = ValueVar('1080p')
        self.display_id_var = ValueVar(0)
        self._display_choices = [('Auto — current Mac display', 0)]
        self.machine_var = ValueVar('')
        self.remember_credentials_var = ValueVar(True)
        self.discovered_by_label = {}
        self.selected_server_id = ''
        self.selected_server_name = ''
        self.selected_desktop_available = True
        self.selected_system_available = False
        self.selected_paired_only = False

        initialize_client_runtime_state(self)
        self._clear_session_queues()
        self._last_texture = None
        self._last_pixbuf = None
        self._file_rows = {}
        self._reconnect_source = 0
        self._reconnect_attempt = 0
        self._last_disconnect_reason = ''
        self._discovery_watchdog = 0
        self._file_render_serial = 0
        self._desktop_trust_recovery_busy = False
        self._online_desktop_recovery_busy = False
        self._wake_connect_busy = False
        self._display_wake_busy = False
        self._last_display_wake_at = 0.0
        self._closing = False

        self._build_native_ui()
        self._install_css()
        self._install_controllers()
        self.connect('notify::fullscreened', self._fullscreened_changed)

        self.tray = TrayController(
            self.root,
            'ZorinMacBridge Client',
            resource_path('assets/client.png'),
            [
                ('Show window', self.show_window),
                ('Find Macs on LAN', self.discover_macs),
                ('Connect', self.connect_remote),
                ('Disconnect', self.disconnect_remote),
                ('Check for updates', self.check_updates),
                ('Quit', self.on_close),
            ],
        )
        self.tray.start()

        GLib.timeout_add(16, self._gtk_tick)
        GLib.timeout_add(700, self._initial_discovery)
        self.connect('close-request', self._on_close_request)
        self._log('INFO', 'ZorinMacBridge Client started with GTK4/libadwaita UI.')

    # ---------- Native UI ----------

    def _install_css(self) -> None:
        css = b'''
        .zmb-card {
            border-radius: 14px;
            padding: 12px;
            background-color: alpha(@card_bg_color, 0.96);
        }
        .zmb-remote {
            border-radius: 14px;
            background: #05070b;
        }
        .zmb-path {
            font-family: monospace;
            opacity: 0.86;
        }
        .zmb-muted { opacity: 0.68; }
        .zmb-file-row { padding: 7px 9px; }
        .zmb-status { font-weight: 600; }
        .zmb-fullscreen-bar {
            background: alpha(#111318, 0.96);
            border-radius: 0;
            padding: 8px 12px;
            margin: 0;
            color: white;
        }
        .zmb-fullscreen-bar label { color: white; }
        '''
        provider = Gtk.CssProvider()
        provider.load_from_data(css)
        display = Gdk.Display.get_default()
        if display is not None:
            Gtk.StyleContext.add_provider_for_display(display, provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)

    def _build_native_ui(self) -> None:
        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self.set_content(outer)

        self.header = Adw.HeaderBar()
        title = Adw.WindowTitle(title='ZorinMacBridge', subtitle='Zorin OS → macOS development bridge')
        self.header.set_title_widget(title)
        outer.append(self.header)

        self.find_button = Gtk.Button(icon_name='view-refresh-symbolic', tooltip_text='Find Macs on LAN')
        self.find_button.connect('clicked', lambda *_: self.discover_macs())
        self.header.pack_start(self.find_button)

        menu = Gio.Menu()
        menu.append('Check for updates…', 'app.update')
        menu.append('About ZorinMacBridge', 'app.about')
        menu_button = Gtk.MenuButton(icon_name='open-menu-symbolic', menu_model=menu)
        self.header.pack_end(menu_button)

        self.fullscreen_bar = self._build_fullscreen_bar()
        outer.append(self.fullscreen_bar)

        page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        page.set_margin_start(12)
        page.set_margin_end(12)
        page.set_margin_top(10)
        page.set_margin_bottom(12)
        outer.append(page)

        self.connection_card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=9)
        self.connection_card.add_css_class('zmb-card')
        page.append(self.connection_card)

        first = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self.connection_card.append(first)
        discovered_label = Gtk.Label(label='Mac', xalign=0)
        discovered_label.set_width_chars(9)
        first.append(discovered_label)
        self.machine_model = Gtk.StringList.new([])
        self.machine_dropdown = Gtk.DropDown(model=self.machine_model)
        self.machine_dropdown.set_hexpand(True)
        self.machine_dropdown.connect('notify::selected', self._machine_dropdown_changed)
        first.append(self.machine_dropdown)
        self.discovery_refresh_button = Gtk.Button(icon_name='view-refresh-symbolic', tooltip_text='Refresh LAN discovery')
        self.discovery_refresh_button.connect('clicked', lambda *_: self.discover_macs())
        first.append(self.discovery_refresh_button)

        second = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self.connection_card.append(second)
        self.ip_entry = self._entry(second, 'Current IP', self.ip_var, width=16)
        self.port_entry = self._entry(second, 'Port', self.port_var, width=7)
        self.password_entry = self._entry(second, 'ZMB session password', self.password_var, password=True, expand=True)
        self.connect_btn = Gtk.Button(label='Connect')
        self.connect_btn.add_css_class('suggested-action')
        self.connect_btn.connect('clicked', lambda *_: self.connect_remote())
        second.append(self.connect_btn)
        self.disconnect_btn = Gtk.Button(label='Disconnect')
        self.disconnect_btn.connect('clicked', lambda *_: self.disconnect_remote())
        second.append(self.disconnect_btn)

        third = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self.connection_card.append(third)
        fp_label = Gtk.Label(label='Desktop trust', xalign=0)
        fp_label.set_width_chars(12)
        third.append(fp_label)
        self.fp_entry = Gtk.Entry()
        self.fp_entry.set_hexpand(True)
        self.fp_entry.set_editable(False)
        self.fp_entry.set_can_focus(False)
        self.fp_entry.set_placeholder_text('Established automatically by System pairing')
        self.fp_entry.set_tooltip_text('Read-only TLS identity pinned automatically through the verified System/SSH pairing.')
        third.append(self.fp_entry)

        opts = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        self.connection_card.append(opts)
        self.shortcut_switch = self._switch_item(opts, 'Linux shortcuts', self.linux_shortcuts_var, self._shortcut_mode_changed)
        self.remember_switch = self._switch_item(opts, 'Remember Mac', self.remember_credentials_var, None)
        self.capture_switch = self._switch_item(opts, 'Capture keyboard & mouse', self.capture_input_var, self._capture_input_changed)
        self.reconnect_switch = self._switch_item(opts, 'Auto reconnect', self.auto_reconnect_var, self._auto_reconnect_changed)
        opts.append(Gtk.Separator(orientation=Gtk.Orientation.VERTICAL))
        push = Gtk.Button(label='Clipboard → Mac')
        push.connect('clicked', lambda *_: self.push_clipboard())
        opts.append(push)
        pull = Gtk.Button(label='Mac → Clipboard')
        pull.connect('clicked', lambda *_: self.pull_clipboard())
        opts.append(pull)

        video_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self.connection_card.append(video_row)
        video_label = Gtk.Label(label='Video', xalign=0)
        video_label.set_width_chars(12)
        video_row.append(video_label)

        resolution_label = Gtk.Label(label='Resolution', xalign=0)
        video_row.append(resolution_label)
        self.resolution_model = Gtk.StringList.new(list(VIDEO_RESOLUTION_PRESETS))
        self.resolution_dropdown = Gtk.DropDown(model=self.resolution_model)
        self.resolution_dropdown.set_selected(list(VIDEO_RESOLUTION_PRESETS).index('1080p'))
        self.resolution_dropdown.connect('notify::selected', self._resolution_dropdown_changed)
        video_row.append(self.resolution_dropdown)

        fps_label = Gtk.Label(label='FPS', xalign=0)
        fps_label.set_margin_start(10)
        video_row.append(fps_label)
        self.fps_model = Gtk.StringList.new(list(VIDEO_FPS_PRESETS))
        self.fps_dropdown = Gtk.DropDown(model=self.fps_model)
        self.fps_dropdown.set_selected(list(VIDEO_FPS_PRESETS).index('30'))
        self.fps_dropdown.connect('notify::selected', self._fps_dropdown_changed)
        video_row.append(self.fps_dropdown)

        quality_label = Gtk.Label(label='Quality', xalign=0)
        quality_label.set_margin_start(10)
        video_row.append(quality_label)
        self.quality_model = Gtk.StringList.new(list(VIDEO_QUALITY_PRESETS))
        self.quality_dropdown = Gtk.DropDown(model=self.quality_model)
        self.quality_dropdown.set_selected(list(VIDEO_QUALITY_PRESETS).index('Adaptive'))
        self.quality_dropdown.connect('notify::selected', self._quality_dropdown_changed)
        video_row.append(self.quality_dropdown)
        self.quality_description = Gtk.Label(label=self._quality_description_text('Adaptive'), xalign=0)
        self.quality_description.add_css_class('zmb-muted')
        self.quality_description.set_hexpand(True)
        video_row.append(self.quality_description)
        self.video_stats_label = Gtk.Label(label='Stream: waiting', xalign=1)
        self.video_stats_label.add_css_class('zmb-muted')
        self.video_stats_label.set_tooltip_text('Actual server FPS · receive rate · local latest-frame drops · render queue delay')
        video_row.append(self.video_stats_label)

        source_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self.connection_card.append(source_row)
        source_label = Gtk.Label(label='Display source', xalign=0)
        source_label.set_width_chars(12)
        source_row.append(source_label)
        self.display_model = Gtk.StringList.new([self._display_choices[0][0]])
        self.display_dropdown = Gtk.DropDown(model=self.display_model)
        self.display_dropdown.connect('notify::selected', self._display_dropdown_changed)
        source_row.append(self.display_dropdown)
        self.display_description = Gtk.Label(
            label='1080p uses the physical Mac display. 2K / 4K use the remote virtual desktop. Resolution, FPS and bitrate are independent.',
            xalign=0,
        )
        self.display_description.add_css_class('zmb-muted')
        self.display_description.set_hexpand(True)
        source_row.append(self.display_description)

        self.status_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        page.append(self.status_box)
        self.status_icon = Gtk.Image.new_from_icon_name('network-offline-symbolic')
        self.status_box.append(self.status_icon)
        self.status_label = Gtk.Label(label='Disconnected', xalign=0)
        self.status_label.add_css_class('zmb-status')
        self.status_label.set_hexpand(True)
        self.status_box.append(self.status_label)
        hint = Gtk.Label(label='Keyboard & mouse are forwarded by default · Esc / Alt+Esc exits full screen', xalign=1)
        hint.add_css_class('zmb-muted')
        self.status_box.append(hint)
        self.enter_fullscreen_btn = Gtk.Button(label='Full Screen')
        self.enter_fullscreen_btn.set_sensitive(False)
        self.enter_fullscreen_btn.connect('clicked', lambda *_: self._toggle_fullscreen())
        self.status_box.append(self.enter_fullscreen_btn)

        self.stack_switcher = Gtk.StackSwitcher()
        page.append(self.stack_switcher)
        self.stack = Gtk.Stack(transition_type=Gtk.StackTransitionType.CROSSFADE)
        self.stack.set_hexpand(True)
        self.stack.set_vexpand(True)
        self.stack_switcher.set_stack(self.stack)
        page.append(self.stack)

        self.desktop_page = self._build_desktop_page()
        self.files_page = self._build_files_page()
        self.system_controller = SystemController(self)
        self.system_page = self.system_controller.page
        self.xcode_controller = XcodeController(self)
        self.xcode_page = self.xcode_controller.page
        self.terminal_controller = TerminalController(self)
        self.terminal_page = self.terminal_controller.page
        self.projects_controller = ProjectsController(self)
        self.projects_page = self.projects_controller.page
        self.logs_page = self._build_logs_page()
        self.stack.add_titled(self.desktop_page, 'desktop', 'Desktop')
        self.stack.add_titled(self.files_page, 'files', 'Files')
        self.stack.add_titled(self.system_page, 'system', 'System')
        self.stack.add_titled(self.xcode_page, 'xcode', 'Xcode')
        self.stack.add_titled(self.terminal_page, 'terminal', 'Terminal')
        self.stack.add_titled(self.projects_page, 'projects', 'Projects')
        self.stack.add_titled(self.logs_page, 'logs', 'Logs')
        self.stack.set_visible_child_name('desktop')
        self._refresh_files_state()

    def _entry(self, box: Gtk.Box, label: str, var: ValueVar, *, width=10, password=False, expand=False):
        lab = Gtk.Label(label=label, xalign=0)
        box.append(lab)
        entry = Gtk.Entry()
        entry.set_width_chars(width)
        entry.set_hexpand(expand)
        entry.set_text(str(var.get() or ''))
        if password:
            entry.set_visibility(False)
            entry.set_input_purpose(Gtk.InputPurpose.PASSWORD)
        entry.connect('changed', lambda w: var.set(w.get_text()))
        box.append(entry)
        old_callback = var._callback
        def update(value):
            text = str(value or '')
            if entry.get_text() != text:
                entry.set_text(text)
            if old_callback:
                old_callback(value)
        var._callback = update
        return entry

    def _switch_item(self, box: Gtk.Box, label: str, var: ValueVar, callback):
        wrap = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        lbl = Gtk.Label(label=label)
        sw = Gtk.Switch(active=bool(var.get()), valign=Gtk.Align.CENTER)
        wrap.append(lbl)
        wrap.append(sw)
        box.append(wrap)
        def changed(widget, _pspec):
            var.set(bool(widget.get_active()))
            if callback:
                callback()
        sw.connect('notify::active', changed)
        return sw

    def _build_fullscreen_bar(self) -> Gtk.Widget:
        bar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        bar.add_css_class('zmb-fullscreen-bar')
        bar.set_hexpand(True)
        bar.set_visible(False)

        self.fs_status_icon = Gtk.Image.new_from_icon_name('network-offline-symbolic')
        bar.append(self.fs_status_icon)
        self.fs_status_label = Gtk.Label(label='Disconnected', xalign=0)
        self.fs_status_label.set_hexpand(True)
        bar.append(self.fs_status_label)
        self.fs_quality_label = Gtk.Label(label='Quality: Adaptive', xalign=0)
        bar.append(self.fs_quality_label)
        self.fs_input_label = Gtk.Label(label='Input: view only', xalign=0)
        bar.append(self.fs_input_label)

        exit_hint = Gtk.Label(label='Esc / Alt+Esc exits', xalign=1)
        exit_hint.add_css_class('zmb-muted')
        bar.append(exit_hint)
        exit_btn = Gtk.Button(label='Exit Full Screen')
        exit_btn.connect('clicked', lambda *_: self._exit_fullscreen())
        bar.append(exit_btn)

        return bar

    def _build_desktop_page(self) -> Gtk.Widget:
        frame = Gtk.Frame()
        frame.add_css_class('zmb-remote')
        frame.set_hexpand(True)
        frame.set_vexpand(True)
        self.picture = Gtk.Picture()
        self.picture.set_hexpand(True)
        self.picture.set_vexpand(True)
        self.picture.set_can_shrink(True)
        self.picture.set_content_fit(Gtk.ContentFit.CONTAIN)
        self.picture.set_focusable(True)
        frame.set_child(self.picture)
        return frame

    def _build_files_page(self) -> Gtk.Widget:
        root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        self.files_state_label = Gtk.Label(label='Pair this Mac in System, then connect Desktop to use Files.', xalign=0, wrap=True)
        self.files_state_label.add_css_class('zmb-muted')
        root.append(self.files_state_label)
        toolbar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        root.append(toolbar)
        self.file_action_buttons = []
        up = Gtk.Button(icon_name='go-up-symbolic', tooltip_text='Parent folder')
        up.connect('clicked', lambda *_: self.remote_up())
        toolbar.append(up)
        self.file_action_buttons.append(up)
        refresh = Gtk.Button(icon_name='view-refresh-symbolic', tooltip_text='Refresh')
        refresh.connect('clicked', lambda *_: self.refresh_files())
        toolbar.append(refresh)
        self.file_action_buttons.append(refresh)
        self.path_label = Gtk.Label(label='~/ZorinMac-Share/', xalign=0)
        self.path_label.add_css_class('zmb-path')
        self.path_label.set_hexpand(True)
        self.path_label.set_ellipsize(Pango.EllipsizeMode.END)
        toolbar.append(self.path_label)
        upload_file = Gtk.Button(label='Upload files')
        upload_file.connect('clicked', lambda *_: self.upload_files())
        toolbar.append(upload_file)
        self.file_action_buttons.append(upload_file)
        upload_folder = Gtk.Button(label='Upload folder')
        upload_folder.connect('clicked', lambda *_: self.upload_folder())
        toolbar.append(upload_folder)
        self.file_action_buttons.append(upload_folder)
        download = Gtk.Button(label='Download selected')
        download.set_tooltip_text('Single-click a file or folder to select it; double-click a folder to open it.')
        download.add_css_class('suggested-action')
        download.connect('clicked', lambda *_: self.download_selected())
        toolbar.append(download)
        self.file_action_buttons.append(download)
        mkdir = Gtk.Button(icon_name='folder-new-symbolic', tooltip_text='New folder on Mac')
        mkdir.connect('clicked', lambda *_: self.create_remote_folder())
        toolbar.append(mkdir)
        self.file_action_buttons.append(mkdir)

        scrolled = Gtk.ScrolledWindow()
        scrolled.set_hexpand(True)
        scrolled.set_vexpand(True)
        self.file_scroller = scrolled
        self.file_list = Gtk.ListBox()
        self.file_list.set_selection_mode(Gtk.SelectionMode.SINGLE)
        # Single-click must only select an entry so folders can be downloaded.
        # Folder navigation is deliberately a double-click/activation action.
        self.file_list.set_activate_on_single_click(False)
        self.file_list.add_css_class('boxed-list')
        self.file_list.connect('row-activated', self._file_row_activated)
        scrolled.set_child(self.file_list)
        root.append(scrolled)

        note = Gtk.Label(
            label='Mac folder: ~/ZorinMac-Share · Single-click selects; double-click opens folders; Download selected downloads files or whole folders recursively. Symlinks are skipped.',
            xalign=0,
        )
        note.add_css_class('zmb-muted')
        root.append(note)
        for button in self.file_action_buttons:
            button.set_sensitive(False)
        return root

    def _build_logs_page(self) -> Gtk.Widget:
        root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        bar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        root.append(bar)
        copy = Gtk.Button(label='Copy all')
        copy.connect('clicked', lambda *_: self.copy_logs())
        bar.append(copy)
        save = Gtk.Button(label='Save…')
        save.connect('clicked', lambda *_: self.save_logs())
        bar.append(save)
        clear = Gtk.Button(label='Clear')
        clear.connect('clicked', lambda *_: self.clear_logs())
        bar.append(clear)
        note = Gtk.Label(label='Passwords are never written to this log.', xalign=1)
        note.set_hexpand(True)
        note.add_css_class('zmb-muted')
        bar.append(note)

        scroll = Gtk.ScrolledWindow()
        scroll.set_hexpand(True)
        scroll.set_vexpand(True)
        self.log_buffer = Gtk.TextBuffer()
        self.log_view = Gtk.TextView(buffer=self.log_buffer, editable=False, monospace=True, wrap_mode=Gtk.WrapMode.WORD_CHAR)
        scroll.set_child(self.log_view)
        root.append(scroll)
        return root

    def _install_controllers(self) -> None:
        motion = Gtk.EventControllerMotion()
        motion.connect('motion', self._gtk_motion)
        self.picture.add_controller(motion)

        click = Gtk.GestureClick()
        click.set_button(0)
        click.connect('pressed', self._gtk_pressed)
        click.connect('released', self._gtk_released)
        self.picture.add_controller(click)

        scroll = Gtk.EventControllerScroll(flags=Gtk.EventControllerScrollFlags.BOTH_AXES | Gtk.EventControllerScrollFlags.DISCRETE)
        scroll.connect('scroll', self._gtk_scroll)
        self.picture.add_controller(scroll)

        keys = Gtk.EventControllerKey()
        # Full-screen escape shortcuts must win before the focused remote view can
        # consume/forward the key event.  Capture phase keeps Exit/Esc reliable even
        # while keyboard capture is enabled.
        keys.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        keys.connect('key-pressed', self._gtk_key_pressed)
        keys.connect('key-released', self._gtk_key_released)
        self.add_controller(keys)

    # ---------- UI state / dialogs ----------

    def _status_changed(self, value) -> None:
        if hasattr(self, 'status_label'):
            self.status_label.set_text(str(value))
            # The status text may temporarily report a video-only error while the
            # authenticated Desktop/control session remains connected. Full-screen
            # availability must follow the real control-session state, not the text
            # prefix (otherwise an H.264 retry disables Full Screen).
            connected = bool(getattr(self, 'connected', False))
            icon = 'network-transmit-receive-symbolic' if connected else 'network-offline-symbolic'
            self.status_icon.set_from_icon_name(icon)
            if hasattr(self, 'fs_status_icon'):
                self.fs_status_icon.set_from_icon_name(icon)
            if hasattr(self, 'fs_status_label'):
                self.fs_status_label.set_text(str(value))
            if hasattr(self, 'enter_fullscreen_btn'):
                self.enter_fullscreen_btn.set_sensitive(connected)
            self._refresh_fullscreen_bar()

    @staticmethod
    def _quality_description_text(name: str) -> str:
        profile = VIDEO_QUALITY_PRESETS.get(name, VIDEO_QUALITY_PRESETS['Adaptive'])
        if name == 'Adaptive':
            low = int(profile.get('min_bitrate', profile['bitrate'])) // 1_000_000
            high = int(profile.get('max_bitrate', profile['bitrate'])) // 1_000_000
            return f'adaptive bitrate · {low}–{high} Mbit/s'
        return f"{profile['bitrate'] // 1_000_000} Mbit/s"

    def _quality_dropdown_changed(self, _widget, _pspec) -> None:
        idx = self.quality_dropdown.get_selected()
        names = list(VIDEO_QUALITY_PRESETS)
        if idx == Gtk.INVALID_LIST_POSITION or idx >= len(names):
            return
        name = names[idx]
        self.quality_var.set(name)
        self.quality_description.set_text(self._quality_description_text(name))
        self._refresh_fullscreen_bar()
        self._log('INFO', f'Video quality selected: {name} ({self._quality_description_text(name)}).')
        if self.connected:
            self._request_video_reconfigure()
            self.status_var.set(f'Connected — applying {name} on the video channel')

    def _fps_dropdown_changed(self, _widget, _pspec) -> None:
        idx = self.fps_dropdown.get_selected()
        names = list(VIDEO_FPS_PRESETS)
        if idx == Gtk.INVALID_LIST_POSITION or idx >= len(names):
            return
        name = names[idx]
        self.fps_var.set(name)
        self._refresh_fullscreen_bar()
        self._log('INFO', f'Video FPS selected: {VIDEO_FPS_PRESETS[name]} FPS.')
        if self.connected:
            self._request_video_reconfigure()
            self.status_var.set(f'Connected — applying {VIDEO_FPS_PRESETS[name]} FPS on the video channel')

    def _display_dropdown_changed(self, _widget, _pspec) -> None:
        idx = self.display_dropdown.get_selected()
        if idx == Gtk.INVALID_LIST_POSITION or idx >= len(self._display_choices):
            return
        label, display_id = self._display_choices[idx]
        self.display_id_var.set(int(display_id))
        self._log('INFO', f'Capture display selected: {label} (id={display_id}).')
        if self.connected:
            self._request_video_reconfigure()
            self.status_var.set(f'Connected — switching capture to {label}')

    def _resolution_dropdown_changed(self, _widget, _pspec) -> None:
        idx = self.resolution_dropdown.get_selected()
        names = list(VIDEO_RESOLUTION_PRESETS)
        if idx == Gtk.INVALID_LIST_POSITION or idx >= len(names):
            return
        name = names[idx]
        self.resolution_var.set(name)
        preset = VIDEO_RESOLUTION_PRESETS[name]
        virtual_mode = bool(preset.get('virtual'))
        self.display_dropdown.set_sensitive(not virtual_mode)
        if virtual_mode:
            self.display_description.set_text(f'{name} = headless remote virtual desktop; the MacBook panel stays out of the remote workspace.')
        else:
            self.display_description.set_text('1080p = physical Mac display (safe/default mode). FPS and Quality remain independent.')
        self._log('INFO', f'Remote desktop resolution selected: {name}' + (' (virtual display).' if virtual_mode else ' (physical display).'))
        if self.connected:
            self._request_video_reconfigure()
            self.status_var.set(f'Connected — switching remote desktop to {name}')

    def _apply_display_list(self, displays) -> None:
        rows = []
        for item in displays if isinstance(displays, list) else []:
            if not isinstance(item, dict):
                continue
            try:
                display_id = int(item.get('id', 0))
                width = int(item.get('width', 0))
                height = int(item.get('height', 0))
            except (TypeError, ValueError):
                continue
            if display_id <= 0 or width <= 0 or height <= 0:
                continue
            built_in = bool(item.get('builtin'))
            main = bool(item.get('main'))
            kind = 'MacBook built-in' if built_in else 'External'
            suffix = ' · main' if main else ''
            label = f'{kind} — {width}×{height}{suffix}'
            rows.append((label, display_id, built_in, width * height, main, width, height))
        rows.sort(key=lambda row: (not row[4], -row[3], row[1]))
        # Auto follows the Mac's main display first. This project often runs on a
        # MacBook with no external Mac-side monitor; Zorin-side 2K/4K monitors are
        # unrelated to ScreenCaptureKit source selection.
        automatic = next((row for row in rows if row[4]), rows[0] if rows else None)
        self._auto_display_id = int(automatic[1]) if automatic else 0
        self._display_source_sizes = {int(row[1]): (int(row[5]), int(row[6])) for row in rows}
        choices = [('Auto — current Mac display', 0)] + [(row[0], row[1]) for row in rows]
        current_id = int(self.display_id_var.get() or 0)
        self._display_choices = choices
        self.display_model = Gtk.StringList.new([label for label, _ in choices])
        self.display_dropdown.set_model(self.display_model)
        selected = next((i for i, (_, did) in enumerate(choices) if did == current_id), 0)
        self.display_dropdown.set_selected(selected)
        self._log('INFO', 'Capture displays: ' + ', '.join(label for label, _ in choices))

    def _refresh_fullscreen_bar(self) -> None:
        if hasattr(self, 'fs_quality_label'):
            self.fs_quality_label.set_text(f'{self.resolution_var.get()} · {self.fps_var.get()} FPS · Quality: {self.quality_var.get()}')
        if hasattr(self, 'fs_input_label'):
            self.fs_input_label.set_text(
                'Input: keyboard & mouse' if self.capture_input_var.get() else 'Input: view only'
            )

    def _auto_reconnect_changed(self) -> None:
        enabled = bool(self.auto_reconnect_var.get())
        self._log('INFO', f'Auto reconnect {"enabled" if enabled else "disabled"}.')
        if not enabled and self._reconnect_source:
            try:
                GLib.source_remove(self._reconnect_source)
            except Exception:
                pass
            self._reconnect_source = 0
            self._reconnect_attempt = 0

    def _schedule_auto_reconnect(self, reason: str) -> None:
        if not self.auto_reconnect_var.get() or self.disconnect_requested or self._reconnect_source:
            return
        self._reconnect_attempt += 1
        delay = reconnect_delay_seconds(self._reconnect_attempt, reason)
        self._last_disconnect_reason = reason
        self.status_var.set(f'Disconnected — reconnecting in {delay:g}s')
        self._log('INFO', f'Unexpected disconnect ({reason}); auto reconnect attempt {self._reconnect_attempt} in {delay:g}s.')
        self._reconnect_source = GLib.timeout_add(int(delay * 1000), self._run_auto_reconnect)

    def _run_auto_reconnect(self):
        self._reconnect_source = 0
        if not self.auto_reconnect_var.get() or self.disconnect_requested or self.connected:
            return GLib.SOURCE_REMOVE
        if self.net_thread is not None and self.net_thread.is_alive():
            self._reconnect_source = GLib.timeout_add(300, self._run_auto_reconnect)
            return GLib.SOURCE_REMOVE
        self._log('INFO', f'Auto reconnect attempt {self._reconnect_attempt} starting.')
        self.connect_remote()
        return GLib.SOURCE_REMOVE

    def _dialog(self, heading: str, body: str, *, destructive=False) -> None:
        dlg = Adw.MessageDialog(transient_for=self, heading=heading, body=body)
        dlg.add_response('ok', 'OK')
        if destructive:
            dlg.set_response_appearance('ok', Adw.ResponseAppearance.DESTRUCTIVE)
        dlg.present()

    def _confirm(self, heading: str, body: str, action_label: str, callback) -> None:
        dlg = Adw.MessageDialog(transient_for=self, heading=heading, body=body)
        dlg.add_response('cancel', 'Cancel')
        dlg.add_response('ok', action_label)
        dlg.set_default_response('ok')
        dlg.set_close_response('cancel')
        dlg.set_response_appearance('ok', Adw.ResponseAppearance.SUGGESTED)
        dlg.connect('response', lambda _d, response: callback() if response == 'ok' else None)
        dlg.present()

    def _show_update_result(self, info) -> None:
        self._log('INFO', f'Update check completed: installed={info.current}, latest={info.latest}, available={info.available}')
        if not info.available:
            self.status_var.set('Update check completed.')
            self._dialog('ZorinMacBridge update', f'You are up to date (version {info.current}).')
            return

        self.status_var.set(f'ZorinMacBridge {info.latest} is available.')
        dlg = Adw.MessageDialog(
            transient_for=self,
            heading='ZorinMacBridge update',
            body=f'Version {info.latest} is available.\nInstalled: {info.current}\n\nDownload, verify, and install it now?',
        )
        dlg.add_response('cancel', 'Cancel')
        dlg.add_response('install', 'Install update')
        dlg.set_default_response('install')
        dlg.set_close_response('cancel')
        dlg.set_response_appearance('install', Adw.ResponseAppearance.SUGGESTED)

        def responded(_dialog, response: str) -> None:
            if response == 'install':
                self._install_update(info)
            else:
                self.status_var.set('Update cancelled.')

        dlg.connect('response', responded)
        dlg.present()

    def show_window(self) -> None:
        self.present()

    def show_about(self) -> None:
        about = Adw.AboutWindow(
            transient_for=self,
            application_name='ZorinMacBridge Client',
            application_icon='zorinmacbridge',
            version=current_version(),
            developer_name='ILoveMyProjects',
            comments='LAN-first Zorin/Linux client for a macOS development machine.',
        )
        about.present()

    def _merge_paired_admin_servers(self, servers):
        """Keep paired Macs selectable even when the desktop mDNS service is down."""
        merged = list(servers)
        seen_ids = {str(item.server_id) for item in merged if getattr(item, 'server_id', '')}
        seen_hosts = {(str(item.ip), int(item.port)) for item in merged}
        for profile in list_profiles():
            host = str(profile.get('host') or '').strip()
            if not host:
                continue
            sid = str(profile.get('server_id') or '').strip()
            try:
                port = int(profile.get('desktop_port') or 45950)
            except (TypeError, ValueError):
                port = 45950
            if (sid and sid in seen_ids) or (host, port) in seen_hosts:
                continue
            name = str(profile.get('server_name') or host).strip() or host
            merged.append(DiscoveredServer(name=name, ip=host, port=port, server_id=sid, paired_only=True, desktop_available=False, admin_available=False))
            if sid:
                seen_ids.add(sid)
            seen_hosts.add((host, port))
        return sorted(merged, key=lambda item: (item.name.lower(), item.paired_only, item.ip, item.port))

    def _set_discovery_busy(self, busy: bool) -> None:
        self._discovery_busy = bool(busy)
        self.find_button.set_sensitive(not busy)
        if hasattr(self, 'discovery_refresh_button'):
            self.discovery_refresh_button.set_sensitive(not busy)

    def discover_macs(self) -> None:
        """Scan Bonjour without ever blocking GTK or stacking concurrent scans."""
        if self._discovery_busy:
            self.status_var.set('LAN scan is already running…')
            return
        self._discovery_serial += 1
        token = self._discovery_serial
        self._set_discovery_busy(True)
        self.status_var.set('Scanning LAN for ZorinMacBridge Macs…')
        self._log('INFO', 'Starting bounded Bonjour scan for desktop + system services.')

        if self._discovery_watchdog:
            try:
                GLib.source_remove(self._discovery_watchdog)
            except Exception:
                pass

        def expired():
            if token != self._discovery_serial:
                return GLib.SOURCE_REMOVE
            self._discovery_serial += 1  # late worker result is ignored
            self._discovery_watchdog = 0
            self._set_discovery_busy(False)
            self.status_var.set('LAN scan timed out. The client is still responsive; retry when the Mac is awake.')
            self._log('WARNING', 'Bonjour scan watchdog expired after 7 seconds.')
            return GLib.SOURCE_REMOVE

        self._discovery_watchdog = GLib.timeout_add(7000, expired)

        def worker() -> None:
            try:
                servers = discover_servers(3.8)
                GLib.idle_add(done, token, servers, None)
            except Exception as exc:
                GLib.idle_add(done, token, None, str(exc))

        def done(job_token: int, servers, error):
            if job_token != self._discovery_serial:
                return False
            if self._discovery_watchdog:
                try:
                    GLib.source_remove(self._discovery_watchdog)
                except Exception:
                    pass
                self._discovery_watchdog = 0
            self._set_discovery_busy(False)
            if error:
                self.status_var.set('LAN discovery error: ' + error)
                self._log('ERROR', 'LAN discovery error: ' + error)
                return False
            self._apply_discovery_results(servers or [])
            return False

        threading.Thread(target=worker, daemon=True).start()

    def _apply_discovery_results(self, live_servers) -> None:
        servers = self._merge_paired_admin_servers(live_servers)
        previous_id = str(self.selected_server_id or '')
        previous_label = str(self.machine_var.get() or '')
        self.discovered_by_label = {server.label: server for server in servers}
        values = list(self.discovered_by_label)
        self.machine_model.splice(0, self.machine_model.get_n_items(), values)
        if not servers:
            self.status_var.set('No ZorinMacBridge Mac found on the LAN.')
            self._log('INFO', 'LAN discovery found no desktop or system service.')
            return

        selected = 0
        if previous_id:
            for idx, server in enumerate(servers):
                if server.server_id and server.server_id == previous_id:
                    selected = idx
                    break
        elif previous_label in self.discovered_by_label:
            selected = values.index(previous_label)
        self.machine_dropdown.set_selected(selected)
        self.machine_var.set(values[selected])
        self._machine_selected()

        desktop = sum(1 for s in servers if s.desktop_available and not s.paired_only)
        system = sum(1 for s in servers if s.admin_available and not s.paired_only)
        offline = sum(1 for s in servers if s.paired_only)
        self.status_var.set(f'LAN scan: {desktop} desktop service(s), {system} system service(s), {offline} saved/offline.')
        self._log('INFO', 'LAN discovery list: ' + ', '.join(s.label for s in servers))

    def _initial_discovery(self):
        self.discover_macs()
        return GLib.SOURCE_REMOVE

    def _machine_dropdown_changed(self, _widget, _pspec) -> None:
        idx = self.machine_dropdown.get_selected()
        if idx == Gtk.INVALID_LIST_POSITION or idx >= self.machine_model.get_n_items():
            return
        value = self.machine_model.get_string(idx)
        self.machine_var.set(value)
        self._machine_selected()

    def _set_tls_fingerprint(self, value: str) -> None:
        text = str(value or '').strip()
        self.fp_var.set(text)
        if hasattr(self, 'fp_entry') and self.fp_entry.get_text() != text:
            self.fp_entry.set_text(text)

    def _refresh_files_state(self) -> None:
        paired = False
        try:
            paired = bool(self.system_controller._profile()) if hasattr(self, 'system_controller') else False
        except Exception:
            paired = False
        if hasattr(self, 'files_state_label'):
            if self.connected and self.prelogin_session:
                self.files_state_label.set_text('macOS login window connected · Files unlock automatically after user login.')
            elif self.connected:
                self.files_state_label.set_text('Desktop connected ✓ · Files are available through the authenticated desktop session.')
            elif paired:
                self.files_state_label.set_text('Mac paired ✓ · Desktop is not connected. Connect Desktop to use Files.')
            else:
                self.files_state_label.set_text('Pair this Mac in System, then connect Desktop to use Files.')
        for button in getattr(self, 'file_action_buttons', []):
            button.set_sensitive(bool(self.connected and not self.prelogin_session))

    def pairing_state_changed(self, profile: dict | None = None, *, status: dict | None = None, include_system: bool = True) -> None:
        """Propagate one successful pairing to every feature surface.

        Desktop TLS trust is persisted only when its fingerprint came back through
        the already-pinned P0 SSH channel (profile/status), never from discovery.
        """
        sid = str(getattr(self, 'selected_server_id', '') or '').strip()
        host = str(self.ip_var.get() or '').strip()
        if profile is None:
            try:
                profile = self.system_controller._profile() if hasattr(self, 'system_controller') else None
            except Exception:
                profile = None
        if isinstance(profile, dict):
            profile_sid = str(profile.get('server_id') or '').strip()
            profile_host = str(profile.get('host') or '').strip()
            if (not sid or not profile_sid or profile_sid == sid) and (not host or not profile_host or profile_host == host):
                try:
                    fingerprint = persist_desktop_trust(profile, status)
                except DesktopTrustMismatch as exc:
                    fingerprint = trusted_desktop_fingerprint(sid)
                    self._set_tls_fingerprint(fingerprint)
                    self._log('ERROR', f'SECURITY: {exc}')
                    self.status_var.set('SECURITY WARNING — Desktop TLS identity changed. Desktop connection is blocked; System/SSH pairing remains available.')
                if fingerprint:
                    self._set_tls_fingerprint(fingerprint)
                    self._log('INFO', 'Desktop TLS trust loaded from the verified System pairing; no second fingerprint confirmation is required.')
                else:
                    self._recover_desktop_trust_async(profile, connect_after=False)
        if include_system and hasattr(self, 'system_controller'):
            self.system_controller.selection_changed()
        if hasattr(self, 'terminal_controller'):
            self.terminal_controller.selection_changed()
        if hasattr(self, 'projects_controller'):
            self.projects_controller.refresh_projects()
        if hasattr(self, 'xcode_controller'):
            self.xcode_controller.selection_changed()
        self._refresh_files_state()

    def _prime_wake_identity_async(self) -> None:
        """Remember the Mac network address while it is awake, before sleep makes discovery disappear."""
        sid = str(self.selected_server_id or '').strip()
        host = str(self.ip_var.get() or '').strip()
        profile = get_profile(sid, host)
        if not profile or profile.get('wake_mac_address'):
            return

        def worker():
            try:
                status = get_status_for_profile(profile)
                mac = normalize_mac_address(status.get('wake_mac_address'))
                if mac:
                    save_profile(sid, host, {**profile, 'wake_mac_address': mac, 'system_hostname': status.get('hostname') or profile.get('system_hostname', '')})
                    self._log('INFO', f'Wake-on-Connect: remembered Mac network address {mac} from the pinned P0 channel.')
            except Exception:
                pass

        threading.Thread(target=worker, daemon=True, name='zmb-wake-identity').start()

    def _machine_selected(self, _event=None) -> None:
        server = self.discovered_by_label.get(self.machine_var.get())
        if server is None:
            return
        self.ip_var.set(server.ip)
        self.port_var.set(str(server.port))
        previous_id = str(self.selected_server_id or '')
        self.selected_server_id = server.server_id or f'{server.ip}:{server.port}'
        self.selected_server_name = server.name
        self.selected_desktop_available = bool(server.desktop_available and not server.paired_only)
        self.selected_system_available = bool(server.admin_available and not server.paired_only)
        self.selected_paired_only = bool(server.paired_only)
        record = client_record(self.selected_server_id)
        saved_fp = str(record.get('fingerprint', '')).strip()
        if saved_fp:
            self._set_tls_fingerprint(saved_fp)
        elif previous_id != self.selected_server_id:
            self._set_tls_fingerprint('')

        # Desktop keyrings can block on D-Bus/Secret Service. Never query one on
        # the GTK thread. A late result is ignored if the user changed machine.
        if previous_id != self.selected_server_id:
            self.password_var.set('')
        self._credential_serial += 1
        credential_token = self._credential_serial
        selected_id = self.selected_server_id

        def credential_worker() -> None:
            saved_password = get_saved_password(selected_id)
            GLib.idle_add(credential_done, credential_token, selected_id, saved_password)

        def credential_done(token: int, server_id: str, password):
            if token != self._credential_serial or server_id != self.selected_server_id:
                return False
            if password:
                self.password_var.set(password)
            return False

        threading.Thread(target=credential_worker, daemon=True).start()

        trust = 'Desktop identity trusted.' if saved_fp else 'Desktop trust will be synchronized automatically from System pairing.'
        host_text = f'{server.hostname} / {server.ip}' if server.hostname else server.ip
        roles = []
        if server.desktop_available:
            roles.append('desktop')
        if server.admin_available:
            roles.append('system')
        if server.paired_only:
            roles.append('saved/offline')
        role_text = ', '.join(roles) or 'legacy'
        self.status_var.set(f'Selected {server.name} at {host_text}:{server.port} ({role_text}). {trust}')
        self._log('INFO', f'Discovery selection: {server.name} hostname={server.hostname or "?"} ip={server.ip}:{server.port}; server_id={self.selected_server_id}; roles={role_text}')
        if server.admin_available and not server.paired_only:
            self._prime_wake_identity_async()
        self.pairing_state_changed(include_system=False)

    def _connection_params(self):
        """Return Desktop connection parameters using only persisted paired trust.

        GTK deliberately does not accept a user-entered TLS SHA anymore. The
        fingerprint must be learned through System pairing and stored by stable
        server_id. This prevents a second/manual trust workflow from reappearing.
        """
        ip = str(self.ip_var.get() or '').strip()
        if not is_lan_ip(ip):
            raise ValueError('Only a literal LAN/loopback/link-local IP address is allowed. Hostnames and public IPs are blocked.')
        port = int(str(self.port_var.get() or '').strip())
        if not (1 <= port <= 65535):
            raise ValueError('Invalid port')
        password = str(self.password_var.get() or '')
        if not password:
            raise ValueError('Enter the ZMB session password.')
        sid = str(self.selected_server_id or '').strip()
        expected_fp = normalize_fp(trusted_desktop_fingerprint(sid))
        if len(expected_fp) != 64:
            raise ValueError('Desktop trust is not established yet. Pair this Mac once in System; ZorinMacBridge will learn the Desktop identity automatically through the verified SSH channel.')
        self._set_tls_fingerprint(format_fp(expected_fp))
        return ip, port, password, expected_fp

    def _recover_desktop_trust_async(self, profile: dict | None = None, *, connect_after: bool = False) -> bool:
        """Recover missing Desktop trust on a worker through pinned SSH/P0."""
        if self._desktop_trust_recovery_busy:
            if connect_after:
                self.status_var.set('Desktop identity recovery is already running…')
            return True
        sid = str(self.selected_server_id or '').strip()
        if trusted_desktop_fingerprint(sid):
            self._set_tls_fingerprint(client_record(sid).get('fingerprint', ''))
            if connect_after:
                GLib.idle_add(self.connect_remote)
            return True
        if profile is None:
            try:
                profile = self.system_controller._profile() if hasattr(self, 'system_controller') else None
            except Exception:
                profile = None
        if not isinstance(profile, dict):
            return False
        profile_sid = str(profile.get('server_id') or '').strip()
        if sid and profile_sid and sid != profile_sid:
            return False
        self._desktop_trust_recovery_busy = True
        if connect_after:
            self.status_var.set('Recovering Desktop identity through the paired SSH channel…')
        self._log('INFO', 'Recovering missing Desktop TLS trust through the already-pinned System/SSH profile.')

        def worker() -> None:
            try:
                fingerprint = ensure_desktop_trust(profile)
                error = '' if fingerprint else 'The paired Mac did not provide a Desktop TLS identity.'
            except Exception as exc:
                fingerprint = ''
                error = str(exc)
            GLib.idle_add(done, fingerprint, error)

        def done(fingerprint: str, error: str):
            self._desktop_trust_recovery_busy = False
            current_sid = str(self.selected_server_id or '').strip()
            if sid and current_sid and current_sid != sid:
                return False
            if fingerprint:
                self._set_tls_fingerprint(fingerprint)
                self._log('INFO', 'Desktop TLS trust recovered through pinned SSH and persisted; no manual SHA is required.')
                if connect_after:
                    self.connect_remote()
            else:
                self._log('ERROR', 'Desktop trust recovery failed: ' + (error or 'unknown error'))
                if connect_after:
                    self._dialog(
                        'Desktop identity unavailable',
                        'This Mac is paired, but its Desktop TLS identity could not be recovered through the verified SSH channel. '
                        'Open System and refresh/recover the Mac. No manual SHA entry is required or accepted.\n\n' + (error or ''),
                    )
            return False

        threading.Thread(target=worker, daemon=True).start()
        return True

    def _recover_online_mac_then_connect(self) -> bool:
        """Recover Desktop through live P0 when the Mac is already online.

        This is the reboot/loginwindow path.  A live System advertisement means
        Wake-on-LAN must not run: query the pinned P0 channel, recover either the
        LoginWindow endpoint or the normal Aqua endpoint, then connect to the
        existing TLS identity.
        """
        if self._online_desktop_recovery_busy:
            self.status_var.set('Mac is online — preparing remote login…')
            return True
        sid = str(self.selected_server_id or '').strip()
        host = str(self.ip_var.get() or '').strip()
        profile = get_profile(sid, host)
        if not profile:
            return False

        self._online_desktop_recovery_busy = True
        self.connect_btn.set_sensitive(False)
        self.status_var.set('Mac is online — preparing remote login…')
        self._log('INFO', 'Desktop Connect: System/P0 is online while Desktop is absent; using session recovery instead of Wake-on-LAN.')

        def port_open(candidate_host: str, candidate_port: int) -> bool:
            try:
                import socket as _socket
                with _socket.create_connection((candidate_host, candidate_port), timeout=1.2):
                    return True
            except Exception:
                return False

        def worker():
            active_profile = dict(profile)
            try:
                status = get_status_for_profile(active_profile)
            except Exception as exc:
                GLib.idle_add(done, None, f'System service stopped responding: {exc}', True)
                return

            no_gui = status.get('user_session_available') != '1'
            action = 'recover-prelogin' if no_gui else 'recover-desktop'
            mode = 'macOS login window' if no_gui else 'Aqua Desktop'
            self._log('INFO', f'Desktop Connect: Mac is awake; requesting {mode} recovery through P0 ({action}).')
            action_error = ''
            try:
                result = run_action_for_profile(active_profile, action, timeout=25)
                if result.returncode != 0:
                    action_error = (result.stderr or result.stdout or f'{action} exited {result.returncode}').strip()
            except Exception as exc:
                action_error = str(exc)

            deadline = time.monotonic() + 25.0
            port = int(active_profile.get('desktop_port') or self.port_var.get() or 45950)
            while time.monotonic() < deadline and not self._closing:
                candidate_host = str(active_profile.get('host') or host)
                if port_open(candidate_host, port):
                    GLib.idle_add(done, active_profile, None, False)
                    return
                # DHCP may have changed across reboot.  Prefer the same stable
                # server_id and update transport address only.
                try:
                    for item in discover_servers(1.2):
                        if sid and item.server_id == sid:
                            active_profile['host'] = item.ip
                            if item.desktop_available and port_open(item.ip, int(item.port or port)):
                                active_profile['desktop_port'] = int(item.port or port)
                                GLib.idle_add(done, active_profile, None, False)
                                return
                            break
                except Exception:
                    pass
                time.sleep(1.0)

            detail = action_error or (
                'LoginWindow pre-login Desktop did not become reachable.' if no_gui
                else 'Desktop service did not become reachable after recovery.'
            )
            GLib.idle_add(done, None, detail, False)

        def done(active_profile, error, system_unreachable):
            self._online_desktop_recovery_busy = False
            self.connect_btn.set_sensitive(True)
            if system_unreachable:
                # Bonjour can be stale for a machine that has just gone to sleep.
                # Only after the pinned P0 channel itself fails do we permit WOL.
                self.selected_system_available = False
                self._log('WARNING', 'Desktop Connect: live System advertisement was stale; falling back to Wake-on-Connect.')
                if self._wake_sleeping_mac_then_connect():
                    return False
            if error:
                self.status_var.set('Mac online — remote login service unavailable.')
                self._log('ERROR', 'Online Desktop recovery failed: ' + error)
                self._dialog(
                    'Remote login service unavailable',
                    'The Mac is online, but ZorinMacBridge could not start the remote login/Desktop endpoint. '
                    'Wake-on-LAN was not used because the System service is already reachable.\n\n' + error,
                )
                return False
            if active_profile:
                live_host = str(active_profile.get('host') or host)
                live_port = int(active_profile.get('desktop_port') or self.port_var.get() or 45950)
                self.ip_var.set(live_host)
                self.port_var.set(str(live_port))
                if sid and live_host:
                    save_profile(sid, live_host, {**active_profile, 'host': live_host, 'desktop_port': live_port})
            self.selected_desktop_available = True
            self.status_var.set('Remote login service ready — connecting…')
            self._log('INFO', 'Desktop Connect: recovered online Desktop/pre-login endpoint; starting normal TLS connection.')
            GLib.timeout_add(100, lambda: (self.connect_remote(), GLib.SOURCE_REMOVE)[1])
            return False

        threading.Thread(target=worker, daemon=True, name='zmb-online-desktop-recovery').start()
        return True

    def _wake_sleeping_mac_then_connect(self) -> bool:
        if self._wake_connect_busy:
            self.status_var.set('Waking Mac…')
            return True
        sid = str(self.selected_server_id or '').strip()
        host = str(self.ip_var.get() or '').strip()
        profile = get_profile(sid, host)
        if not profile:
            return False

        self._wake_connect_busy = True
        self.connect_btn.set_sensitive(False)
        self.status_var.set('Waking Mac…')
        self._log('INFO', 'Wake-on-Connect: selected Mac is saved/offline; starting LAN wake and reachability probes.')

        def worker():
            active_profile = dict(profile)
            mac = str(active_profile.get('wake_mac_address') or '').strip()
            if not mac:
                mac = neighbor_mac_for_host(str(active_profile.get('host') or host))
            if mac:
                send_wake_on_lan(mac, rounds=5)
                self._log('INFO', f'Wake-on-Connect: magic packet sent to {mac}.')
            else:
                self._log('WARNING', 'Wake-on-Connect: no saved/neighbor MAC address; using active network probes only.')

            deadline = time.monotonic() + 60.0
            last_status = None
            desktop_seen = False
            while time.monotonic() < deadline and not self._closing:
                # A connection attempt itself can participate in macOS Wake for
                # network access / sleep-proxy handling, so probe both SSH and the
                # Desktop listener while waiting for the magic packet to take effect.
                for port in (int(active_profile.get('ssh_port') or 22), int(active_profile.get('desktop_port') or 45950)):
                    try:
                        import socket as _socket
                        with _socket.create_connection((str(active_profile.get('host') or host), port), timeout=1.2):
                            if port == int(active_profile.get('desktop_port') or 45950):
                                desktop_seen = True
                    except Exception:
                        pass
                if desktop_seen:
                    GLib.idle_add(done, active_profile, None)
                    return
                try:
                    last_status = get_status_for_profile(active_profile)
                    found_mac = normalize_mac_address(last_status.get('wake_mac_address'))
                    if found_mac and found_mac != active_profile.get('wake_mac_address'):
                        active_profile['wake_mac_address'] = found_mac
                    try:
                        run_action_for_profile(active_profile, 'wake-display', timeout=8)
                    except Exception as exc:
                        self._log('DEBUG', f'Wake-on-Connect: wake-display probe returned {exc}.')
                    if last_status.get('zmb_port_listening') == '1':
                        GLib.idle_add(done, active_profile, None)
                        return
                    if last_status.get('user_session_available') == '1':
                        try:
                            run_action_for_profile(active_profile, 'recover-desktop', timeout=20)
                        except Exception as exc:
                            self._log('WARNING', f'Wake-on-Connect: recover-desktop returned {exc}; continuing readiness probes.')
                except Exception:
                    # If DHCP changed while the Mac was asleep, Bonjour can reveal
                    # the new address as soon as it wakes. Keep identity anchored to
                    # server_id and update only the transport address.
                    try:
                        for item in discover_servers(1.2):
                            if sid and item.server_id == sid:
                                active_profile['host'] = item.ip
                                break
                    except Exception:
                        pass
                time.sleep(1.5)
            GLib.idle_add(done, None, 'Mac did not wake or restore the Desktop service within 60 seconds.')

        def done(active_profile, error):
            self._wake_connect_busy = False
            self.connect_btn.set_sensitive(True)
            if error:
                self.status_var.set('Mac is still offline after wake attempt.')
                self._log('ERROR', 'Wake-on-Connect failed: ' + error)
                self._dialog('Wake failed', error + '\n\nThe saved login settings were not changed.')
                return False
            if active_profile:
                live_host = str(active_profile.get('host') or host)
                self.ip_var.set(live_host)
                if sid and live_host:
                    save_profile(sid, live_host, active_profile)
            self.selected_desktop_available = True
            self.selected_paired_only = False
            self.status_var.set('Mac woke up — connecting Desktop…')
            self._log('INFO', 'Wake-on-Connect: Mac/Desktop became reachable; clearing saved/offline state and continuing normal Desktop connection.')
            GLib.timeout_add(100, lambda: (self.connect_remote(), GLib.SOURCE_REMOVE)[1])
            return False

        threading.Thread(target=worker, daemon=True, name='zmb-wake-connect').start()
        return True

    def _wake_display_for_video_async(self) -> None:
        now = time.monotonic()
        if self._display_wake_busy or (now - self._last_display_wake_at) < 2.0:
            return
        sid = str(self.selected_server_id or '').strip()
        host = str(self.ip_var.get() or '').strip()
        profile = get_profile(sid, host)
        if not profile:
            self._log('WARNING', 'Display wake requested but no paired System profile is available.')
            return
        self._display_wake_busy = True
        self._last_display_wake_at = now
        self.status_var.set('Mac display is asleep — waking screen…')
        self._log('INFO', 'ScreenCaptureKit reported no display; requesting one-shot display wake through paired P0.')

        def worker():
            try:
                run_action_for_profile(profile, 'wake-display', timeout=8)
                error = ''
            except Exception as exc:
                error = str(exc)
            GLib.idle_add(done, error)

        def done(error: str):
            self._display_wake_busy = False
            if error:
                self._log('WARNING', 'Display wake action failed: ' + error)
            else:
                self.status_var.set('Mac display wake requested — waiting for video…')
                self._log('INFO', 'Display wake action accepted; video channel will retry without disconnecting control.')
            return False

        threading.Thread(target=worker, daemon=True, name='zmb-display-wake').start()

    def connect_remote(self) -> None:
        if self.connected or (self.net_thread is not None and self.net_thread.is_alive()):
            return
        strategy = desktop_connect_strategy(
            desktop_available=self.selected_desktop_available,
            system_available=self.selected_system_available,
            paired_only=self.selected_paired_only,
        )
        if strategy == 'recover-online':
            if self._recover_online_mac_then_connect():
                return
            self.status_var.set('Mac is online, but no paired System profile is available for Desktop recovery.')
            self._log('WARNING', 'Online Desktop recovery skipped because no paired System profile exists for the selected Mac.')
            return
        if strategy == 'wake':
            if self._wake_sleeping_mac_then_connect():
                return
            self.status_var.set('Saved Mac is offline and cannot be woken until it has been paired once in System.')
            self._log('WARNING', 'Wake-on-Connect skipped because no paired System profile exists for the selected Mac.')
            return
        if self._reconnect_source:
            try:
                GLib.source_remove(self._reconnect_source)
            except Exception:
                pass
            self._reconnect_source = 0
        if not trusted_desktop_fingerprint(str(self.selected_server_id or '').strip()):
            if self._recover_desktop_trust_async(connect_after=True):
                return
            self._dialog(
                'System pairing required',
                'Pair this Mac once in the System tab. Desktop trust is then learned and remembered automatically; no separate SHA fingerprint is required.',
            )
            return
        try:
            ip, port, _password, expected_fp = self._connection_params()
        except Exception as exc:
            self._log('ERROR', f'Connection parameters rejected: {exc}')
            self._dialog('Connection', str(exc))
            return
        self._connect_snapshot = (ip, port, _password, expected_fp)
        self._desktop_display_snapshot = self._desktop_display_prepare_payload()
        self.disconnect_requested = False
        # Connection/reconnect state is independent from window full-screen state.
        # Never clear fullscreen_active here: an automatic reconnect can happen while
        # the window remains physically fullscreen, which would otherwise disable all
        # local exit paths (button/Esc/Alt+Esc).
        self.mouse_buttons_down = set()
        self.remember_credentials_active = bool(self.remember_credentials_var.get())
        if not self.selected_server_id:
            self.selected_server_id = f'{ip}:{port}'
            self.selected_server_name = ip
        self.stop_event.clear()
        self.status_var.set('Connecting…')
        self._log('INFO', f'Connect requested: {ip}:{port}; expected fingerprint {format_fp(expected_fp)}')
        self.net_thread = threading.Thread(target=self._network_loop, daemon=True)
        self.net_thread.start()

    def disconnect_remote(self) -> None:
        if self._reconnect_source:
            try:
                GLib.source_remove(self._reconnect_source)
            except Exception:
                pass
            self._reconnect_source = 0
        self._reconnect_attempt = 0
        if self._window_is_fullscreen() or self.fullscreen_active:
            self._exit_fullscreen()
        self._release_remote_input_state()
        self.disconnect_requested = True
        self.stop_event.set()
        self._clear_session_queues()
        self.connected = False
        self.status_var.set('Disconnected')
        self._refresh_files_state()
        self._log('INFO', 'Disconnect requested by user.')

    def _restart_after_update(self) -> None:
        # Replace the process immediately. Do not call GTK/tray/network teardown
        # first: any blocking cleanup here can leave the old GUI in a
        # "not responding" state after the package has already been replaced.
        # Python-created sockets/file descriptors are non-inheritable by default,
        # so exec closes the old session as the process image is replaced.
        exe = '/usr/bin/zorinmacbridge'
        if not Path(exe).is_file():
            self._dialog(
                'Restart failed',
                f'The updated launcher was not found at {exe}. Close this window and start ZorinMacBridge Client from the application menu.',
            )
            return
        self._log('INFO', 'Restarting after update by immediately replacing the current process with /usr/bin/zorinmacbridge.')
        try:
            os.execv(exe, [exe])
        except Exception as exc:
            self._log('ERROR', f'Restart after update failed: {type(exc).__name__}: {exc}')
            self._dialog('Restart failed', f'The update is installed, but the client could not restart automatically.\n\n{exc}')

    # ---------- Queue / video ----------

    def _gtk_tick(self):
        try:
            self._process_gtk_queue()
        except Exception as exc:
            # Never let a single UI event remove the GLib periodic source.
            # The failed queue item has already been consumed, so the next tick
            # can continue servicing video/input and future actions.
            self.status_var.set('A UI action failed; ZorinMacBridge kept running. See Logs.')
            self._log('ERROR', f'GTK queue handler failed: {type(exc).__name__}: {exc}')
        try:
            viewport = int(self.picture.get_allocated_width())
            if viewport >= 320:
                self._video_viewport_width = viewport
        except Exception:
            pass
        latest = None
        try:
            while True:
                latest = self.video_frames.get_nowait()
        except queue.Empty:
            pass
        if latest is not None:
            self._last_image = latest
            try:
                self._last_render_delay_ms = max(0.0, (time.monotonic() - float(latest[3])) * 1000.0)
            except Exception:
                self._last_render_delay_ms = 0.0
            # Do not spend CPU constructing textures for a minimized/unmapped window.
            if self.get_mapped():
                self._redraw()
        return GLib.SOURCE_CONTINUE

    def _process_gtk_queue(self) -> None:
        # Never monopolize the GTK main loop. A burst of logs/network events must
        # leave time for window redraw/input so GNOME does not mark us hung.
        deadline = time.monotonic() + 0.008
        processed = 0
        try:
            while processed < 160 and time.monotonic() < deadline:
                item = self.ui_queue.get_nowait()
                processed += 1
                kind = item[0]
                if kind == 'log':
                    self._append_log(item[1])
                elif kind == 'prelogin_state':
                    self.prelogin_session = bool(item[1])
                    if self.prelogin_session:
                        self.prelogin_video_ready = False
                        self.prelogin_input_armed = False
                        self.prelogin_input_ready = False
                    self._refresh_files_state()
                elif kind == 'prelogin_pipeline':
                    stage = str(item[1] or '') if len(item) > 1 else ''
                    labels = {
                        'capture-frame': 'capturing LoginWindow',
                        'pixel-buffer': 'preparing LoginWindow frame',
                        'encode-submit': 'encoding LoginWindow frame',
                        'h264-written': 'sending LoginWindow frame',
                    }
                    if self.connected and self.prelogin_session and not self.prelogin_video_ready and stage in labels:
                        self.status_var.set(f'Connected — {labels[stage]}…')
                elif kind == 'prelogin_capture_backend':
                    backend = str(item[1] or '') if len(item) > 1 else ''
                    if self.connected and self.prelogin_session and not self.prelogin_video_ready:
                        if backend == 'scstream':
                            self.status_var.set('Connected — starting LoginWindow SCStream…')
                        elif backend == 'screencapture-cli':
                            self.status_var.set('Connected — trying macOS LoginWindow system capture…')
                        elif backend == 'screenshot-manager':
                            self.status_var.set('Connected — trying LoginWindow screenshot fallback…')
                elif kind == 'prelogin_capture_failure':
                    reason = str(item[1] or '') if len(item) > 1 else 'unknown capture failure'
                    if self.connected and self.prelogin_session and not self.prelogin_video_ready:
                        self.status_var.set('LoginWindow video unavailable — ' + reason)
                    self._log('ERROR', 'LoginWindow capture failed: ' + reason)
                elif kind == 'prelogin_video_ready':
                    self.prelogin_video_ready = True
                    if self.connected and self.prelogin_session:
                        if self.prelogin_input_ready:
                            self.status_var.set('Connected — macOS login window · remote input active')
                            if self.capture_input_var.get():
                                self.picture.grab_focus()
                        elif self.prelogin_input_armed:
                            self.status_var.set('Connected — macOS login window · input armed, awaiting first dispatch')
                            if self.capture_input_var.get():
                                self.picture.grab_focus()
                        else:
                            self.status_var.set('Connected — macOS login window · waiting for input backend')
                    if self.prelogin_input_ready:
                        self._log('INFO', f'Pre-login video is visible ({item[1]}x{item[2]}); remote input active.')
                    elif self.prelogin_input_armed:
                        self._log('INFO', f'Pre-login video is visible ({item[1]}x{item[2]}); input armed, delivery unverified.')
                    else:
                        self._log('INFO', f'Pre-login video is visible ({item[1]}x{item[2]}); waiting for LoginWindow input backend.')
                elif kind == 'prelogin_input_armed':
                    self.prelogin_input_armed = True
                    backend = str(item[1] or '') if len(item) > 1 else 'unknown'
                    result = str(item[2] or '') if len(item) > 2 else 'OPEN_READY'
                    if self.connected and self.prelogin_session and self.prelogin_video_ready:
                        self.status_var.set('Connected — macOS login window · input armed, awaiting first dispatch')
                        if self.capture_input_var.get():
                            self.picture.grab_focus()
                    self._log('INFO', f'Pre-login remote input armed: backend={backend}, state={result}; delivery not yet verified.')
                elif kind == 'prelogin_input_ready':
                    self.prelogin_input_armed = True
                    self.prelogin_input_ready = True
                    backend = str(item[1] or '') if len(item) > 1 else 'unknown'
                    result = str(item[2] or '') if len(item) > 2 else 'OPEN_READY'
                    if self.connected and self.prelogin_session and self.prelogin_video_ready:
                        self.status_var.set('Connected — macOS login window · remote input active')
                        if self.capture_input_var.get():
                            self.picture.grab_focus()
                    self._log('INFO', f'Pre-login remote input active: backend={backend}, state={result}.')
                elif kind == 'prelogin_input_unavailable':
                    self.prelogin_input_armed = False
                    self.prelogin_input_ready = False
                    reason = str(item[1] or '') if len(item) > 1 else 'no LoginWindow input backend available'
                    if self.connected and self.prelogin_session:
                        if self.prelogin_video_ready:
                            self.status_var.set('Connected — macOS login window · view only (input unavailable)')
                        else:
                            self.status_var.set('Connected — macOS login window · input unavailable')
                    self._log('ERROR', 'Pre-login remote input unavailable: ' + reason)
                elif kind == 'connected':
                    self.connected = True
                    self._reconnect_attempt = 0
                    if self.prelogin_session:
                        self.status_var.set('Connected — macOS login window · waiting for first frame')
                    elif self.capture_input_var.get():
                        self.status_var.set('Connected — keyboard & mouse capture enabled')
                        self.picture.grab_focus()
                    else:
                        self.status_var.set('Connected — view only')
                    self._log('INFO', 'Desktop session connected' + (' (macOS login window).' if self.prelogin_session else '.'))
                    self._refresh_fullscreen_bar()
                    self._refresh_files_state()
                    if not self.prelogin_session:
                        self.refresh_files()
                elif kind == 'prelogin_handoff':
                    user = str(item[1] or '') if len(item) > 1 else ''
                    self.status_var.set('Login accepted — switching to Aqua Desktop…')
                    self._log('INFO', f'LoginWindow → Aqua handoff announced by Mac{f" for {user}" if user else ""}.')
                elif kind == 'disconnected':
                    self.connected = False
                    reason = item[1] if len(item) > 1 else 'Session ended'
                    auth_failed = 'bad password' in str(reason).casefold()
                    if auth_failed:
                        self.status_var.set('Authentication failed — check ZMB session password')
                    else:
                        self.status_var.set('Disconnected' if reason == 'Disconnected by user' else f'Disconnected — {reason}')
                    self._log('INFO', f'Desktop session disconnected: {reason}')
                    self._refresh_files_state()
                    if auth_failed:
                        self._reconnect_attempt = 0
                        self._log('WARNING', 'Desktop authentication failed; auto reconnect suppressed until the session password is corrected and Connect is pressed again.')
                        try:
                            self.password_entry.grab_focus()
                            self.password_entry.select_region(0, -1)
                        except Exception:
                            pass
                    elif reason != 'Disconnected by user' and not self.disconnect_requested:
                        self._schedule_auto_reconnect(reason)
                elif kind == 'video_error':
                    # Video is an independent channel. Keep Desktop/control semantics
                    # intact so keyboard/mouse and Full Screen remain available while
                    # H.264 retries in the background.
                    self.status_var.set(f'Connected — video retrying: {item[1]}')
                    self._log('ERROR', item[1])
                elif kind == 'error':
                    self.status_var.set(f'Error: {item[1]}')
                    self._log('ERROR', item[1])
                elif kind == 'display_sleeping':
                    self._wake_display_for_video_async()
                elif kind == 'display_list':
                    self._apply_display_list(item[1])
                elif kind == 'remote_display_prepare':
                    info = item[1] if len(item) > 1 and isinstance(item[1], dict) else {}
                    if not info.get('ready', False):
                        detail = str(info.get('error') or 'remote display preparation failed')
                        self.status_var.set(f'Connected — display preparing/retrying: {detail}')
                        self._log('WARNING', 'Remote display prepare: ' + detail)
                    else:
                        mode = str(info.get('name') or self.resolution_var.get())
                        self._log('INFO', f'Remote display prepared before video: {mode}.')
                elif kind == 'video_stats':
                    stats = item[1]
                    server = stats.get('server') or {}
                    server_fps = server.get('fps')
                    fps = float(server_fps) if isinstance(server_fps, (int, float)) else float(stats.get('decode_fps', 0.0))
                    self.video_stats_label.set_text(
                        f"{stats.get('profile', 'video')} · {fps:.0f} fps · "
                        f"{float(stats.get('receive_mbps', 0.0)):.1f} Mbit/s · "
                        f"drop {float(stats.get('drop_pct', 0.0)):.1f}% · "
                        f"queue {float(stats.get('queue_ms', 0.0)):.0f} ms"
                    )
                elif kind == 'files':
                    self._show_files(item[1])
                elif kind == 'info':
                    self.status_var.set(item[1])
                elif kind == 'clipboard':
                    self._set_local_clipboard(item[1])
                elif kind == 'credential':
                    self._credential_result(*item[1:])
                elif kind == 'discovery':
                    self._discovery_result(*item[1:])
                elif kind == 'discovery_error':
                    self._discovery_error(*item[1:])
                elif kind == 'update':
                    _, token, info = item
                    if token != self._update_check_serial:
                        continue
                    self._update_check_busy = False
                    self._show_update_result(info)
                elif kind == 'update_progress':
                    self.status_var.set(item[1])
                elif kind == 'update_installed':
                    result = item[1]
                    self.status_var.set(f'Updated to {result.installed}. Restart recommended.')
                    self._log('INFO', f'Update installed successfully: {result.current} → {result.installed}')
                    self._confirm('Update installed', f'ZorinMacBridge {result.installed} was installed successfully.', 'Restart now', self._restart_after_update)
                elif kind == 'update_install_error':
                    self.status_var.set('Update installation failed.')
                    self._dialog('Update installation failed', 'The update was not installed.\n\n' + item[1])
                elif kind == 'update_error':
                    _, token, error = item
                    if token != self._update_check_serial:
                        continue
                    self._update_check_busy = False
                    self.status_var.set('Update check failed.')
                    self._dialog('Update check failed', 'The manual update check could not reach GitHub Releases.\n\n' + error)
                elif kind == 'update_timeout':
                    _, token, error = item
                    if token != self._update_check_serial:
                        continue
                    self.status_var.set('Update check timed out.')
                    self._dialog('Update check timed out', error)
        except queue.Empty:
            pass

    def _queue_video_image(self, image) -> bool:
        """Prepare RGB bytes off the GTK thread and keep only the latest frame."""
        dropped = False
        try:
            rgb = image if getattr(image, 'mode', None) == 'RGB' else image.convert('RGB')
            prepared = (rgb.width, rgb.height, rgb.tobytes(), time.monotonic())
            try:
                self.video_frames.put_nowait(prepared)
            except queue.Full:
                dropped = True
                try:
                    self.video_frames.get_nowait()
                except queue.Empty:
                    pass
                try:
                    self.video_frames.put_nowait(prepared)
                except queue.Full:
                    dropped = True
        except Exception as exc:
            self._log('WARNING', f'Could not prepare decoded video frame: {exc}')
        return dropped

    def _redraw(self) -> None:
        prepared = getattr(self, '_last_image', None)
        if prepared is None:
            return
        try:
            width, height, raw = prepared[:3]
        except Exception:
            return
        data = GLib.Bytes.new(raw)
        # GTK 4 can present RGB memory directly; fall back to Pixbuf on older
        # GI builds. This removes one conversion/copy from the common path.
        try:
            texture = Gdk.MemoryTexture.new(width, height, Gdk.MemoryFormat.R8G8B8, data, width * 3)
            self._last_pixbuf = None
        except Exception:
            pixbuf = GdkPixbuf.Pixbuf.new_from_bytes(data, GdkPixbuf.Colorspace.RGB, False, 8, width, height, width * 3)
            texture = Gdk.Texture.new_for_pixbuf(pixbuf)
            self._last_pixbuf = pixbuf
        self._last_texture = texture
        self.picture.set_paintable(texture)

    # ---------- Native input ----------

    def _display_geometry(self):
        aw = max(1, self.picture.get_allocated_width())
        ah = max(1, self.picture.get_allocated_height())
        iw, ih = self.remote_image_size
        if iw <= 0 or ih <= 0:
            return 0, 0, aw, ah
        scale = min(aw / iw, ah / ih)
        dw, dh = iw * scale, ih * scale
        return (aw - dw) / 2.0, (ah - dh) / 2.0, dw, dh

    def _norm_point(self, x, y):
        x0, y0, w, h = self._display_geometry()
        if w <= 0 or h <= 0 or x < x0 or y < y0 or x > x0 + w or y > y0 + h:
            return None
        return ((x - x0) / w, (y - y0) / h)

    def _remote_input_ready(self) -> bool:
        if not self.connected or not self.capture_input_var.get():
            return False
        # Never let pointer/keyboard traffic tear down a pre-login session before
        # the user has actually received the first loginwindow frame.
        if self.prelogin_session:
            if not self.prelogin_video_ready or not self.prelogin_input_armed:
                return False
        return True

    def _gtk_motion(self, _controller, x, y):
        if not self._remote_input_ready():
            return
        pos = self._norm_point(x, y)
        if pos:
            pos = self._transform_stream_point(*pos)
            self._enqueue(pack_packet(MOUSE_MOVE, __import__('struct').pack('!ff', pos[0], pos[1])))
            if self.prelogin_session:
                now = time.monotonic()
                if now - self._last_prelogin_mouse_tx_log_at >= 0.5:
                    self._last_prelogin_mouse_tx_log_at = now
                    self._log('INFO', f'Pre-login mouse TX: action=move x={pos[0]:.3f} y={pos[1]:.3f}.')

    def _gtk_pressed(self, gesture, n_press, x, y):
        button = int(gesture.get_current_button())
        # Double-click is never a local gesture. Every click belongs to the remote
        # Mac; full-screen is entered only through the explicit Full Screen button.
        if not self._remote_input_ready():
            return
        pos = self._norm_point(x, y)
        if pos and button in (1, 2, 3):
            self.mouse_buttons_down.add(button)
            pos = self._transform_stream_point(*pos)
            self._enqueue(pack_packet(MOUSE_BUTTON, __import__('struct').pack('!BBff', button, 1, pos[0], pos[1])))
            if self.prelogin_session:
                self._log('INFO', f'Pre-login mouse TX: action=button-down button={button} x={pos[0]:.3f} y={pos[1]:.3f}.')
        self.picture.grab_focus()

    def _gtk_released(self, gesture, _n_press, x, y):
        if not self._remote_input_ready():
            return
        button = int(gesture.get_current_button())
        pos = self._norm_point(x, y)
        if pos and button in (1, 2, 3):
            self.mouse_buttons_down.discard(button)
            pos = self._transform_stream_point(*pos)
            self._enqueue(pack_packet(MOUSE_BUTTON, __import__('struct').pack('!BBff', button, 0, pos[0], pos[1])))
            if self.prelogin_session:
                self._log('INFO', f'Pre-login mouse TX: action=button-up button={button} x={pos[0]:.3f} y={pos[1]:.3f}.')

    def _gtk_scroll(self, _controller, dx, dy):
        if self._remote_input_ready():
            sx = int(round(-dx * 3))
            sy = int(round(-dy * 3))
            if sx or sy:
                self._enqueue(pack_packet(SCROLL, __import__('struct').pack('!ii', sx, sy)))
                if self.prelogin_session:
                    self._log('INFO', f'Pre-login mouse TX: action=scroll dx={sx} dy={sy}.')
        return True

    @staticmethod
    def _keysym_name(keyval: int) -> str:
        name = Gdk.keyval_name(keyval) or ''
        aliases = {
            'Page_Up': 'Prior', 'Page_Down': 'Next', 'KP_Enter': 'Return',
            'ISO_Left_Tab': 'Tab', 'space': 'space',
        }
        return aliases.get(name, name)

    def _local_text_input_focused(self) -> bool:
        """Return True when keyboard input belongs to a local GTK editor.

        The window-wide key controller runs in CAPTURE phase so full-screen Escape
        remains reliable.  That controller must never steal keystrokes from local
        entries (especially the ZMB session password field) before GTK can deliver
        them to the focused editor.
        """
        widget = self.get_focus()
        while widget is not None:
            # Gtk.Entry is the concrete widget used by ZMB session password.
            # Keep the explicit concrete checks even if a PyGObject build does not
            # allow Gtk.Editable (a GInterface) in isinstance().
            if isinstance(widget, (Gtk.Entry, Gtk.TextView)):
                return True
            try:
                if isinstance(widget, Gtk.Editable):
                    return True
            except TypeError:
                pass
            try:
                widget = widget.get_parent()
            except Exception:
                break
        return False

    def _gtk_key_pressed(self, _controller, keyval, _keycode, state):
        keysym = self._keysym_name(keyval)
        if keysym == 'Escape' and (self._window_is_fullscreen() or self.fullscreen_active):
            # Both Esc and Alt+Esc are reserved locally while fullscreen.
            self._exit_fullscreen()
            return True
        if self._local_text_input_focused():
            if self.connected and self.prelogin_session:
                now = time.monotonic()
                if now - self._last_prelogin_input_blocked_log_at >= 1.0:
                    self._last_prelogin_input_blocked_log_at = now
                    self._log('WARNING', 'Pre-login keyboard TX blocked: a local GTK text editor still has focus.')
            return False
        if not self._remote_input_ready():
            return False

        keysym = self._map_keysym(keysym)
        if self.prelogin_session:
            category = 'modifier' if keysym in MODIFIER_KEYS or keysym in {'Meta_L', 'Meta_R'} else ('printable' if len(keysym) == 1 else 'special')
            self._log('INFO', f'Pre-login keyboard TX: action=key-down category={category}.')
        if keysym in MODIFIER_KEYS or keysym in {'Meta_L', 'Meta_R'}:
            if keysym not in self.modifiers_down:
                self.modifiers_down.add(keysym)
                self._enqueue(pack_json(KEY, {'keysym': keysym, 'down': True}))
            return True

        key_lower = keysym.lower() if len(keysym) == 1 else keysym
        if self._command_down() and key_lower in {'c', 'x'}:
            # Copy/cut updates the Mac pasteboard on the key-down chord.  Pull it
            # shortly afterwards regardless of which key the user releases first.
            self.root.after(140, self._request_remote_clipboard)
        if self._command_down() and key_lower == 'v':
            # Read the Linux clipboard off the GTK thread, then send the clipboard
            # update and only afterwards synthesize Command+V on the Mac.
            self._paste_clipboard_shortcut()
            return True

        if keysym in SPECIAL_KEYS or self._has_non_text_modifier():
            self._enqueue(pack_json(KEY, {'keysym': keysym, 'down': True}))
            self.keys_down.add(keysym)
            return True

        codepoint = Gdk.keyval_to_unicode(keyval)
        char = chr(codepoint) if codepoint else ''
        if char and char.isprintable():
            if not self._enqueue_printable_key(char):
                self._enqueue(pack_packet(TEXT, char.encode('utf-8')))
        elif keysym:
            self._enqueue(pack_json(KEY, {'keysym': keysym, 'down': True}))
            self.keys_down.add(keysym)
        return True

    def _gtk_key_released(self, _controller, keyval, _keycode, _state):
        if self._local_text_input_focused():
            if self.connected and self.prelogin_session:
                now = time.monotonic()
                if now - self._last_prelogin_input_blocked_log_at >= 1.0:
                    self._last_prelogin_input_blocked_log_at = now
                    self._log('WARNING', 'Pre-login keyboard TX blocked: a local GTK text editor still has focus.')
            return False
        if not self._remote_input_ready():
            return False
        keysym = self._map_keysym(self._keysym_name(keyval))
        if self.prelogin_session:
            category = 'modifier' if keysym in MODIFIER_KEYS or keysym in {'Meta_L', 'Meta_R'} else ('printable' if len(keysym) == 1 else 'special')
            self._log('INFO', f'Pre-login keyboard TX: action=key-up category={category}.')
        key_lower = keysym.lower() if len(keysym) == 1 else keysym
        command_was_down = self._command_down()
        if keysym in self.modifiers_down:
            self.modifiers_down.discard(keysym)
            self._enqueue(pack_json(KEY, {'keysym': keysym, 'down': False}))
        elif keysym in self.keys_down:
            self.keys_down.discard(keysym)
            self._enqueue(pack_json(KEY, {'keysym': keysym, 'down': False}))
        return True

    def _capture_input_changed(self) -> None:
        enabled = bool(self.capture_input_var.get())
        self._refresh_fullscreen_bar()
        if not enabled:
            self._release_remote_input_state()
            if self.connected:
                self.status_var.set('Connected — view only')
            self._log('INFO', 'Remote keyboard/mouse capture disabled; desktop is view-only.')
        else:
            self.picture.grab_focus()
            if self.connected:
                self.status_var.set('Connected — keyboard & mouse capture enabled')
            self._log('INFO', 'Remote keyboard/mouse capture enabled.')

    def _release_remote_input_state(self) -> None:
        self._release_local_modifiers()
        if self.connected:
            for button in list(self.mouse_buttons_down):
                self._enqueue(pack_packet(MOUSE_BUTTON, __import__('struct').pack('!BBff', button, 0, 0.5, 0.5)))
        self.mouse_buttons_down.clear()

    def _window_is_fullscreen(self) -> bool:
        try:
            return bool(Gtk.Window.is_fullscreen(self))
        except Exception:
            try:
                return bool(self.get_property('fullscreened'))
            except Exception:
                return bool(self.fullscreen_active)

    def _apply_fullscreen_layout(self, active: bool) -> None:
        self.fullscreen_active = bool(active)
        if active:
            self.stack.set_visible_child_name('desktop')
        self.header.set_visible(not active)
        self.connection_card.set_visible(not active)
        self.status_box.set_visible(not active)
        self.stack_switcher.set_visible(not active)
        self.fullscreen_bar.set_visible(active)
        if active:
            self._refresh_fullscreen_bar()
            self.picture.grab_focus()

    def _fullscreened_changed(self, _window, _pspec) -> None:
        # GTK4 fullscreen/unfullscreen are asynchronous. Mirror the actual window
        # manager state instead of letting a hand-maintained flag drift.
        active = self._window_is_fullscreen()
        previous = bool(self.fullscreen_active)
        self._apply_fullscreen_layout(active)
        if active != previous:
            self._log('INFO', 'Entered full-screen desktop view.' if active else 'Exited full-screen desktop view.')

    def _toggle_fullscreen(self, event=None):
        if self._window_is_fullscreen() or self.fullscreen_active:
            return self._exit_fullscreen(event)
        self._release_remote_input_state()
        # Apply the visual layout immediately, then let notify::fullscreened
        # confirm the asynchronous compositor/window-manager transition.
        self._apply_fullscreen_layout(True)
        Gtk.Window.fullscreen(self)
        self.picture.grab_focus()
        self._log('INFO', 'Full-screen requested. Use Exit Full Screen, Esc, or Alt+Esc to exit.')
        return True

    def _exit_fullscreen(self, event=None):
        # Emergency/idempotent exit: never reject the click because a stale local
        # flag says the window is not full-screen. Restore normal controls now and
        # request unfullscreen from GTK explicitly.
        was_fullscreen = self._window_is_fullscreen() or bool(self.fullscreen_active)
        self._release_remote_input_state()
        self._apply_fullscreen_layout(False)
        Gtk.Window.unfullscreen(self)
        if was_fullscreen:
            self._log('INFO', 'Exit full-screen requested.')
        return True

    # ---------- Clipboard ----------

    def _get_local_clipboard(self) -> str | None:
        commands = []
        if os.environ.get('WAYLAND_DISPLAY') and shutil.which('wl-paste'):
            commands.append(['wl-paste', '--no-newline'])
        if shutil.which('xclip'):
            commands.append(['xclip', '-selection', 'clipboard', '-o'])
        for command in commands:
            try:
                p = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=2, check=False)
                if p.returncode == 0:
                    raw = p.stdout
                    if len(raw) > MAX_CLIPBOARD:
                        self._log('WARNING', 'Linux text clipboard is too large (>2 MiB); not sending it.')
                        return None
                    return raw.decode('utf-8', 'replace')
            except Exception:
                pass
        return None

    def _set_local_clipboard(self, text: str) -> None:
        # Clipboard helpers are external processes. Never wait for them on the
        # GTK main thread; a broken wl-copy/xclip must not freeze the client.
        raw = text.encode('utf-8')

        def worker() -> None:
            commands = []
            if os.environ.get('WAYLAND_DISPLAY') and shutil.which('wl-copy'):
                commands.append(['wl-copy'])
            if shutil.which('xclip'):
                commands.append(['xclip', '-selection', 'clipboard'])
            ok = False
            for command in commands:
                try:
                    proc = subprocess.run(command, input=raw, stderr=subprocess.DEVNULL, timeout=2, check=False)
                    if proc.returncode == 0:
                        ok = True
                        break
                except Exception:
                    pass
            GLib.idle_add(
                self.status_var.set,
                'Text clipboard copied Mac → Linux' if ok else 'Could not access the Linux text clipboard.',
            )

        threading.Thread(target=worker, daemon=True).start()

    def _read_local_clipboard_async(self, callback) -> None:
        """Read text clipboard off the GTK thread and invoke callback on GTK."""
        def worker() -> None:
            text = self._get_local_clipboard()
            GLib.idle_add(callback, text)

        threading.Thread(target=worker, daemon=True).start()

    def _paste_clipboard_shortcut(self) -> None:
        def done(text):
            if text is not None:
                self._enqueue(pack_packet(CLIPBOARD_SET, text.encode('utf-8')))
            self._enqueue_command_shortcut('v')
            return False

        self._read_local_clipboard_async(done)

    def _push_clipboard_silent(self) -> None:
        def done(text):
            if text is not None:
                self._enqueue(pack_packet(CLIPBOARD_SET, text.encode('utf-8')))
            return False

        self._read_local_clipboard_async(done)

    def push_clipboard(self) -> None:
        if not self.connected:
            return

        def done(text):
            if text is None:
                self.status_var.set('No text found in the Linux clipboard.')
                return False
            self._enqueue(pack_packet(CLIPBOARD_SET, text.encode('utf-8')))
            self.status_var.set('Text clipboard sent Linux → Mac')
            return False

        self._read_local_clipboard_async(done)

    # ---------- Logs ----------

    def _log(self, level: str, message: str) -> None:
        timestamp = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S.%f')[:-3]
        clean = str(message).replace('\r', '\\r')
        line = f'{timestamp} [{level}] {clean}'
        _persistent_log(line)
        self.ui_queue.put(('log', line))

    def _append_log(self, line: str) -> None:
        end = self.log_buffer.get_end_iter()
        self.log_buffer.insert(end, line.rstrip() + '\n')
        # Bound the TextBuffer. Long-running/reconnecting sessions used to grow
        # without limit and also left an anonymous mark behind for every line.
        if self.log_buffer.get_char_count() > 1_000_000:
            start = self.log_buffer.get_start_iter()
            cut = self.log_buffer.get_iter_at_offset(250_000)
            self.log_buffer.delete(start, cut)
        mark = self.log_buffer.create_mark(None, self.log_buffer.get_end_iter(), False)
        self.log_view.scroll_to_mark(mark, 0.0, False, 0.0, 1.0)
        self.log_buffer.delete_mark(mark)

    def clear_logs(self) -> None:
        self.log_buffer.set_text('')

    def _log_text(self):
        return self.log_buffer.get_text(self.log_buffer.get_start_iter(), self.log_buffer.get_end_iter(), True)

    def copy_logs(self) -> None:
        text = self._log_text()
        clipboard = Gdk.Display.get_default().get_clipboard()
        provider = Gdk.ContentProvider.new_for_bytes('text/plain;charset=utf-8', GLib.Bytes.new(text.encode('utf-8')))
        clipboard.set_content(provider)
        self.status_var.set('Logs copied to clipboard.')

    def save_logs(self) -> None:
        dialog = Gtk.FileDialog(title='Save ZorinMacBridge client logs')
        dialog.set_initial_name('zorinmacbridge-client.log')
        dialog.save(self, None, self._save_logs_done)

    def _save_logs_done(self, dialog, result):
        try:
            file = dialog.save_finish(result)
            if not file or not file.get_path():
                return
            target = file.get_path()
            # Snapshot GTK-owned text on the GTK thread, then do disk I/O in a
            # worker. Slow/removable filesystems must not freeze the UI.
            text = self._log_text()

            def worker() -> None:
                try:
                    Path(target).write_text(text, encoding='utf-8')
                    GLib.idle_add(done, None)
                except Exception as exc:
                    GLib.idle_add(done, str(exc))

            def done(error):
                if error:
                    self.status_var.set('Could not save logs: ' + error)
                    self._log('ERROR', 'Could not save logs: ' + error)
                else:
                    self.status_var.set(f'Logs saved to {target}')
                return False

            threading.Thread(target=worker, daemon=True).start()
        except GLib.Error:
            pass

    # ---------- Native remote file browser ----------

    def _show_files(self, response: dict) -> None:
        self.remote_path = str(response.get('path', ''))
        shown = '~/' + ('ZorinMac-Share/' + self.remote_path if self.remote_path else 'ZorinMac-Share/')
        self.remote_path_var.set('/' + self.remote_path if self.remote_path else '/')
        self.path_label.set_text(shown)
        self.file_entries.clear()
        self._file_rows.clear()

        # Replace the list widget in O(1) from the caller's perspective instead of
        # synchronously removing thousands of old rows on the GTK thread.
        new_list = Gtk.ListBox()
        new_list.set_selection_mode(Gtk.SelectionMode.SINGLE)
        new_list.add_css_class('boxed-list')
        new_list.connect('row-activated', self._file_row_activated)
        self.file_list = new_list
        self.file_scroller.set_child(new_list)

        entries = list(response.get('items', []))
        self._file_render_serial += 1
        render_token = self._file_render_serial
        index = 0

        def render_batch():
            nonlocal index
            if render_token != self._file_render_serial:
                return GLib.SOURCE_REMOVE
            # Large directories used to create every GTK row in one callback,
            # which can make GNOME report the app as not responding.
            stop = min(len(entries), index + 80)
            while index < stop:
                entry = entries[index]
                iid = f'e{index}'
                self.file_entries[iid] = entry
                row = Gtk.ListBoxRow()
                row.zmb_iid = iid
                line = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
                line.add_css_class('zmb-file-row')
                is_dir = entry.get('kind') == 'dir'
                icon = Gtk.Image.new_from_icon_name('folder-symbolic' if is_dir else 'text-x-generic-symbolic')
                icon.set_pixel_size(24)
                line.append(icon)
                name = Gtk.Label(label=entry['name'], xalign=0)
                name.set_hexpand(True)
                name.set_ellipsize(Pango.EllipsizeMode.END)
                line.append(name)
                typ = Gtk.Label(label='Folder' if is_dir else 'File', xalign=0)
                typ.add_css_class('zmb-muted')
                typ.set_width_chars(8)
                line.append(typ)
                size = Gtk.Label(label='' if is_dir else human_size(int(entry.get('size', 0))), xalign=1)
                size.set_width_chars(11)
                line.append(size)
                try:
                    ts = datetime.datetime.fromtimestamp(int(entry.get('mtime', 0))).strftime('%Y-%m-%d %H:%M')
                except (ValueError, OSError, OverflowError):
                    ts = '?'
                modified = Gtk.Label(label=ts, xalign=0)
                modified.add_css_class('zmb-muted')
                modified.set_width_chars(16)
                line.append(modified)
                row.set_child(line)
                self._file_rows[iid] = row
                self.file_list.append(row)
                index += 1
            return GLib.SOURCE_CONTINUE if index < len(entries) else GLib.SOURCE_REMOVE

        GLib.idle_add(render_batch, priority=GLib.PRIORITY_DEFAULT_IDLE)

    def _file_row_activated(self, _listbox, row):
        iid = getattr(row, 'zmb_iid', None)
        entry = self.file_entries.get(iid)
        if entry and entry.get('kind') == 'dir':
            self.remote_path = remote_join(self.remote_path, entry['name'])
            self.refresh_files()

    def _selected_file_entry(self):
        row = self.file_list.get_selected_row()
        if row is None:
            return None
        return self.file_entries.get(getattr(row, 'zmb_iid', None))

    def upload_files(self) -> None:
        if not self.connected:
            return
        dialog = Gtk.FileDialog(title='Select Linux files to upload to the Mac')
        dialog.open_multiple(self, None, self._upload_files_done)

    def _upload_files_done(self, dialog, result):
        try:
            model = dialog.open_multiple_finish(result)
            paths = []
            for i in range(model.get_n_items()):
                file = model.get_item(i)
                if file.get_path():
                    paths.append(Path(file.get_path()))
            if paths:
                threading.Thread(target=self._upload_files_worker, args=(paths, self.remote_path), daemon=True).start()
        except GLib.Error:
            pass

    def upload_folder(self) -> None:
        if not self.connected:
            return
        dialog = Gtk.FileDialog(title='Select a Linux folder to upload to the Mac')
        dialog.select_folder(self, None, self._upload_folder_done)

    def _upload_folder_done(self, dialog, result):
        try:
            file = dialog.select_folder_finish(result)
            if file and file.get_path():
                threading.Thread(target=self._upload_folder_worker, args=(Path(file.get_path()), self.remote_path), daemon=True).start()
        except GLib.Error:
            pass

    def create_remote_folder(self) -> None:
        if not self.connected:
            return
        entry = Gtk.Entry(placeholder_text='Folder name')
        dlg = Adw.MessageDialog(transient_for=self, heading='New folder on Mac', body='Create a folder inside ~/ZorinMac-Share.')
        dlg.set_extra_child(entry)
        dlg.add_response('cancel', 'Cancel')
        dlg.add_response('create', 'Create')
        dlg.set_response_appearance('create', Adw.ResponseAppearance.SUGGESTED)
        def response(_dlg, rid):
            if rid != 'create':
                return
            name = entry.get_text().strip()
            if not name or '/' in name or '\\' in name or name in {'.', '..'}:
                self._dialog('New folder', 'The folder name is invalid.')
                return
            threading.Thread(target=self._create_remote_folder_worker, args=(remote_join(self.remote_path, name),), daemon=True).start()
        dlg.connect('response', response)
        dlg.present()

    def download_selected(self) -> None:
        if not self.connected:
            return
        entry = self._selected_file_entry()
        if not entry:
            self._dialog('Download', 'Select a file or folder first.')
            return
        remote = remote_join(self.remote_path, entry['name'])
        if entry.get('kind') == 'file':
            dialog = Gtk.FileDialog(title='Save file from Mac')
            dialog.set_initial_name(entry['name'])
            dialog.save(self, None, lambda d, r: self._download_file_dialog_done(d, r, remote))
        else:
            dialog = Gtk.FileDialog(title='Select a destination folder on Linux')
            dialog.select_folder(self, None, lambda d, r: self._download_folder_dialog_done(d, r, remote, entry['name']))

    def _download_file_dialog_done(self, dialog, result, remote):
        try:
            file = dialog.save_finish(result)
            if file and file.get_path():
                threading.Thread(target=self._download_file_worker, args=(remote, Path(file.get_path())), daemon=True).start()
        except GLib.Error:
            pass

    def _download_folder_dialog_done(self, dialog, result, remote, name):
        try:
            folder = dialog.select_folder_finish(result)
            if folder and folder.get_path():
                threading.Thread(target=self._download_folder_worker, args=(remote, Path(folder.get_path()) / name), daemon=True).start()
        except GLib.Error:
            pass

    # ---------- lifecycle ----------

    def _on_close_request(self, *_args):
        self.on_close(reason='window-close')
        # Let GTK complete its normal window destruction.  Returning True here
        # used to veto the compositor/window-manager close and left a visible
        # "not responding" window whenever GApplication cleanup stalled.
        return False

    def on_close(self, reason: str = 'user-quit') -> None:
        if self._closing:
            return
        self._closing = True

        # Visual shutdown comes first.  The user must never be left staring at a
        # hung window while optional native/SSH/tray cleanup is winding down.
        try:
            self.set_visible(False)
        except Exception:
            pass

        # Explicit Quit gets a hard upper bound.  Normal exit terminates this
        # daemon watchdog automatically; a wedged GI/VTE/pystray backend cannot
        # keep the client process alive indefinitely.
        arm_hard_exit_watchdog(1.25)

        app_for_reason = self.get_application()
        if app_for_reason is not None:
            app_for_reason._zmb_shutdown_reason = str(reason)
        try:
            self._log('INFO', f'Application shutdown requested explicitly: {reason}.')
        except Exception:
            pass

        # Invalidate late callbacks immediately.  Do not call disconnect_remote()
        # here: that is an interactive UI path which touches fullscreen/input
        # widgets.  Process exit will close sockets; only cheap stop flags/queues
        # are needed during application shutdown.
        self._update_check_busy = False
        self._update_check_serial += 1
        self._discovery_busy = False
        self._discovery_serial += 1
        self.disconnect_requested = True
        self.stop_event.set()
        self.connected = False
        try:
            self._clear_session_queues()
        except Exception:
            pass

        try:
            # Shutdown variant deliberately avoids VTE.reset()/widget updates.
            self.terminal_controller.disconnect_for_shutdown()
        except Exception:
            pass
        try:
            # Never wait for an SSH/project child on the GTK main thread.
            self.projects_controller.cancel(wait=False, update_ui=False)
        except Exception:
            pass

        if self.tray is not None:
            try:
                self.tray.stop_async()
            except Exception:
                pass
        app = self.get_application()
        if app is not None:
            app.quit()


class ClientApplication(Adw.Application):
    def __init__(self):
        super().__init__(application_id='com.ilovemyprojects.ZorinMacBridge', flags=Gio.ApplicationFlags.DEFAULT_FLAGS)
        self.window = None
        self._zmb_shutdown_reason = 'unrequested'
        self.connect('shutdown', self._shutdown_observed)
        self._install_actions()

    def _shutdown_observed(self, *_args):
        stamp = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S.%f')[:-3]
        _persistent_log(f'{stamp} [INFO] GApplication shutdown observed; reason={self._zmb_shutdown_reason}.')

    def _install_actions(self):
        update = Gio.SimpleAction.new('update', None)
        update.connect('activate', lambda *_: self.window.check_updates() if self.window else None)
        self.add_action(update)
        about = Gio.SimpleAction.new('about', None)
        about.connect('activate', lambda *_: self.window.show_about() if self.window else None)
        self.add_action(about)

    def do_activate(self):
        if self.window is None:
            self.window = ClientWindow(self)
        self.window.present()


def main() -> int:
    crash_file = _install_crash_logging()
    stamp = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S.%f')[:-3]
    _persistent_log(f'{stamp} [INFO] Process start pid={os.getpid()} argv={sys.argv!r}.')
    Adw.init()
    app = ClientApplication()
    try:
        return app.run(sys.argv)
    finally:
        try:
            if crash_file is not None:
                crash_file.flush()
        except Exception:
            pass


if __name__ == '__main__':
    raise SystemExit(main())
