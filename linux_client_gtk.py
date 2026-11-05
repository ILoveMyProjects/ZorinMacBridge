#!/usr/bin/env python3
from __future__ import annotations

"""Native Zorin/GNOME client UI for ZorinMacBridge.

The networking, TLS pinning, H.264 decode and file-transfer implementation is
reused from linux_client.ClientApp.  This module replaces the Tk UI with
GTK4/libadwaita so the installed application follows the Zorin/GNOME theme.
"""

import datetime
import os
import posixpath
import queue
import shutil
import subprocess
import sys
import threading
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
    format_fp,
    human_size,
    normalize_fp,
    remote_join,
)
from protocol import CLIPBOARD_GET, CLIPBOARD_SET, KEY, MOUSE_BUTTON, MOUSE_MOVE, SCROLL, TEXT, pack_json, pack_packet
from discovery import DiscoveredServer, discover_servers
from resources import resource_path
from settings import client_record
from zmb_pairing_state import persist_desktop_trust
from secret_store import get_password as get_saved_password
from tray_icon import TrayController
from updates import current_version
from zmb_admin_backend import list_profiles
from zmb_system_page import SystemController
from zmb_xcode_page import XcodeController
from zmb_terminal_page import TerminalController
from zmb_projects_page import ProjectsController

# GTK4 and the GTK3 AppIndicator backend cannot live in the same process.
# Prefer pystray's Xorg backend as a best-effort tray icon under X11/XWayland.
os.environ.setdefault('PYSTRAY_BACKEND', 'xorg')


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
        app = self.window.get_application()
        if app is not None:
            app.quit()


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
        self.capture_input_var = ValueVar(False)
        self.auto_reconnect_var = ValueVar(True)
        self.quality_var = ValueVar('Adaptive')
        self.machine_var = ValueVar('')
        self.remember_credentials_var = ValueVar(True)

        self.discovered_by_label = {}
        self.selected_server_id = ''
        self.selected_server_name = ''
        self.selected_desktop_available = True
        self.remember_credentials_active = True
        self._connect_snapshot = None

        self.outgoing = queue.Queue(maxsize=1000)
        self._mouse_move_lock = threading.Lock()
        self._pending_mouse_move = None
        self.ui_queue = queue.Queue()
        self.stop_event = threading.Event()
        self.net_thread = None
        self.video_thread = None
        self.video_frames = queue.Queue(maxsize=1)
        self._video_reconfigure_event = threading.Event()
        self._video_viewport_width = 1600
        self._last_render_delay_ms = 0.0
        self.connected = False
        self.remote_image_size = (1, 1)
        self.display_rect = (0, 0, 1, 1)
        self.modifiers_down = set()
        self.keys_down = set()
        self.remote_path = ''
        self.file_entries = {}
        self._clear_session_queues()
        self.disconnect_requested = False
        self.fullscreen_active = False
        self.mouse_buttons_down = set()
        self._last_texture = None
        self._last_pixbuf = None
        self._file_rows = {}
        self._reconnect_source = 0
        self._reconnect_attempt = 0
        self._last_disconnect_reason = ''
        self._discovery_serial = 0
        self._discovery_watchdog = 0
        self._discovery_busy = False
        self._update_check_busy = False
        self._update_check_serial = 0
        self._credential_serial = 0
        self._file_render_serial = 0

        self._build_native_ui()
        self._install_css()
        self._install_controllers()

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
            background: alpha(#111318, 0.88);
            border-radius: 12px;
            padding: 8px 10px;
            margin: 10px;
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
        fp_label = Gtk.Label(label='TLS fingerprint', xalign=0)
        fp_label.set_width_chars(12)
        third.append(fp_label)
        self.fp_entry = Gtk.Entry()
        self.fp_entry.set_hexpand(True)
        self.fp_entry.set_placeholder_text('SHA-256 fingerprint from the Mac server')
        self.fp_entry.connect('changed', lambda w: self.fp_var.set(w.get_text()))
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
        quality_label = Gtk.Label(label='Video quality', xalign=0)
        quality_label.set_width_chars(12)
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
        self.video_stats_label.set_tooltip_text('Decoded FPS · receive rate · local latest-frame drops · render queue delay')
        video_row.append(self.video_stats_label)

        status_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        page.append(status_box)
        self.status_icon = Gtk.Image.new_from_icon_name('network-offline-symbolic')
        status_box.append(self.status_icon)
        self.status_label = Gtk.Label(label='Disconnected', xalign=0)
        self.status_label.add_css_class('zmb-status')
        self.status_label.set_hexpand(True)
        status_box.append(self.status_label)
        hint = Gtk.Label(label='Double-click desktop to enter full screen · Alt+Esc exits', xalign=1)
        hint.add_css_class('zmb-muted')
        status_box.append(hint)

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

    def _build_desktop_page(self) -> Gtk.Widget:
        overlay = Gtk.Overlay()
        overlay.set_hexpand(True)
        overlay.set_vexpand(True)

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
        overlay.set_child(frame)

        self.fullscreen_bar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        self.fullscreen_bar.add_css_class('zmb-fullscreen-bar')
        self.fullscreen_bar.set_halign(Gtk.Align.FILL)
        self.fullscreen_bar.set_valign(Gtk.Align.START)
        self.fullscreen_bar.set_visible(False)
        self.fs_status_icon = Gtk.Image.new_from_icon_name('network-offline-symbolic')
        self.fullscreen_bar.append(self.fs_status_icon)
        self.fs_status_label = Gtk.Label(label='Disconnected', xalign=0)
        self.fs_status_label.set_hexpand(True)
        self.fullscreen_bar.append(self.fs_status_label)
        self.fs_quality_label = Gtk.Label(label='Balanced', xalign=0)
        self.fullscreen_bar.append(self.fs_quality_label)
        self.fs_input_label = Gtk.Label(label='Input: view only', xalign=0)
        self.fullscreen_bar.append(self.fs_input_label)
        exit_hint = Gtk.Label(label='Double-click this bar or press Alt+Esc to exit', xalign=1)
        exit_hint.add_css_class('zmb-muted')
        self.fullscreen_bar.append(exit_hint)
        exit_btn = Gtk.Button(label='Exit Full Screen')
        exit_btn.connect('clicked', lambda *_: self._exit_fullscreen())
        self.fullscreen_bar.append(exit_btn)
        overlay.add_overlay(self.fullscreen_bar)

        bar_click = Gtk.GestureClick(button=1)
        bar_click.connect('pressed', self._fullscreen_bar_pressed)
        self.fullscreen_bar.add_controller(bar_click)
        return overlay

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
        download = Gtk.Button(label='Download')
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
        self.file_list.add_css_class('boxed-list')
        self.file_list.connect('row-activated', self._file_row_activated)
        scrolled.set_child(self.file_list)
        root.append(scrolled)

        note = Gtk.Label(
            label='Mac folder: ~/ZorinMac-Share · File access is restricted to this folder; symlinks are skipped.',
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
        keys.connect('key-pressed', self._gtk_key_pressed)
        keys.connect('key-released', self._gtk_key_released)
        self.add_controller(keys)

    # ---------- UI state / dialogs ----------

    def _status_changed(self, value) -> None:
        if hasattr(self, 'status_label'):
            self.status_label.set_text(str(value))
            connected = str(value).startswith('Connected')
            icon = 'network-transmit-receive-symbolic' if connected else 'network-offline-symbolic'
            self.status_icon.set_from_icon_name(icon)
            if hasattr(self, 'fs_status_icon'):
                self.fs_status_icon.set_from_icon_name(icon)
            if hasattr(self, 'fs_status_label'):
                self.fs_status_label.set_text(str(value))
            self._refresh_fullscreen_bar()

    @staticmethod
    def _quality_description_text(name: str) -> str:
        profile = VIDEO_QUALITY_PRESETS.get(name, VIDEO_QUALITY_PRESETS['Adaptive'])
        text = f"up to {profile['max_width']} px · {profile['fps']} FPS · {profile['bitrate'] // 1_000_000} Mbit/s"
        return ('auto · ' + text) if name == 'Adaptive' else text

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

    def _refresh_fullscreen_bar(self) -> None:
        if hasattr(self, 'fs_quality_label'):
            self.fs_quality_label.set_text(f'Quality: {self.quality_var.get()}')
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
        delay = min(10.0, 1.0 * (2 ** min(self._reconnect_attempt - 1, 4)))
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
            if self.connected:
                self.files_state_label.set_text('Desktop connected ✓ · Files are available through the authenticated desktop session.')
            elif paired:
                self.files_state_label.set_text('Mac paired ✓ · Desktop is not connected. Connect Desktop to use Files.')
            else:
                self.files_state_label.set_text('Pair this Mac in System, then connect Desktop to use Files.')
        for button in getattr(self, 'file_action_buttons', []):
            button.set_sensitive(bool(self.connected))

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
                fingerprint = persist_desktop_trust(profile, status)
                if fingerprint:
                    self._set_tls_fingerprint(fingerprint)
                    self._log('INFO', 'Desktop TLS trust learned from the verified P0 SSH pairing; no second fingerprint confirmation is required.')
        if include_system and hasattr(self, 'system_controller'):
            self.system_controller.selection_changed()
        if hasattr(self, 'terminal_controller'):
            self.terminal_controller.selection_changed()
        if hasattr(self, 'projects_controller'):
            self.projects_controller.refresh_projects()
        if hasattr(self, 'xcode_controller'):
            self.xcode_controller.selection_changed()
        self._refresh_files_state()

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

        trust = 'Saved identity loaded.' if saved_fp else 'Verify the TLS fingerprint once.'
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
        self.pairing_state_changed(include_system=False)

    def connect_remote(self) -> None:
        if self.connected or (self.net_thread is not None and self.net_thread.is_alive()):
            return
        if not self.selected_desktop_available:
            self.status_var.set('Desktop service is offline; use System to recover/start ZorinMacBridge on this Mac.')
            self._log('WARNING', 'Connect skipped because the selected Mac currently advertises System/recovery only.')
            try:
                self.stack.set_visible_child_name('system')
                if self.system_controller._profile():
                    self.system_controller.refresh()
            except Exception:
                pass
            return
        if self._reconnect_source:
            try:
                GLib.source_remove(self._reconnect_source)
            except Exception:
                pass
            self._reconnect_source = 0
        try:
            ip, port, _password, expected_fp = self._connection_params()
        except Exception as exc:
            self._log('ERROR', f'Connection parameters rejected: {exc}')
            self._dialog('Connection', str(exc))
            return
        self._connect_snapshot = (ip, port, _password, expected_fp)
        self.disconnect_requested = False
        self.fullscreen_active = False
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
        if self.fullscreen_active:
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
                elif kind == 'connected':
                    self.connected = True
                    self._reconnect_attempt = 0
                    self.status_var.set('Connected — view only; enable Capture keyboard & mouse to control the Mac')
                    self._log('INFO', 'Desktop session connected.')
                    self._refresh_fullscreen_bar()
                    self._refresh_files_state()
                    self.refresh_files()
                elif kind == 'disconnected':
                    self.connected = False
                    reason = item[1] if len(item) > 1 else 'Session ended'
                    self.status_var.set('Disconnected' if reason == 'Disconnected by user' else f'Disconnected — {reason}')
                    self._log('INFO', f'Desktop session disconnected: {reason}')
                    self._refresh_files_state()
                    if reason != 'Disconnected by user' and not self.disconnect_requested:
                        self._schedule_auto_reconnect(reason)
                elif kind == 'error':
                    self.status_var.set(f'Error: {item[1]}')
                    self._log('ERROR', item[1])
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

    def _gtk_motion(self, _controller, x, y):
        if not self.capture_input_var.get():
            return
        pos = self._norm_point(x, y)
        if pos:
            self._enqueue(pack_packet(MOUSE_MOVE, __import__('struct').pack('!ff', pos[0], pos[1])))

    def _gtk_pressed(self, gesture, n_press, x, y):
        button = int(gesture.get_current_button())
        # Double-click on the remote image only ENTERS full screen. Once full
        # screen, double-clicks belong to the remote Mac; exiting is intentionally
        # moved to the top status bar (or Alt+Esc) so remote double-click remains usable.
        if button == 1 and n_press == 2 and not self.fullscreen_active:
            self._toggle_fullscreen()
            return
        if not self.capture_input_var.get():
            return
        pos = self._norm_point(x, y)
        if pos and button in (1, 2, 3):
            self.mouse_buttons_down.add(button)
            self._enqueue(pack_packet(MOUSE_BUTTON, __import__('struct').pack('!BBff', button, 1, pos[0], pos[1])))
        self.picture.grab_focus()

    def _gtk_released(self, gesture, _n_press, x, y):
        if not self.capture_input_var.get():
            return
        button = int(gesture.get_current_button())
        pos = self._norm_point(x, y)
        if pos and button in (1, 2, 3):
            self.mouse_buttons_down.discard(button)
            self._enqueue(pack_packet(MOUSE_BUTTON, __import__('struct').pack('!BBff', button, 0, pos[0], pos[1])))

    def _gtk_scroll(self, _controller, dx, dy):
        if self.capture_input_var.get():
            sx = int(round(-dx * 3))
            sy = int(round(-dy * 3))
            if sx or sy:
                self._enqueue(pack_packet(SCROLL, __import__('struct').pack('!ii', sx, sy)))
        return True

    @staticmethod
    def _keysym_name(keyval: int) -> str:
        name = Gdk.keyval_name(keyval) or ''
        aliases = {
            'Page_Up': 'Prior', 'Page_Down': 'Next', 'KP_Enter': 'Return',
            'ISO_Left_Tab': 'Tab', 'space': 'space',
        }
        return aliases.get(name, name)

    def _gtk_key_pressed(self, _controller, keyval, _keycode, state):
        keysym = self._keysym_name(keyval)
        alt = bool(state & Gdk.ModifierType.ALT_MASK)
        if keysym == 'Escape' and self.fullscreen_active and alt:
            self._exit_fullscreen()
            return True
        if not self.capture_input_var.get():
            return False

        keysym = self._map_keysym(keysym)
        if keysym in MODIFIER_KEYS or keysym in {'Meta_L', 'Meta_R'}:
            if keysym not in self.modifiers_down:
                self.modifiers_down.add(keysym)
                self._enqueue(pack_json(KEY, {'keysym': keysym, 'down': True}))
            return True

        key_lower = keysym.lower() if len(keysym) == 1 else keysym
        if self._command_down() and key_lower == 'v':
            self._push_clipboard_silent()

        if keysym in SPECIAL_KEYS or self._has_non_text_modifier():
            self._enqueue(pack_json(KEY, {'keysym': keysym, 'down': True}))
            self.keys_down.add(keysym)
            return True

        codepoint = Gdk.keyval_to_unicode(keyval)
        char = chr(codepoint) if codepoint else ''
        if char and char.isprintable():
            self._enqueue(pack_packet(TEXT, char.encode('utf-8')))
        elif keysym:
            self._enqueue(pack_json(KEY, {'keysym': keysym, 'down': True}))
            self.keys_down.add(keysym)
        return True

    def _gtk_key_released(self, _controller, keyval, _keycode, _state):
        if not self.capture_input_var.get():
            return False
        keysym = self._map_keysym(self._keysym_name(keyval))
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

    def _toggle_fullscreen(self, event=None):
        if self.fullscreen_active:
            return True
        self._release_remote_input_state()
        self.fullscreen_active = True
        self.stack.set_visible_child_name('desktop')
        self.header.set_visible(False)
        self.connection_card.set_visible(False)
        self.stack_switcher.set_visible(False)
        self.fullscreen_bar.set_visible(True)
        self._refresh_fullscreen_bar()
        self.fullscreen()
        self.picture.grab_focus()
        self._log('INFO', 'Entered full-screen desktop view. Double-click the top bar or press Alt+Esc to exit.')
        return True

    def _fullscreen_bar_pressed(self, _gesture, n_press, _x, _y):
        if self.fullscreen_active and n_press == 2:
            self._exit_fullscreen()

    def _exit_fullscreen(self, event=None):
        if not self.fullscreen_active:
            return False
        self._release_remote_input_state()
        self.fullscreen_bar.set_visible(False)
        self.unfullscreen()
        self.header.set_visible(True)
        self.connection_card.set_visible(True)
        self.stack_switcher.set_visible(True)
        self.fullscreen_active = False
        self._log('INFO', 'Exited full-screen desktop view.')
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
        self.ui_queue.put(('log', f'{timestamp} [{level}] {clean}'))

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
        self.on_close()
        return False

    def on_close(self) -> None:
        try:
            self.disconnect_remote()
        except Exception:
            pass
        try:
            self.terminal_controller.disconnect()
        except Exception:
            pass
        try:
            self.projects_controller.cancel()
        except Exception:
            pass
        if self.tray is not None:
            self.tray.stop()
        app = self.get_application()
        if app is not None:
            app.quit()


class ClientApplication(Adw.Application):
    def __init__(self):
        super().__init__(application_id='com.ilovemyprojects.ZorinMacBridge', flags=Gio.ApplicationFlags.DEFAULT_FLAGS)
        self.window = None
        self._install_actions()

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
    Adw.init()
    app = ClientApplication()
    return app.run(sys.argv)


if __name__ == '__main__':
    raise SystemExit(main())
