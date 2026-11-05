#!/usr/bin/env python3
from __future__ import annotations

import os
import threading

import gi
from gi.repository import GLib, Gtk

from zmb_shell_backend import get_shell_profile, shell_ready, shell_ssh_argv, run_shell_command

try:
    gi.require_version("Vte", "3.91")
    from gi.repository import Vte
except (ValueError, ImportError):
    Vte = None


class TerminalController:
    """Interactive user-level SSH terminal.

    P3 deliberately does not reuse the P0 ForcedCommand key. This terminal uses
    a separate Ed25519 key installed into the paired macOS user's account and has
    no ZorinMacBridge sudoers privileges.
    """

    def __init__(self, window):
        self.window = window
        self.child_pid = -1
        self.spawn_serial = 0
        self.page = self._build_page()
        if hasattr(window, "machine_dropdown"):
            window.machine_dropdown.connect("notify::selected", self._selection_changed)
        GLib.idle_add(self.selection_changed)

    def current(self):
        host = str(self.window.ip_var.get() or "").strip()
        sid = str(getattr(self.window, "selected_server_id", "") or "").strip()
        name = str(getattr(self.window, "selected_server_name", "") or host).strip()
        return sid, host, name

    def _profile(self):
        sid, host, _ = self.current()
        return get_shell_profile(sid, host) if host else None

    def _build_page(self):
        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        outer.set_margin_start(4); outer.set_margin_end(4); outer.set_margin_top(4); outer.set_margin_bottom(4)

        bar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        outer.append(bar)
        self.summary = Gtk.Label(label="Select a paired Mac.", xalign=0, wrap=True)
        self.summary.set_hexpand(True)
        self.summary.add_css_class("zmb-muted")
        bar.append(self.summary)
        self.enable_btn = Gtk.Button(label="Enable Terminal")
        self.enable_btn.connect("clicked", self._enable_terminal)
        bar.append(self.enable_btn)
        self.connect_btn = Gtk.Button(label="Connect")
        self.connect_btn.add_css_class("suggested-action")
        self.connect_btn.connect("clicked", lambda *_: self.connect())
        bar.append(self.connect_btn)
        self.disconnect_btn = Gtk.Button(label="Disconnect")
        self.disconnect_btn.connect("clicked", lambda *_: self.disconnect())
        bar.append(self.disconnect_btn)

        if Vte is not None:
            self.terminal = Vte.Terminal()
            self.terminal.set_hexpand(True)
            self.terminal.set_vexpand(True)
            self.terminal.set_scrollback_lines(20000)
            self.terminal.set_scroll_on_keystroke(True)
            self.terminal.set_scroll_on_output(False)
            self.terminal.set_input_enabled(True)
            self.terminal.connect("child-exited", self._child_exited)
            scroller = Gtk.ScrolledWindow()
            scroller.set_hexpand(True); scroller.set_vexpand(True)
            scroller.set_child(self.terminal)
            outer.append(scroller)
            self.fallback_box = None
        else:
            self.terminal = None
            info = Gtk.Label(
                label=(
                    "GTK4 VTE is not installed, so the full PTY terminal is unavailable. "
                    "The command runner below still uses the same pinned user SSH key. "
                    "Install gir1.2-vte-3.91 and libvte-2.91-gtk4-0 for the interactive terminal."
                ),
                xalign=0, wrap=True,
            )
            info.add_css_class("zmb-muted")
            outer.append(info)
            self.fallback_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
            outer.append(self.fallback_box)
            row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
            self.fallback_box.append(row)
            self.command_entry = Gtk.Entry(hexpand=True, placeholder_text="Command to run as the macOS user")
            self.command_entry.connect("activate", self._run_fallback)
            row.append(self.command_entry)
            run = Gtk.Button(label="Run")
            run.connect("clicked", self._run_fallback)
            row.append(run)
            scroll = Gtk.ScrolledWindow(vexpand=True)
            self.fallback_output = Gtk.TextView(editable=False, monospace=True, wrap_mode=Gtk.WrapMode.CHAR)
            scroll.set_child(self.fallback_output)
            self.fallback_box.append(scroll)

        self._set_controls()
        return outer

    def _selection_changed(self, *_args):
        self.disconnect()
        GLib.idle_add(self.selection_changed)

    def selection_changed(self):
        profile = self._profile()
        _sid, host, name = self.current()
        if not host or not profile:
            self.summary.set_text("Pair this Mac in System before using Terminal.")
        elif not shell_ready(profile):
            self.summary.set_text(f"{name or host} is paired for System, but Terminal / Project Actions are not enabled yet.")
        else:
            self.summary.set_text(f"User terminal ready: {profile.get('user', '?')}@{host}. This shell is user-level, not root.")
        self._set_controls()
        return False

    def _set_controls(self):
        profile = self._profile()
        ready = shell_ready(profile)
        connected = self.child_pid > 0
        self.enable_btn.set_sensitive(profile is not None and not connected)
        self.connect_btn.set_sensitive(ready and not connected and self.terminal is not None)
        self.disconnect_btn.set_sensitive(connected)
        if self.fallback_box is not None:
            self.fallback_box.set_sensitive(ready)

    def _enable_terminal(self, *_args):
        # Reuse the already-audited pairing dialog. It now has a checkbox that
        # installs the separate user shell key during the one-time password login.
        try:
            self.window.system_controller.open_pair_dialog()
        except Exception as exc:
            self.window._dialog("Enable Terminal", str(exc), destructive=True)

    def connect(self):
        if self.terminal is None or self.child_pid > 0:
            return
        profile = self._profile()
        if not shell_ready(profile):
            self.selection_changed()
            return
        try:
            argv = shell_ssh_argv(profile, tty=True)
        except Exception as exc:
            self.window._dialog("Terminal", str(exc), destructive=True)
            return
        self.spawn_serial += 1
        serial = self.spawn_serial
        self.summary.set_text(f"Connecting terminal to {profile.get('user')}@{profile.get('host')}…")
        self.connect_btn.set_sensitive(False)

        def spawned(_terminal, pid, error, _user_data=None):
            if serial != self.spawn_serial:
                return
            if error is not None or int(pid) <= 0:
                self.child_pid = -1
                self.summary.set_text(f"Terminal connection failed: {error}")
                self.window._log("ERROR", f"Terminal spawn failed: {error}")
                self._set_controls()
                return
            self.child_pid = int(pid)
            self.summary.set_text(f"Connected terminal: {profile.get('user')}@{profile.get('host')}")
            self.window._log("INFO", f"Terminal connected to {profile.get('host')}")
            self._set_controls()

        try:
            self.terminal.spawn_async(
                Vte.PtyFlags.DEFAULT,
                os.path.expanduser("~"),
                argv,
                None,
                GLib.SpawnFlags.DEFAULT,
                None,
                15000,
                None,
                spawned,
                None,
            )
        except Exception as exc:
            self.child_pid = -1
            self.summary.set_text(f"Terminal connection failed: {exc}")
            self.window._log("ERROR", f"Terminal spawn failed: {exc}")
            self._set_controls()

    def disconnect(self):
        self.spawn_serial += 1
        if self.child_pid > 0:
            try:
                os.kill(self.child_pid, 15)
            except ProcessLookupError:
                pass
            except Exception:
                pass
        self.child_pid = -1
        if self.terminal is not None:
            try:
                self.terminal.reset(True, True)
            except Exception:
                pass
        self._set_controls()

    def _child_exited(self, _terminal, status):
        self.child_pid = -1
        self.summary.set_text(f"Terminal disconnected (status {status}).")
        self._set_controls()

    def _run_fallback(self, *_args):
        if self.fallback_box is None:
            return
        command = self.command_entry.get_text().strip()
        profile = self._profile()
        if not command or not shell_ready(profile):
            return
        self.command_entry.set_text("")
        self.fallback_box.set_sensitive(False)
        self.summary.set_text("Running terminal command…")

        def worker():
            try:
                proc = run_shell_command(profile, command, timeout=120)
                text = (proc.stdout or "") + (proc.stderr or "")
                GLib.idle_add(done, text, None, proc.returncode)
            except Exception as exc:
                GLib.idle_add(done, "", str(exc), -1)

        def done(text, error, code):
            buf = self.fallback_output.get_buffer()
            if error:
                buf.set_text(error)
                self.summary.set_text("Command failed.")
            else:
                buf.set_text(text or f"Command exited with {code}.\n")
                self.summary.set_text(f"Command finished with exit status {code}.")
            self._set_controls()
            return False

        threading.Thread(target=worker, daemon=True).start()
