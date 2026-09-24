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
from resources import resource_path
from settings import client_record
from secret_store import get_password as get_saved_password
from tray_icon import TrayController
from updates import current_version

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
        self.quality_var = ValueVar('Balanced')
        self.machine_var = ValueVar('')
        self.remember_credentials_var = ValueVar(True)

        self.discovered_by_label = {}
        self.selected_server_id = ''
        self.selected_server_name = ''
        self.remember_credentials_active = True

        self.outgoing = queue.Queue(maxsize=1000)
        self.ui_queue = queue.Queue()
        self.stop_event = threading.Event()
        self.net_thread = None
        self.video_thread = None
        self.video_frames = queue.Queue(maxsize=2)
        self.connected = False
        self.remote_image_size = (1, 1)
        self.display_rect = (0, 0, 1, 1)
        self.modifiers_down = set()
        self.keys_down = set()
        self.remote_path = ''
        self.file_entries = {}
        self.disconnect_requested = False
        self.fullscreen_active = False
        self.mouse_buttons_down = set()
        self._last_texture = None
        self._last_pixbuf = None
        self._file_rows = {}
        self._reconnect_source = 0
        self._reconnect_attempt = 0
        self._last_disconnect_reason = ''

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
        refresh = Gtk.Button(icon_name='view-refresh-symbolic', tooltip_text='Refresh LAN discovery')
        refresh.connect('clicked', lambda *_: self.discover_macs())
        first.append(refresh)

        second = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self.connection_card.append(second)
        self.ip_entry = self._entry(second, 'IP', self.ip_var, width=16)
        self.port_entry = self._entry(second, 'Port', self.port_var, width=7)
        self.password_entry = self._entry(second, 'Password', self.password_var, password=True, expand=True)
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
        self.quality_dropdown.set_selected(list(VIDEO_QUALITY_PRESETS).index('Balanced'))
        self.quality_dropdown.connect('notify::selected', self._quality_dropdown_changed)
        video_row.append(self.quality_dropdown)
        self.quality_description = Gtk.Label(label=self._quality_description_text('Balanced'), xalign=0)
        self.quality_description.add_css_class('zmb-muted')
        self.quality_description.set_hexpand(True)
        video_row.append(self.quality_description)

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
        self.logs_page = self._build_logs_page()
        self.stack.add_titled(self.desktop_page, 'desktop', 'Desktop')
        self.stack.add_titled(self.files_page, 'files', 'Files')
        self.stack.add_titled(self.logs_page, 'logs', 'Logs')
        self.stack.set_visible_child_name('desktop')

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
        toolbar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        root.append(toolbar)
        up = Gtk.Button(icon_name='go-up-symbolic', tooltip_text='Parent folder')
        up.connect('clicked', lambda *_: self.remote_up())
        toolbar.append(up)
        refresh = Gtk.Button(icon_name='view-refresh-symbolic', tooltip_text='Refresh')
        refresh.connect('clicked', lambda *_: self.refresh_files())
        toolbar.append(refresh)
        self.path_label = Gtk.Label(label='~/ZorinMac-Share/', xalign=0)
        self.path_label.add_css_class('zmb-path')
        self.path_label.set_hexpand(True)
        self.path_label.set_ellipsize(Pango.EllipsizeMode.END)
        toolbar.append(self.path_label)
        upload_file = Gtk.Button(label='Upload files')
        upload_file.connect('clicked', lambda *_: self.upload_files())
        toolbar.append(upload_file)
        upload_folder = Gtk.Button(label='Upload folder')
        upload_folder.connect('clicked', lambda *_: self.upload_folder())
        toolbar.append(upload_folder)
        download = Gtk.Button(label='Download')
        download.add_css_class('suggested-action')
        download.connect('clicked', lambda *_: self.download_selected())
        toolbar.append(download)
        mkdir = Gtk.Button(icon_name='folder-new-symbolic', tooltip_text='New folder on Mac')
        mkdir.connect('clicked', lambda *_: self.create_remote_folder())
        toolbar.append(mkdir)

        scrolled = Gtk.ScrolledWindow()
        scrolled.set_hexpand(True)
        scrolled.set_vexpand(True)
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
        profile = VIDEO_QUALITY_PRESETS.get(name, VIDEO_QUALITY_PRESETS['Balanced'])
        return f"up to {profile['max_width']} px · {profile['fps']} FPS · {profile['bitrate'] // 1_000_000} Mbit/s"

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
            self.status_var.set(f'Connected — {name} quality selected; reconnect to apply it')

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

    def connect_remote(self) -> None:
        if self.connected or (self.net_thread is not None and self.net_thread.is_alive()):
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
        self.connected = False
        self.status_var.set('Disconnected')
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
        self._process_gtk_queue()
        latest = None
        try:
            while True:
                latest = self.video_frames.get_nowait()
        except queue.Empty:
            pass
        if latest is not None:
            self._last_image = latest
            self._redraw()
        return GLib.SOURCE_CONTINUE

    def _process_gtk_queue(self) -> None:
        try:
            while True:
                item = self.ui_queue.get_nowait()
                kind = item[0]
                if kind == 'log':
                    self._append_log(item[1])
                elif kind == 'connected':
                    self.connected = True
                    self._reconnect_attempt = 0
                    self.status_var.set('Connected — view only; enable Capture keyboard & mouse to control the Mac')
                    self._log('INFO', 'Desktop session connected.')
                    self._refresh_fullscreen_bar()
                    self.refresh_files()
                elif kind == 'disconnected':
                    self.connected = False
                    reason = item[1] if len(item) > 1 else 'Session ended'
                    self.status_var.set('Disconnected' if reason == 'Disconnected by user' else f'Disconnected — {reason}')
                    self._log('INFO', f'Desktop session disconnected: {reason}')
                    if reason != 'Disconnected by user' and not self.disconnect_requested:
                        self._schedule_auto_reconnect(reason)
                elif kind == 'error':
                    self.status_var.set(f'Error: {item[1]}')
                    self._log('ERROR', item[1])
                elif kind == 'files':
                    self._show_files(item[1])
                elif kind == 'info':
                    self.status_var.set(item[1])
                elif kind == 'clipboard':
                    self._set_local_clipboard(item[1])
                elif kind == 'discovery':
                    servers = item[1]
                    self.discovered_by_label = {server.label: server for server in servers}
                    values = list(self.discovered_by_label)
                    self.machine_model.splice(0, self.machine_model.get_n_items(), values)
                    if servers:
                        self.machine_dropdown.set_selected(0)
                        self.machine_var.set(values[0])
                        self._machine_selected()
                        self.status_var.set(f'Found {len(servers)} running Mac server(s) on the LAN.')
                        self._log('INFO', f'LAN discovery found {len(servers)} server(s): ' + ', '.join(s.label for s in servers))
                    else:
                        self.status_var.set('No running ZorinMacBridge server found on the LAN.')
                        self._log('INFO', 'LAN discovery found no running servers.')
                elif kind == 'discovery_error':
                    self.status_var.set(f'LAN discovery error: {item[1]}')
                    self._log('ERROR', f'LAN discovery error: {item[1]}')
                elif kind == 'update':
                    info = item[1]
                    self._log('INFO', f'Update check completed: installed={info.current}, latest={info.latest}, available={info.available}')
                    if info.available:
                        self._confirm(
                            'ZorinMacBridge update',
                            f'Version {info.latest} is available.\nInstalled: {info.current}\n\nDownload, verify and install it now?',
                            'Install update',
                            lambda info=info: self._install_update(info),
                        )
                    else:
                        self._dialog('ZorinMacBridge update', f'You are up to date (version {info.current}).')
                        self.status_var.set('Update check completed.')
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
                    self.status_var.set('Update check failed.')
                    self._dialog('Update check failed', 'The manual update check could not reach GitHub Releases.\n\n' + item[1])
        except queue.Empty:
            pass

    def _redraw(self) -> None:
        img = getattr(self, '_last_image', None)
        if img is None:
            return
        rgb = img.convert('RGB')
        data = GLib.Bytes.new(rgb.tobytes())
        pixbuf = GdkPixbuf.Pixbuf.new_from_bytes(data, GdkPixbuf.Colorspace.RGB, False, 8, rgb.width, rgb.height, rgb.width * 3)
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
                        self.status_var.set('Text clipboard is too large (>2 MiB).')
                        return None
                    return raw.decode('utf-8', 'replace')
            except Exception:
                pass
        return None

    def _set_local_clipboard(self, text: str) -> None:
        raw = text.encode('utf-8')
        commands = []
        if os.environ.get('WAYLAND_DISPLAY') and shutil.which('wl-copy'):
            commands.append(['wl-copy'])
        if shutil.which('xclip'):
            commands.append(['xclip', '-selection', 'clipboard'])
        for command in commands:
            try:
                p = subprocess.run(command, input=raw, stderr=subprocess.DEVNULL, timeout=2, check=False)
                if p.returncode == 0:
                    self.status_var.set('Text clipboard copied Mac → Linux')
                    return
            except Exception:
                pass
        self.status_var.set('Could not access the Linux text clipboard.')

    # ---------- Logs ----------

    def _log(self, level: str, message: str) -> None:
        timestamp = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S.%f')[:-3]
        clean = str(message).replace('\r', '\\r')
        self.ui_queue.put(('log', f'{timestamp} [{level}] {clean}'))

    def _append_log(self, line: str) -> None:
        end = self.log_buffer.get_end_iter()
        self.log_buffer.insert(end, line.rstrip() + '\n')
        mark = self.log_buffer.create_mark(None, self.log_buffer.get_end_iter(), False)
        self.log_view.scroll_to_mark(mark, 0.0, False, 0.0, 1.0)

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
            if file and file.get_path():
                Path(file.get_path()).write_text(self._log_text(), encoding='utf-8')
                self.status_var.set(f'Logs saved to {file.get_path()}')
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
        child = self.file_list.get_first_child()
        while child is not None:
            nxt = child.get_next_sibling()
            self.file_list.remove(child)
            child = nxt
        for i, entry in enumerate(response.get('items', [])):
            iid = f'e{i}'
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
            ts = datetime.datetime.fromtimestamp(int(entry.get('mtime', 0))).strftime('%Y-%m-%d %H:%M')
            modified = Gtk.Label(label=ts, xalign=0)
            modified.add_css_class('zmb-muted')
            modified.set_width_chars(16)
            line.append(modified)
            row.set_child(line)
            self._file_rows[iid] = row
            self.file_list.append(row)

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
