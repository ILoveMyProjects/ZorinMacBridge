#!/usr/bin/env python3
from __future__ import annotations

import threading

from gi.repository import GLib, Gtk

from zmb_admin_backend import AdminError, get_profile, run_action


from zmb_xcode_data import parse_xcode_overview

class XcodeController:
    def __init__(self, window):
        self.window = window
        self.busy = False
        self._serial = 0
        self._watchdog = 0
        self._controls: list[Gtk.Widget] = []
        self.page = self._build_page()
        if hasattr(window, "machine_dropdown"):
            window.machine_dropdown.connect("notify::selected", self._machine_changed)
        if hasattr(window, "stack"):
            window.stack.connect("notify::visible-child-name", self._tab_changed)
        GLib.idle_add(self.selection_changed)

    def current(self) -> tuple[str, str, str]:
        host = str(self.window.ip_var.get() or "").strip()
        sid = str(getattr(self.window, "selected_server_id", "") or "").strip()
        name = str(getattr(self.window, "selected_server_name", "") or host).strip()
        return sid, host, name

    def _profile(self):
        sid, host, _ = self.current()
        return get_profile(sid, host) if host else None

    def _build_page(self) -> Gtk.Widget:
        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        outer.set_margin_start(4); outer.set_margin_end(4); outer.set_margin_top(4); outer.set_margin_bottom(4)

        card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        card.add_css_class("zmb-card")
        outer.append(card)
        top = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        card.append(top)
        self.icon = Gtk.Image.new_from_icon_name("applications-engineering-symbolic")
        top.append(self.icon)
        labels = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        labels.set_hexpand(True); top.append(labels)
        title = Gtk.Label(label="Xcode and Simulator", xalign=0); title.add_css_class("title-3"); labels.append(title)
        self.summary = Gtk.Label(label="Select a paired Mac.", xalign=0, wrap=True); self.summary.add_css_class("zmb-muted"); labels.append(self.summary)
        self.spinner = Gtk.Spinner(); top.append(self.spinner)
        refresh = Gtk.Button(icon_name="view-refresh-symbolic", tooltip_text="Refresh Xcode and Simulator state")
        refresh.connect("clicked", lambda *_: self.refresh()); top.append(refresh); self._controls.append(refresh)
        open_xcode = Gtk.Button(label="Open Xcode"); open_xcode.connect("clicked", lambda *_: self._run("xcode-open", "Opening Xcode…")); top.append(open_xcode); self._controls.append(open_xcode)
        open_sim = Gtk.Button(label="Open Simulator"); open_sim.connect("clicked", lambda *_: self._run("sim-open", "Opening Simulator…")); top.append(open_sim); self._controls.append(open_sim)

        self.info = Gtk.Grid(column_spacing=18, row_spacing=5)
        card.append(self.info)

        sim_card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        sim_card.add_css_class("zmb-card"); outer.append(sim_card)
        st = Gtk.Label(label="Available iOS Simulators", xalign=0); st.add_css_class("title-4"); sim_card.append(st)
        note = Gtk.Label(label="Boot/shutdown uses xcrun simctl in the logged-in macOS user session. No arbitrary shell command is exposed.", xalign=0, wrap=True); note.add_css_class("zmb-muted"); sim_card.append(note)
        self.sim_list = Gtk.ListBox()
        self.sim_list.set_selection_mode(Gtk.SelectionMode.NONE)
        self.sim_list.add_css_class("boxed-list")
        sim_card.append(self.sim_list)

        scroller = Gtk.ScrolledWindow(); scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC); scroller.set_hexpand(True); scroller.set_vexpand(True); scroller.set_child(outer)
        return scroller

    def _machine_changed(self, *_args):
        self._cancel_watchdog(); self._serial += 1; self.busy = False; self.spinner.stop(); GLib.idle_add(self.selection_changed)

    def _tab_changed(self, *_args):
        if getattr(self.window.stack, "get_visible_child_name", lambda: "")() == "xcode" and self._profile() and not self.busy:
            GLib.idle_add(self.refresh)

    def selection_changed(self):
        sid, host, name = self.current()
        if not host or not get_profile(sid, host):
            self.summary.set_text("Pair this Mac in System before using Xcode controls.")
            self._set_controls(False)
            self._clear_simulators(); self._clear_info()
            return False
        self.summary.set_text(f"{name or host} is paired. Open this tab or press Refresh to read developer tools.")
        self._set_controls(True)
        if getattr(self.window.stack, "get_visible_child_name", lambda: "")() == "xcode":
            self.refresh()
        return False

    def _set_controls(self, enabled: bool):
        for w in self._controls:
            w.set_sensitive(enabled and not self.busy)

    def _set_busy(self, busy: bool, message: str | None = None):
        self.busy = busy
        self.spinner.start() if busy else self.spinner.stop()
        if message: self.summary.set_text(message)
        self._set_controls(self._profile() is not None)

    def _cancel_watchdog(self):
        if self._watchdog:
            try: GLib.source_remove(self._watchdog)
            except Exception: pass
            self._watchdog = 0

    def _begin(self, message: str, timeout_ms: int = 30000) -> int:
        self._cancel_watchdog(); self._serial += 1; token = self._serial; self._set_busy(True, message)
        def expired():
            if token != self._serial: return GLib.SOURCE_REMOVE
            self._watchdog = 0; self._serial += 1; self._set_busy(False)
            self.summary.set_text("Xcode operation timed out; GUI released. Check the Mac session and Xcode installation.")
            self.window._log("ERROR", "Xcode operation watchdog expired")
            return GLib.SOURCE_REMOVE
        self._watchdog = GLib.timeout_add(timeout_ms, expired)
        return token

    def _finish(self, token: int) -> bool:
        if token != self._serial: return False
        self._cancel_watchdog(); self._set_busy(False); return True

    def _clear_info(self):
        child = self.info.get_first_child()
        while child is not None:
            nxt = child.get_next_sibling(); self.info.remove(child); child = nxt

    def _clear_simulators(self):
        child = self.sim_list.get_first_child()
        while child is not None:
            nxt = child.get_next_sibling(); self.sim_list.remove(child); child = nxt

    def _render(self, meta: dict[str, str], devices: list[dict[str, str]]):
        self._clear_info(); self._clear_simulators()
        rows = [
            ("Developer directory", meta.get("developer_dir", "?")),
            ("Xcode", meta.get("xcode_version", "?")),
            ("Build", meta.get("xcode_build", "?")),
            ("simctl", "Ready" if meta.get("simctl_ok") == "1" else "Unavailable"),
        ]
        for i, (label, value) in enumerate(rows):
            l = Gtk.Label(label=label, xalign=0); l.add_css_class("zmb-muted")
            r = Gtk.Label(label=value, xalign=0, selectable=True, wrap=True)
            self.info.attach(l, 0, i, 1, 1); self.info.attach(r, 1, i, 1, 1)
        booted = sum(1 for d in devices if d["state"] == "Booted")
        self.summary.set_text(f"{meta.get('xcode_version', 'Xcode unknown')} · {len(devices)} simulator(s) · {booted} booted")
        if not devices:
            row = Gtk.ListBoxRow(); row.set_child(Gtk.Label(label=meta.get("simctl_error") or "No available simulators returned by simctl.", xalign=0, wrap=True)); self.sim_list.append(row); return
        for dev in devices:
            row = Gtk.ListBoxRow(); box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10, margin_top=7, margin_bottom=7, margin_start=8, margin_end=8); row.set_child(box)
            text = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2); text.set_hexpand(True); box.append(text)
            name = Gtk.Label(label=dev["name"], xalign=0); name.add_css_class("heading"); text.append(name)
            detail = Gtk.Label(label=f"{dev['runtime']} · {dev['state']} · {dev['udid']}", xalign=0, selectable=True); detail.add_css_class("zmb-muted"); text.append(detail)
            action = "sim-shutdown" if dev["state"] == "Booted" else "sim-boot"
            label = "Shutdown" if dev["state"] == "Booted" else "Boot"
            btn = Gtk.Button(label=label); btn.connect("clicked", lambda _b, a=action, u=dev["udid"], m=f"{label} simulator…": self._run(a, m, arg=u, refresh_after=True)); box.append(btn)
            self.sim_list.append(row)

    def refresh(self):
        if self.busy: return
        sid, host, _ = self.current()
        if not host or not get_profile(sid, host): self.selection_changed(); return
        token = self._begin("Reading Xcode and Simulator state…", 35000)
        def worker():
            try:
                p = run_action(server_id=sid, host=host, action="xcode-overview", timeout=25)
                if p.returncode != 0: raise AdminError((p.stderr or p.stdout or "xcode-overview failed").strip())
                meta, devices = parse_xcode_overview(p.stdout or "")
                GLib.idle_add(done, token, meta, devices, None)
            except Exception as exc: GLib.idle_add(done, token, {}, [], str(exc))
        def done(tok, meta, devices, error):
            if not self._finish(tok): return False
            if error:
                self.summary.set_text(error); self.window._log("ERROR", "Xcode status: " + error); return False
            self._render(meta, devices); return False
        threading.Thread(target=worker, daemon=True).start()

    def _run(self, action: str, message: str, *, arg: str | None = None, refresh_after: bool = False):
        if self.busy: return
        sid, host, _ = self.current(); token = self._begin(message, 45000)
        def worker():
            try:
                p = run_action(server_id=sid, host=host, action=action, arg=arg, timeout=30)
                if p.returncode != 0: raise AdminError((p.stderr or p.stdout or f"{action} failed").strip())
                GLib.idle_add(done, token, None)
            except Exception as exc: GLib.idle_add(done, token, str(exc))
        def done(tok, error):
            if not self._finish(tok): return False
            if error:
                self.window._dialog("Xcode action failed", error, destructive=True); self.window._log("ERROR", f"Xcode action {action}: {error}"); return False
            self.window._log("INFO", f"Xcode action {action}: OK")
            if refresh_after: GLib.timeout_add(800, lambda: (self.refresh(), False)[1])
            return False
        threading.Thread(target=worker, daemon=True).start()
