#!/usr/bin/env python3
from __future__ import annotations

import threading

from gi.repository import Adw, GLib, Gtk

from zmb_admin_backend import (
    AdminError,
    get_profile,
    get_status,
    pair_device,
    machine_state,
    reboot_readiness,
    revoke_current_device,
    run_action,
    scan_host_keys,
    status_summary,
)

from zmb_shell_backend import get_shell_profile, install_shell_access, revoke_shell_access, shell_ready
from zmb_recovery import classify_transport_error, parse_recovery_report, recovery_summary, render_recovery_report


STATUS_FIELDS = [
    ("State", "machine_state"),
    ("Hostname", "hostname"),
    ("Machine ID", "server_id"),
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
    ("Power source", "power_source"),
    ("Battery", "battery_percent"),
    ("Battery detail", "battery_detail"),
    ("Xcode developer dir", "xcode_developer_dir"),
    ("CPU usage", "cpu_usage_percent"),
    ("Load average", "load_average"),
    ("Memory", "memory_summary"),
    ("Memory total", "memory_total_bytes"),
    ("Disk free", "disk_root_available_bytes"),
    ("Uptime", "uptime"),
    ("Paired admin devices", "paired_devices"),
    ("Auto-recovery", "autoheal_enabled"),
    ("Recovery paused", "autoheal_paused"),
    ("Auto update", "auto_update_mode"),
    ("Installed ZMB", "update_current"),
    ("Latest ZMB", "update_latest"),
    ("Update available", "update_available"),
    ("Last update result", "update_last_result"),
]


def _display_value(key: str, value: str) -> str:
    if value in {"1", "true"}:
        return "Yes"
    if value in {"0", "false"}:
        return "No"
    if key == "admin_heartbeat_age_seconds" and value not in {"-1", "unknown", "?"}:
        return value + " s"
    if key in {"cpu_usage_percent", "battery_percent"} and value not in {"unknown", "?"}:
        return value + "%"
    if key in {"memory_total_bytes", "disk_root_available_bytes"} and value.isdigit():
        return f"{int(value) / (1024 ** 3):.1f} GiB"
    return value


class SystemController:
    def __init__(self, window):
        self.window = window
        self.busy = False
        self._operation_serial = 0
        self._operation_watchdog = 0
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

        self.spinner = Gtk.Spinner()
        self.spinner.set_tooltip_text("System operation in progress")
        top.append(self.spinner)
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
            ("Recover desktop", "recover-desktop"),
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

        wake_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        action_card.append(wake_row)
        wake_row.append(Gtk.Label(label="Wake for network access", xalign=0))
        wake_note = Gtk.Label(label="Configures macOS network-wake support while the Mac is online; actual wake capability depends on the Mac/network.", xalign=0, wrap=True)
        wake_note.add_css_class("zmb-muted")
        wake_note.set_hexpand(True)
        wake_row.append(wake_note)
        wake_on = Gtk.Button(label="Enable")
        wake_off = Gtk.Button(label="Disable")
        wake_on.connect("clicked", lambda *_: self._run_action("set-wake-network", arg="on"))
        wake_off.connect("clicked", lambda *_: self._run_action("set-wake-network", arg="off"))
        wake_row.append(wake_on)
        wake_row.append(wake_off)
        self.action_buttons.extend([wake_on, wake_off])

        recovery_card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        recovery_card.add_css_class("zmb-card")
        outer.append(recovery_card)
        recovery_title = Gtk.Label(label="P6 diagnostics and recovery", xalign=0)
        recovery_title.add_css_class("title-4")
        recovery_card.append(recovery_title)
        self.recovery_summary_label = Gtk.Label(
            label="Run diagnostics to identify the failed layer before repairing it.",
            xalign=0, wrap=True, selectable=True,
        )
        self.recovery_summary_label.add_css_class("zmb-muted")
        recovery_card.append(self.recovery_summary_label)
        recovery_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        recovery_card.append(recovery_row)
        diagnose_btn = Gtk.Button(label="Run diagnostics")
        diagnose_btn.connect("clicked", lambda *_: self._run_action(
            "diagnose", backend_timeout=20, watchdog_ms=30000,
            operation_message="Running layered P6 recovery diagnostics…",
        ))
        recovery_row.append(diagnose_btn)
        recover_all_btn = Gtk.Button(label="Recover all safe layers")
        recover_all_btn.add_css_class("suggested-action")
        recover_all_btn.connect("clicked", lambda *_: self._confirm_recover_all())
        recovery_row.append(recover_all_btn)
        self.action_buttons.extend([diagnose_btn, recover_all_btn])

        update_card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        update_card.add_css_class("zmb-card")
        outer.append(update_card)
        update_title = Gtk.Label(label="ZorinMacBridge updates", xalign=0)
        update_title.add_css_class("title-4")
        update_card.append(update_title)
        self.update_summary = Gtk.Label(
            label="Update status will appear after the Mac is paired.", xalign=0, wrap=True
        )
        self.update_summary.add_css_class("zmb-muted")
        update_card.append(self.update_summary)

        update_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        update_card.append(update_row)
        update_row.append(Gtk.Label(label="Automatic updates", xalign=0))
        self.update_mode_model = Gtk.StringList.new(["Off", "Notify only", "Install automatically"])
        self.update_mode_dropdown = Gtk.DropDown(model=self.update_mode_model)
        self.update_mode_dropdown.set_selected(1)
        self.update_mode_dropdown.set_tooltip_text(
            "Off: no background checks. Notify: check every 6 hours. Install automatically: verify and install new official releases every 6 hours."
        )
        update_row.append(self.update_mode_dropdown)
        self.update_mode_btn = Gtk.Button(label="Save mode")
        self.update_mode_btn.connect("clicked", lambda *_: self._save_update_mode())
        update_row.append(self.update_mode_btn)
        self.check_update_btn = Gtk.Button(label="Check update")
        self.check_update_btn.connect("clicked", lambda *_: self._run_action(
            "check-update", backend_timeout=30, watchdog_ms=42000,
            operation_message="Checking the Mac for a ZorinMacBridge update…",
        ))
        update_row.append(self.check_update_btn)
        self.update_now_btn = Gtk.Button(label="Update now")
        self.update_now_btn.add_css_class("suggested-action")
        self.update_now_btn.connect("clicked", lambda *_: self._confirm_update_now())
        update_row.append(self.update_now_btn)
        self.update_controls = [self.update_mode_dropdown, self.update_mode_btn, self.check_update_btn, self.update_now_btn]

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
        # A System worker may still be finishing for the previously selected Mac.
        # Invalidate its token immediately so a late callback can never render
        # status/action results into the newly selected machine.
        self._cancel_operation_watchdog()
        self._operation_serial += 1
        if self.busy:
            self._set_busy(False)
        self.last_status = None
        GLib.idle_add(self.selection_changed)

    def selection_changed(self):
        sid, host, name = self.current()
        self.recovery_summary_label.set_text("Run diagnostics to identify the failed layer before repairing it.")
        profile = get_profile(sid, host) if host else None
        if not host:
            self.summary.set_text("Select a Mac first.")
            self._set_actions(False)
            self.pairing_label.set_text("No Mac selected")
            return False
        if profile:
            shell_state = "terminal enabled" if shell_ready(profile) else "terminal disabled"
            self.pairing_label.set_text(
                f"Paired as {profile.get('user', '?')}@{host} · device {profile.get('device_id', '?')} · {shell_state}"
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
        for widget in getattr(self, "update_controls", []):
            widget.set_sensitive(enabled and not self.busy)
        self.refresh_btn.set_sensitive(not self.busy)
        self.pair_btn.set_sensitive(not self.busy)

    def _set_busy(self, busy: bool, message: str | None = None) -> None:
        self.busy = busy
        if busy:
            self.spinner.start()
        else:
            self.spinner.stop()
        if message:
            self.summary.set_text(message)
        self._set_actions(self._profile() is not None)

    def _cancel_operation_watchdog(self) -> None:
        if self._operation_watchdog:
            try:
                GLib.source_remove(self._operation_watchdog)
            except Exception:
                pass
            self._operation_watchdog = 0

    def _begin_operation(self, message: str, *, timeout_ms: int, timeout_message: str, show_dialog: bool = True) -> int:
        """Start a UI operation with an independent GTK watchdog.

        Backend calls also have hard subprocess timeouts. This second watchdog is
        deliberately independent: even if a worker thread or child process ever
        misbehaves, the GTK controls are released and a late result is ignored.
        """
        self._cancel_operation_watchdog()
        self._operation_serial += 1
        token = self._operation_serial
        self._set_busy(True, message)

        def expired():
            if token != self._operation_serial:
                return GLib.SOURCE_REMOVE
            self._operation_watchdog = 0
            self._operation_serial += 1  # invalidate any late worker callback
            self._set_busy(False)
            self.state_icon.set_from_icon_name("dialog-warning-symbolic")
            self.summary.set_text(timeout_message)
            self.window._log("ERROR", "System admin timeout: " + timeout_message)
            if show_dialog:
                self.window._dialog("System operation timed out", timeout_message, destructive=True)
            return GLib.SOURCE_REMOVE

        self._operation_watchdog = GLib.timeout_add(timeout_ms, expired)
        return token

    def _finish_operation(self, token: int) -> bool:
        if token != self._operation_serial:
            return False
        self._cancel_operation_watchdog()
        self._set_busy(False)
        return True

    def _clear_grid(self) -> None:
        child = self.grid.get_first_child()
        while child is not None:
            nxt = child.get_next_sibling()
            self.grid.remove(child)
            child = nxt

    def _render_status(self, data: dict[str, str] | None, error: str | None):
        if error:
            self.last_status = None
            self.state_icon.set_from_icon_name("dialog-warning-symbolic")
            self.summary.set_text(error)
            issue = classify_transport_error(error)
            self.recovery_summary_label.set_text(f"{issue.code} — {issue.summary}")
            self.window._log("ERROR", f"System admin: {error}")
            return False
        assert data is not None
        self.last_status = data
        self.title.set_text(data.get("hostname") or self.current()[2] or "Mac")
        state = machine_state(data)
        data = {**data, "machine_state": state}
        self.last_status = data
        summary = status_summary(data)
        self.summary.set_text(f"{state.replace('_', ' ')} — {summary}")
        icon = "emblem-ok-symbolic" if state == "DESKTOP_READY" else ("dialog-warning-symbolic" if state in {"DEGRADED", "RECOVERY_NEEDED"} else "dialog-information-symbolic")
        self.state_icon.set_from_icon_name(icon)
        self._clear_grid()
        for row, (label, key) in enumerate(STATUS_FIELDS):
            left = Gtk.Label(label=label, xalign=0)
            left.add_css_class("zmb-muted")
            right = Gtk.Label(label=_display_value(key, data.get(key, "?")), xalign=0, selectable=True, wrap=True)
            self.grid.attach(left, 0, row, 1, 1)
            self.grid.attach(right, 1, row, 1, 1)

        mode = data.get("auto_update_mode", "notify")
        self.update_mode_dropdown.set_selected({"off": 0, "notify": 1, "install": 2}.get(mode, 1))
        current = data.get("update_current", "unknown")
        latest = data.get("update_latest", "unknown")
        available = data.get("update_available", "unknown")
        result = data.get("update_last_result", "never")
        error_text = data.get("update_last_error", "").strip()
        if available == "1":
            update_text = f"Update available: {current} → {latest}. Last check: {result}."
        elif available == "0":
            update_text = f"Mac ZMB is up to date ({current or latest}). Last check: {result}."
        else:
            update_text = f"Update status: {result}. Use Check update to query GitHub now."
        if error_text:
            update_text += " Error: " + error_text
        self.update_summary.set_text(update_text)
        return False

    def refresh(self) -> None:
        if self.busy:
            return
        sid, host, _ = self.current()
        if not host or not get_profile(sid, host):
            self.selection_changed()
            return
        token = self._begin_operation(
            "Reading Mac system status…",
            timeout_ms=18000,
            timeout_message="The Mac did not answer the System status request within 18 seconds. The GUI has been released; check that the Mac is awake and SSH/Remote Login is reachable.",
            show_dialog=False,
        )

        def worker():
            try:
                data = get_status(server_id=sid, host=host)
                GLib.idle_add(done, token, data, None)
            except Exception as exc:
                GLib.idle_add(done, token, None, str(exc))

        def done(op_token: int, data, error):
            if not self._finish_operation(op_token):
                return False
            return self._render_status(data, error)

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

    def _run_action(
        self, action: str, *, arg: str | None = None, backend_timeout: int = 18,
        watchdog_ms: int = 26000, operation_message: str | None = None,
    ) -> None:
        sid, host, _ = self.current()
        seconds = max(1, watchdog_ms // 1000)
        token = self._begin_operation(
            operation_message or f"Running {action}…",
            timeout_ms=watchdog_ms,
            timeout_message=f"The Mac did not finish '{action}' within {seconds} seconds. The GUI has been released. You can retry after checking that the Mac is awake and reachable.",
        )

        def worker():
            try:
                proc = run_action(server_id=sid, host=host, action=action, arg=arg, timeout=backend_timeout)
                if proc.returncode != 0:
                    raise AdminError((proc.stderr or proc.stdout or f"{action} exited {proc.returncode}").strip())
                output = (proc.stdout or "").strip()
                GLib.idle_add(done, token, None, output)
            except Exception as exc:
                GLib.idle_add(done, token, str(exc), "")

        def done(op_token: int, error: str | None, output: str):
            if not self._finish_operation(op_token):
                return False
            if error:
                issue = classify_transport_error(error)
                self.recovery_summary_label.set_text(f"{issue.code} — {issue.summary}")
                self.window._dialog("System action failed", error, destructive=True)
                self.window._log("ERROR", f"System action {action}: {error}")
                return False
            self.window._log("INFO", f"System action {action}: OK")
            if action in {"diagnose", "recover-all"}:
                meta, issues = parse_recovery_report(output)
                summary_text = recovery_summary(meta, issues)
                self.recovery_summary_label.set_text(summary_text)
                self._show_text(
                    "ZorinMacBridge P6 diagnostics" if action == "diagnose" else "ZorinMacBridge P6 recovery result",
                    render_recovery_report(meta, issues),
                )
                GLib.timeout_add(700, lambda: (self.refresh(), False)[1])
            elif action == "logs":
                self._show_text("ZorinMacBridge system logs", output or "No log output")
            elif action in {"reboot", "shutdown", "sleep"}:
                self.summary.set_text(f"{action}: command accepted by Mac")
            elif action == "update-now":
                self.window._dialog("Mac update", output or "Update command completed.")
                GLib.timeout_add(1800, lambda: (self.refresh(), False)[1])
            elif action in {"check-update", "set-auto-update"}:
                GLib.timeout_add(500, lambda: (self.refresh(), False)[1])
            else:
                GLib.timeout_add(1000, lambda: (self.refresh(), False)[1])
            return False

        threading.Thread(target=worker, daemon=True).start()

    def _confirm_recover_all(self) -> None:
        self.window._confirm(
            "Recover ZorinMacBridge",
            "Run the bounded P6 repair sequence? It can repair P0/LaunchAgent state, clear a stale updater lock, restore a verified update backup when the installed app is missing/invalid, and restart the desktop service. It will NOT change FileVault, user accounts, or force-enable Remote Login.",
            "Recover",
            lambda: self._run_action(
                "recover-all", backend_timeout=65, watchdog_ms=85000,
                operation_message="Recovering safe ZorinMacBridge layers and verifying the result…",
            ),
        )

    def _selected_update_mode(self) -> str:
        return {0: "off", 1: "notify", 2: "install"}.get(int(self.update_mode_dropdown.get_selected()), "notify")

    def _save_update_mode(self) -> None:
        mode = self._selected_update_mode()
        labels = {"off": "Off", "notify": "Notify only", "install": "Install automatically"}
        self._run_action(
            "set-auto-update", arg=mode, backend_timeout=18, watchdog_ms=28000,
            operation_message=f"Setting automatic updates to {labels[mode]}…",
        )

    def _confirm_update_now(self) -> None:
        self.window._confirm(
            "Update Mac ZorinMacBridge",
            "Download the latest official Apple Silicon release on the Mac, verify SHA-256 and code signature, preserve this Mac's stable privacy identity, install it without a local password prompt, refresh P0 helpers, and restart the desktop service when a GUI session exists?",
            "Update now",
            lambda: self._run_action(
                "update-now", backend_timeout=720, watchdog_ms=780000,
                operation_message="Mac is downloading, verifying and installing the ZorinMacBridge update…",
            ),
        )

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
            label="This is a one-time setup. Use the SAME password you use to log in/unlock this Mac. This is NOT the ZorinMacBridge session password. The password is used only during this pairing and is not saved. P0 installs a restricted admin key; optionally P3 also installs a separate normal-user Terminal key.",
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
            ("Selected Mac", host_entry), ("SSH port", port_entry), ("macOS short username", user_entry), ("macOS account password (login/unlock)", password_entry)
        ]):
            grid.attach(Gtk.Label(label=label, xalign=0), 0, row, 1, 1)
            grid.attach(widget, 1, row, 1, 1)

        shell_checkbox = Gtk.CheckButton(label="Enable Terminal / Project Actions (normal macOS user SSH key)")
        shell_checkbox.set_active(bool(existing.get("shell_enabled", True)))
        shell_checkbox.set_tooltip_text("Creates a separate user-level Ed25519 key. It has no ZorinMacBridge sudoers privileges and is not the restricted P0 admin key.")
        box.append(shell_checkbox)

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

        state: dict[str, object] = {
            "scan": None, "busy": False, "job_serial": 0, "watchdog": 0, "closed": False,
        }

        def cancel_job_watchdog() -> None:
            source = int(state.get("watchdog") or 0)
            if source:
                try:
                    GLib.source_remove(source)
                except Exception:
                    pass
                state["watchdog"] = 0

        def start_job(kind: str, *, timeout_ms: int, timeout_text: str) -> int:
            cancel_job_watchdog()
            state["job_serial"] = int(state.get("job_serial") or 0) + 1
            token = int(state["job_serial"])
            state["busy"] = True
            scan_btn.set_sensitive(False)
            pair_btn.set_sensitive(False)

            def expired():
                if state.get("closed") or token != int(state.get("job_serial") or 0):
                    return GLib.SOURCE_REMOVE
                state["watchdog"] = 0
                state["job_serial"] = token + 1  # ignore a late worker result
                state["busy"] = False
                password_entry.set_text("")
                scan_btn.set_sensitive(True)
                if kind == "scan":
                    state["scan"] = None
                    verified.set_active(False)
                    verified.set_sensitive(False)
                fingerprint.set_text(timeout_text)
                self.window._log("ERROR", "Pairing timeout: " + timeout_text.replace("\n", " "))
                set_pair_sensitive()
                return GLib.SOURCE_REMOVE

            state["watchdog"] = GLib.timeout_add(timeout_ms, expired)
            return token

        def finish_job(token: int) -> bool:
            if state.get("closed") or token != int(state.get("job_serial") or 0):
                return False
            cancel_job_watchdog()
            state["busy"] = False
            scan_btn.set_sensitive(True)
            return True

        def close_requested(*_args):
            state["closed"] = True
            state["job_serial"] = int(state.get("job_serial") or 0) + 1
            cancel_job_watchdog()
            password_entry.set_text("")
            return False

        dialog.connect("close-request", close_requested)

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
            token = start_job(
                "scan",
                timeout_ms=12000,
                timeout_text=(
                    "No SSH fingerprint response within 12 seconds.\n"
                    "The pairing window is still usable; check that the Mac is awake and Remote Login is enabled, then retry."
                ),
            )
            fingerprint.set_text("Reading SSH host key from the Mac…")
            # GTK widgets are not thread-safe. Capture every widget value on the
            # GTK thread before starting the worker.
            scan_host = host_entry.get_text().strip()

            def worker():
                try:
                    result = scan_host_keys(scan_host, ssh_port)
                    GLib.idle_add(scan_done, token, result, None)
                except Exception as exc:
                    GLib.idle_add(scan_done, token, None, str(exc))

            threading.Thread(target=worker, daemon=True).start()

        def scan_done(job_token: int, result, error):
            if not finish_job(job_token):
                return False
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
                self.window._dialog("Pair Mac", "Enter the macOS short username and the account password you use to log in/unlock this Mac. Do not enter the ZorinMacBridge session password here.")
                return
            token = start_job(
                "pair",
                timeout_ms=34000,
                timeout_text=(
                    "Pairing did not finish within 34 seconds.\n"
                    "The UI has been released and the password field cleared. Check Remote Login/P0 on the Mac and retry."
                ),
            )
            fingerprint.set_text("Installing restricted admin key, testing P0, and configuring the optional user Terminal key…")
            pair_host = host_entry.get_text().strip()
            enable_shell = bool(shell_checkbox.get_active())

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
                    if enable_shell:
                        profile = install_shell_access(profile, password)
                    elif shell_ready(profile):
                        revoke_shell_access(profile)
                        profile = get_profile(sid, pair_host) or profile
                    GLib.idle_add(pair_done, token, profile, None)
                except Exception as exc:
                    GLib.idle_add(pair_done, token, None, str(exc))

            threading.Thread(target=worker, daemon=True).start()

        def pair_done(job_token: int, profile, error):
            if not finish_job(job_token):
                return False
            password_entry.set_text("")
            if error:
                fingerprint.set_text("Pairing failed:\n" + error)
                set_pair_sensitive()
                return False
            dialog.close()
            self.window._log("INFO", f"System administration paired with {profile.get('server_name') or profile.get('host')}; Terminal/Projects={'enabled' if profile.get('shell_enabled') else 'disabled'}.")
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
            "Remove this Zorin machine's restricted administration key and P3 user Terminal key from the Mac? Desktop trust/password settings are not changed.",
            "Revoke",
            self._unpair,
        )

    def _unpair(self) -> None:
        sid, host, _ = self.current()
        token = self._begin_operation(
            "Revoking administration key…",
            timeout_ms=23000,
            timeout_message="The Mac did not answer the revoke request within 23 seconds. The GUI has been released; the local pairing profile was not silently removed.",
        )

        def worker():
            try:
                profile = get_shell_profile(sid, host)
                if profile and shell_ready(profile):
                    # Do not leave a normal-user shell key behind when the admin
                    # device is revoked. If removal fails, keep the local profile
                    # so the user can retry instead of silently orphaning access.
                    revoke_shell_access(profile)
                revoke_current_device(server_id=sid, host=host)
                GLib.idle_add(done, token, None)
            except Exception as exc:
                GLib.idle_add(done, token, str(exc))

        def done(op_token: int, error):
            if not self._finish_operation(op_token):
                return False
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
