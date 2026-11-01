#!/usr/bin/env python3
from __future__ import annotations

import threading

from gi.repository import Adw, GLib, Gtk

from zmb_admin_backend import (
    AdminError,
    get_profile,
    get_status,
    pair_device,
    reboot_readiness,
    revoke_current_device,
    run_action,
    scan_host_keys,
    status_summary,
)


STATUS_FIELDS = [
    ("macOS", "macos_version"),
    ("Architecture", "arch"),
    ("Target user", "target_user"),
    ("GUI session", "user_session_available"),
    ("System service", "admin_daemon_loaded"),
    ("Heartbeat age", "admin_heartbeat_age_seconds"),
    ("Desktop app", "zmb_app_running"),
    ("LaunchAgent", "launch_agent_loaded"),
    ("Desktop port", "zmb_port_listening"),
    ("Remote Login", "remote_login"),
    ("FileVault", "filevault"),
    ("Remote reboot unlock", "filevault_ssh_unlock_eligible"),
    ("Reboot state", "reboot_risk"),
    ("Wake on network", "wake_on_network_setting"),
    ("CPU usage", "cpu_usage_percent"),
    ("Load average", "load_average"),
    ("Memory", "memory_summary"),
    ("Memory total", "memory_total_bytes"),
    ("Disk free", "disk_root_available_bytes"),
    ("Uptime", "uptime"),
    ("Paired admin devices", "paired_devices"),
    ("Auto-recovery", "autoheal_enabled"),
    ("Recovery paused", "autoheal_paused"),
]


def _display_value(key: str, value: str) -> str:
    if value in {"1", "true"}:
        return "Yes"
    if value in {"0", "false"}:
        return "No"
    if key == "admin_heartbeat_age_seconds" and value not in {"-1", "unknown", "?"}:
        return value + " s"
    if key == "cpu_usage_percent" and value not in {"unknown", "?"}:
        return value + "%"
    if key in {"memory_total_bytes", "disk_root_available_bytes"} and value.isdigit():
        return f"{int(value) / (1024 ** 3):.1f} GiB"
    return value


class SystemController:
    def __init__(self, window):
        self.window = window
        self.busy = False
        self.last_status: dict[str, str] | None = None
        self.scanned_pairing: dict | None = None
        self.action_buttons: list[Gtk.Button] = []
        self.page = self._build_page()
        if hasattr(window, "machine_dropdown"):
            window.machine_dropdown.connect("notify::selected", self._machine_changed)
        GLib.idle_add(self.selection_changed)

    def current(self) -> tuple[str, str, str]:
        host = str(self.window.ip_var.get() or "").strip()
        sid = str(getattr(self.window, "selected_server_id", "") or "").strip()
        name = str(getattr(self.window, "selected_server_name", "") or host).strip()
        return sid, host, name

    def _profile(self):
        sid, host, _ = self.current()
        if not host:
            return None
        return get_profile(sid, host)

    def _build_page(self) -> Gtk.Widget:
        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        outer.set_margin_start(4)
        outer.set_margin_end(4)
        outer.set_margin_top(4)
        outer.set_margin_bottom(4)

        card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        card.add_css_class("zmb-card")
        outer.append(card)

        top = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        card.append(top)
        self.state_icon = Gtk.Image.new_from_icon_name("network-offline-symbolic")
        top.append(self.state_icon)
        text = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        text.set_hexpand(True)
        top.append(text)
        self.title = Gtk.Label(label="System administration", xalign=0)
        self.title.add_css_class("title-3")
        text.append(self.title)
        self.summary = Gtk.Label(label="Select a Mac to configure unattended administration.", xalign=0, wrap=True)
        self.summary.add_css_class("zmb-muted")
        text.append(self.summary)

        self.refresh_btn = Gtk.Button(icon_name="view-refresh-symbolic", tooltip_text="Refresh system status")
        self.refresh_btn.connect("clicked", lambda *_: self.refresh())
        top.append(self.refresh_btn)
        self.pair_btn = Gtk.Button(label="Pair / Configure")
        self.pair_btn.connect("clicked", lambda *_: self.open_pair_dialog())
        top.append(self.pair_btn)

        self.grid = Gtk.Grid(column_spacing=20, row_spacing=6)
        card.append(self.grid)

        action_card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        action_card.add_css_class("zmb-card")
        outer.append(action_card)
        action_title = Gtk.Label(label="Recovery and power", xalign=0)
        action_title.add_css_class("title-4")
        action_card.append(action_title)
        action_note = Gtk.Label(
            label="These controls use the paired, restricted SSH key. The key cannot open a shell or create SSH tunnels.",
            xalign=0,
            wrap=True,
        )
        action_note.add_css_class("zmb-muted")
        action_card.append(action_note)

        flow = Gtk.FlowBox(selection_mode=Gtk.SelectionMode.NONE, max_children_per_line=5,
                           min_children_per_line=2, row_spacing=8, column_spacing=8)
        action_card.append(flow)
        for label, action in [
            ("Start ZMB", "start-zmb"),
            ("Stop ZMB", "stop-zmb"),
            ("Restart ZMB", "restart-zmb"),
            ("Repair startup", "repair-launch-agent"),
            ("Logs", "logs"),
            ("Sleep", "sleep"),
            ("Restart Mac", "reboot"),
            ("Shut down", "shutdown"),
        ]:
            btn = Gtk.Button(label=label)
            btn.connect("clicked", self._action_clicked, action)
            flow.insert(btn, -1)
            self.action_buttons.append(btn)

        security_card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        security_card.add_css_class("zmb-card")
        outer.append(security_card)
        security_title = Gtk.Label(label="Pairing", xalign=0)
        security_title.add_css_class("title-4")
        security_card.append(security_title)
        self.pairing_label = Gtk.Label(label="Not paired", xalign=0, wrap=True, selectable=True)
        security_card.append(self.pairing_label)
        unpair = Gtk.Button(label="Revoke this Zorin device")
        unpair.add_css_class("destructive-action")
        unpair.set_halign(Gtk.Align.START)
        unpair.connect("clicked", lambda *_: self._confirm_unpair())
        security_card.append(unpair)
        self.unpair_btn = unpair

        scroller = Gtk.ScrolledWindow()
        scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroller.set_hexpand(True)
        scroller.set_vexpand(True)
        scroller.set_child(outer)
        return scroller

    def _machine_changed(self, *_args) -> None:
        GLib.idle_add(self.selection_changed)

    def selection_changed(self):
        sid, host, name = self.current()
        profile = get_profile(sid, host) if host else None
        if not host:
            self.summary.set_text("Select a Mac first.")
            self._set_actions(False)
            self.pairing_label.set_text("No Mac selected")
            return False
        if profile:
            self.pairing_label.set_text(
                f"Paired as {profile.get('user', '?')}@{host} · device {profile.get('device_id', '?')}"
            )
            self._set_actions(True)
            self.summary.set_text(f"{name or host} is paired. Refreshing system status…")
            self.refresh()
        else:
            self.last_status = None
            self._clear_grid()
            self.title.set_text(name or host)
            self.summary.set_text("Not paired for unattended System administration. Desktop connection can still work normally.")
            self.pairing_label.set_text("Not paired")
            self.state_icon.set_from_icon_name("network-offline-symbolic")
            self._set_actions(False)
        return False

    def _set_actions(self, enabled: bool) -> None:
        for btn in self.action_buttons:
            btn.set_sensitive(enabled and not self.busy)
        self.unpair_btn.set_sensitive(enabled and not self.busy)
        self.refresh_btn.set_sensitive(not self.busy)
        self.pair_btn.set_sensitive(not self.busy)

    def _set_busy(self, busy: bool, message: str | None = None) -> None:
        self.busy = busy
        if message:
            self.summary.set_text(message)
        self._set_actions(self._profile() is not None)

    def _clear_grid(self) -> None:
        child = self.grid.get_first_child()
        while child is not None:
            nxt = child.get_next_sibling()
            self.grid.remove(child)
            child = nxt

    def _render_status(self, data: dict[str, str] | None, error: str | None):
        self._set_busy(False)
        if error:
            self.last_status = None
            self.state_icon.set_from_icon_name("dialog-warning-symbolic")
            self.summary.set_text(error)
            self.window._log("ERROR", f"System admin: {error}")
            return False
        assert data is not None
        self.last_status = data
        self.title.set_text(data.get("hostname") or self.current()[2] or "Mac")
        summary = status_summary(data)
        self.summary.set_text(summary)
        icon = "emblem-ok-symbolic" if summary.startswith("READY") else "dialog-information-symbolic"
        self.state_icon.set_from_icon_name(icon)
        self._clear_grid()
        for row, (label, key) in enumerate(STATUS_FIELDS):
            left = Gtk.Label(label=label, xalign=0)
            left.add_css_class("zmb-muted")
            right = Gtk.Label(label=_display_value(key, data.get(key, "?")), xalign=0, selectable=True, wrap=True)
            self.grid.attach(left, 0, row, 1, 1)
            self.grid.attach(right, 1, row, 1, 1)
        return False

    def refresh(self) -> None:
        if self.busy:
            return
        sid, host, _ = self.current()
        if not host or not get_profile(sid, host):
            self.selection_changed()
            return
        self._set_busy(True, "Reading Mac system status…")

        def worker():
            try:
                data = get_status(server_id=sid, host=host)
                GLib.idle_add(self._render_status, data, None)
            except Exception as exc:
                GLib.idle_add(self._render_status, None, str(exc))

        threading.Thread(target=worker, daemon=True).start()

    def _action_clicked(self, _button, action: str) -> None:
        if self.busy:
            return
        if action == "reboot":
            data = self.last_status
            if data is None:
                self.window._dialog("Restart Mac", "Refresh System status first so FileVault risk can be checked.")
                return
            safe, notes = reboot_readiness(data)
            if not safe:
                self.window._dialog("Restart blocked", "\n".join(notes) + "\n\nThe GUI will not force a risky reboot.", destructive=True)
                return
            self.window._confirm("Restart Mac", "\n".join(notes) + "\n\nRestart the Mac now?", "Restart", lambda: self._run_action(action))
            return
        if action == "shutdown":
            self.window._confirm("Shut down Mac", "The Mac will become unavailable until it is powered on again.", "Shut down", lambda: self._run_action(action))
            return
        if action == "sleep":
            self.window._confirm("Sleep Mac", "Put the Mac to sleep now?", "Sleep", lambda: self._run_action(action))
            return
        self._run_action(action)

    def _run_action(self, action: str) -> None:
        sid, host, _ = self.current()
        self._set_busy(True, f"Running {action}…")

        def worker():
            try:
                proc = run_action(server_id=sid, host=host, action=action, timeout=30)
                if proc.returncode != 0:
                    raise AdminError((proc.stderr or proc.stdout or f"{action} exited {proc.returncode}").strip())
                output = (proc.stdout or "").strip()
                GLib.idle_add(done, None, output)
            except Exception as exc:
                GLib.idle_add(done, str(exc), "")

        def done(error: str | None, output: str):
            self._set_busy(False)
            if error:
                self.window._dialog("System action failed", error, destructive=True)
                self.window._log("ERROR", f"System action {action}: {error}")
                return False
            self.window._log("INFO", f"System action {action}: OK")
            if action == "logs":
                self._show_text("ZorinMacBridge system logs", output or "No log output")
            elif action in {"reboot", "shutdown", "sleep"}:
                self.summary.set_text(f"{action}: command accepted by Mac")
            else:
                GLib.timeout_add(1000, lambda: (self.refresh(), False)[1])
            return False

        threading.Thread(target=worker, daemon=True).start()

    def _show_text(self, title: str, text: str) -> None:
        win = Gtk.Window(title=title, transient_for=self.window, default_width=820, default_height=520)
        scroller = Gtk.ScrolledWindow()
        view = Gtk.TextView(editable=False, monospace=True, wrap_mode=Gtk.WrapMode.NONE)
        view.get_buffer().set_text(text)
        scroller.set_child(view)
        win.set_child(scroller)
        win.present()

    def open_pair_dialog(self) -> None:
        sid, host, name = self.current()
        if not host:
            self.window._dialog("Pair Mac", "Select or enter the Mac address first.")
            return
        existing = get_profile(sid, host) or {}
        dialog = Gtk.Window(title="Pair System administration", transient_for=self.window, modal=True,
                            default_width=620, default_height=500)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10,
                      margin_top=18, margin_bottom=18, margin_start=18, margin_end=18)
        dialog.set_child(box)

        title = Gtk.Label(label=f"Pair {name or host}", xalign=0)
        title.add_css_class("title-2")
        box.append(title)
        note = Gtk.Label(
            label="This is a one-time setup. The macOS account password is used only to install a restricted SSH key and is not saved.",
            xalign=0, wrap=True,
        )
        note.add_css_class("zmb-muted")
        box.append(note)

        grid = Gtk.Grid(column_spacing=10, row_spacing=8)
        box.append(grid)
        host_entry = Gtk.Entry(text=host, hexpand=True)
        host_entry.set_editable(False)
        host_entry.set_tooltip_text("Taken from the Mac selected in the main ZorinMacBridge window")
        port_entry = Gtk.Entry(text=str(existing.get("ssh_port", 22)), width_chars=6)
        user_entry = Gtk.Entry(text=str(existing.get("user", "")), hexpand=True)
        password_entry = Gtk.Entry(hexpand=True)
        password_entry.set_visibility(False)
        password_entry.set_input_purpose(Gtk.InputPurpose.PASSWORD)
        for row, (label, widget) in enumerate([
            ("Selected Mac", host_entry), ("SSH port", port_entry), ("macOS user", user_entry), ("macOS password", password_entry)
        ]):
            grid.attach(Gtk.Label(label=label, xalign=0), 0, row, 1, 1)
            grid.attach(widget, 1, row, 1, 1)

        fingerprint = Gtk.Label(label="SSH fingerprint has not been read yet.", xalign=0, wrap=True, selectable=True)
        box.append(fingerprint)
        verified = Gtk.CheckButton(label="I verified this SSH fingerprint against the Mac")
        verified.set_sensitive(False)
        box.append(verified)

        buttons = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        buttons.set_halign(Gtk.Align.END)
        box.append(buttons)
        cancel = Gtk.Button(label="Cancel")
        cancel.connect("clicked", lambda *_: dialog.close())
        buttons.append(cancel)
        scan_btn = Gtk.Button(label="Read fingerprint")
        buttons.append(scan_btn)
        pair_btn = Gtk.Button(label="Pair this Mac")
        pair_btn.add_css_class("suggested-action")
        pair_btn.set_sensitive(False)
        buttons.append(pair_btn)

        state: dict[str, object] = {"scan": None, "busy": False}

        def invalidate_scan(*_args):
            if state.get("scan") is not None:
                state["scan"] = None
                verified.set_active(False)
                verified.set_sensitive(False)
                fingerprint.set_text("SSH port changed. Read the fingerprint again before pairing.")
            set_pair_sensitive()

        def set_pair_sensitive(*_args):
            pair_btn.set_sensitive(bool(state.get("scan")) and verified.get_active() and not state.get("busy"))

        verified.connect("toggled", set_pair_sensitive)
        port_entry.connect("changed", invalidate_scan)

        def do_scan(*_args):
            if state.get("busy"):
                return
            try:
                ssh_port = int(port_entry.get_text().strip())
            except ValueError:
                self.window._dialog("Pair Mac", "SSH port must be a number.")
                return
            state["busy"] = True
            scan_btn.set_sensitive(False)
            pair_btn.set_sensitive(False)
            fingerprint.set_text("Reading SSH host key from the Mac…")

            def worker():
                try:
                    result = scan_host_keys(host_entry.get_text().strip(), ssh_port)
                    GLib.idle_add(scan_done, result, None)
                except Exception as exc:
                    GLib.idle_add(scan_done, None, str(exc))

            threading.Thread(target=worker, daemon=True).start()

        def scan_done(result, error):
            state["busy"] = False
            scan_btn.set_sensitive(True)
            if error:
                state["scan"] = None
                verified.set_sensitive(False)
                verified.set_active(False)
                fingerprint.set_text(error)
                set_pair_sensitive()
                return False
            state["scan"] = result
            pref = result["preferred"]
            fingerprint.set_text(
                f"Mac SSH fingerprint ({pref['type']}):\n{pref['fingerprint']}\n\n"
                "Compare this with the fingerprint printed by the P0 installer on the Mac."
            )
            verified.set_sensitive(True)
            verified.set_active(False)
            set_pair_sensitive()
            return False

        def do_pair(*_args):
            result = state.get("scan")
            if not isinstance(result, dict) or not verified.get_active():
                return
            user = user_entry.get_text().strip()
            password = password_entry.get_text()
            try:
                ssh_port = int(port_entry.get_text().strip())
                desktop_port = int(str(self.window.port_var.get() or "45950").strip())
            except ValueError:
                self.window._dialog("Pair Mac", "SSH and desktop ports must be numbers.")
                return
            if not user or not password:
                self.window._dialog("Pair Mac", "Enter the macOS short username and account password.")
                return
            state["busy"] = True
            pair_btn.set_sensitive(False)
            scan_btn.set_sensitive(False)
            fingerprint.set_text("Installing restricted device key and testing P0 service…")
            pair_host = host_entry.get_text().strip()

            def worker():
                try:
                    profile = pair_device(
                        server_id=sid,
                        server_name=name,
                        host=pair_host,
                        user=user,
                        password=password,
                        ssh_port=ssh_port,
                        desktop_port=desktop_port,
                        scanned_host_keys=[result["preferred"]["line"]],
                    )
                    GLib.idle_add(pair_done, profile, None)
                except Exception as exc:
                    GLib.idle_add(pair_done, None, str(exc))

            threading.Thread(target=worker, daemon=True).start()

        def pair_done(profile, error):
            state["busy"] = False
            password_entry.set_text("")
            if error:
                scan_btn.set_sensitive(True)
                fingerprint.set_text("Pairing failed:\n" + error)
                set_pair_sensitive()
                return False
            dialog.close()
            self.window._log("INFO", f"System administration paired with {profile.get('server_name') or profile.get('host')}.")
            self.selection_changed()
            return False

        scan_btn.connect("clicked", do_scan)
        pair_btn.connect("clicked", do_pair)
        dialog.present()

    def _confirm_unpair(self) -> None:
        if not self._profile():
            return
        self.window._confirm(
            "Revoke Zorin device",
            "Remove this Zorin machine's restricted administration key from the Mac? Desktop trust/password settings are not changed.",
            "Revoke",
            self._unpair,
        )

    def _unpair(self) -> None:
        sid, host, _ = self.current()
        self._set_busy(True, "Revoking administration key…")

        def worker():
            try:
                revoke_current_device(server_id=sid, host=host)
                GLib.idle_add(done, None)
            except Exception as exc:
                GLib.idle_add(done, str(exc))

        def done(error):
            self._set_busy(False)
            if error:
                self.window._dialog("Revoke failed", error, destructive=True)
            else:
                self.window._log("INFO", "System administration device key revoked.")
                self.selection_changed()
            return False

        threading.Thread(target=worker, daemon=True).start()


def build_system_page(window) -> Gtk.Widget:
    """Called by the main ZorinMacBridge GTK client; returns its integrated System tab."""
    controller = SystemController(window)
    window.zmb_system_controller = controller
    return controller.page
