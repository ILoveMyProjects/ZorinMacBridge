#!/usr/bin/env python3
from __future__ import annotations

import os
import signal
import threading
import time

from gi.repository import GLib, Gtk, Pango

from zmb_project_data import (
    ProjectError,
    build_project_command,
    delete_project,
    load_projects,
    normalize_project,
    upsert_project,
)
from zmb_shell_backend import get_shell_profile, shell_ready, start_shell_command, terminate_shell_process
from zmb_sync_backend import preview_project_sync, start_project_sync


class ProjectsController:
    OUTPUT_LIMIT = 2_000_000

    def __init__(self, window):
        self.window = window
        self.projects: list[dict] = []
        self.rows_by_id: dict[str, Gtk.ListBoxRow] = {}
        self.row_ids: dict[Gtk.ListBoxRow, str] = {}
        self.selected_id = ""
        self.proc = None
        self.busy = False
        self.busy_label = ""
        self.run_serial = 0
        self._run_started = 0.0
        self.page = self._build_page()
        if hasattr(window, "machine_dropdown"):
            window.machine_dropdown.connect("notify::selected", self._machine_changed)
        GLib.idle_add(self.refresh_projects)

    def current(self):
        host = str(self.window.ip_var.get() or "").strip()
        sid = str(getattr(self.window, "selected_server_id", "") or "").strip()
        name = str(getattr(self.window, "selected_server_name", "") or host).strip()
        return sid, host, name

    def _profile(self):
        sid, host, _ = self.current()
        return get_shell_profile(sid, host) if host else None

    def _selected(self):
        return next((item for item in self.projects if item.get("id") == self.selected_id), None)

    def _build_page(self):
        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        outer.set_margin_start(4); outer.set_margin_end(4); outer.set_margin_top(4); outer.set_margin_bottom(4)

        header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        outer.append(header)
        self.summary = Gtk.Label(label="Saved project actions run as the normal macOS user over the dedicated Terminal SSH key.", xalign=0, wrap=True)
        self.summary.set_hexpand(True); self.summary.add_css_class("zmb-muted")
        header.append(self.summary)
        add = Gtk.Button(label="Add project")
        add.connect("clicked", lambda *_: self._open_editor(None))
        header.append(add)
        self.edit_btn = Gtk.Button(label="Edit")
        self.edit_btn.connect("clicked", lambda *_: self._open_editor(self._selected()))
        header.append(self.edit_btn)
        self.delete_btn = Gtk.Button(label="Delete")
        self.delete_btn.connect("clicked", self._delete_selected)
        header.append(self.delete_btn)

        paned = Gtk.Paned(orientation=Gtk.Orientation.HORIZONTAL, position=370)
        paned.set_hexpand(True); paned.set_vexpand(True)
        outer.append(paned)

        left = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        left.set_size_request(300, -1)
        self.listbox = Gtk.ListBox(selection_mode=Gtk.SelectionMode.SINGLE)
        self.listbox.add_css_class("boxed-list")
        self.listbox.connect("row-selected", self._row_selected)
        left_scroll = Gtk.ScrolledWindow(vexpand=True)
        left_scroll.set_child(self.listbox)
        left.append(left_scroll)
        paned.set_start_child(left)

        right = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        right.set_margin_start(8)
        paned.set_end_child(right)
        self.detail = Gtk.Label(label="Select a project.", xalign=0, wrap=True, selectable=True)
        right.append(self.detail)

        sync_actions = Gtk.FlowBox(selection_mode=Gtk.SelectionMode.NONE, column_spacing=6, row_spacing=6)
        right.append(sync_actions)
        self.preview_sync_btn = Gtk.Button(label="Preview sync")
        self.preview_sync_btn.connect("clicked", lambda *_: self._preview_sync(False))
        sync_actions.insert(self.preview_sync_btn, -1)
        self.sync_btn = Gtk.Button(label="Sync Zorin → Mac")
        self.sync_btn.add_css_class("suggested-action")
        self.sync_btn.connect("clicked", lambda *_: self._preview_sync(True))
        sync_actions.insert(self.sync_btn, -1)

        actions = Gtk.FlowBox(selection_mode=Gtk.SelectionMode.NONE, column_spacing=6, row_spacing=6)
        right.append(actions)
        self.action_buttons = []
        for label, action in [
            ("Git status", "git-status"), ("Git pull --ff-only", "git-pull"),
            ("Inspect Xcode", "inspect"), ("Open in Xcode", "open-xcode"),
            ("Build", "build"), ("Test", "test"), ("Clean", "clean"),
        ]:
            btn = Gtk.Button(label=label)
            btn.connect("clicked", lambda _b, a=action: self._run_action(a))
            actions.insert(btn, -1)
            self.action_buttons.append(btn)
        self.cancel_btn = Gtk.Button(label="Cancel running action")
        self.cancel_btn.add_css_class("destructive-action")
        self.cancel_btn.connect("clicked", lambda *_: self.cancel())
        self.cancel_btn.set_sensitive(False)
        right.append(self.cancel_btn)

        self.progress = Gtk.ProgressBar(show_text=True)
        self.progress.set_text("Idle")
        right.append(self.progress)
        scroll = Gtk.ScrolledWindow(vexpand=True)
        self.output = Gtk.TextView(editable=False, monospace=True, wrap_mode=Gtk.WrapMode.CHAR)
        scroll.set_child(self.output)
        right.append(scroll)
        self._update_controls()
        return outer

    def _machine_changed(self, *_args):
        self.cancel()
        GLib.idle_add(self.refresh_projects)

    def refresh_projects(self):
        sid, _host, _name = self.current()
        self.projects = [item for item in load_projects() if item.get("server_id") == sid] if sid else []
        self.rows_by_id.clear()
        self.row_ids.clear()
        child = self.listbox.get_first_child()
        while child is not None:
            nxt = child.get_next_sibling(); self.listbox.remove(child); child = nxt
        for project in self.projects:
            row = Gtk.ListBoxRow()
            box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2, margin_top=7, margin_bottom=7, margin_start=8, margin_end=8)
            row.set_child(box)
            name = Gtk.Label(label=project["name"], xalign=0); name.add_css_class("heading"); box.append(name)
            desc = Gtk.Label(label=project["remote_path"], xalign=0, ellipsize=Pango.EllipsizeMode.MIDDLE); desc.add_css_class("zmb-muted"); box.append(desc)
            self.row_ids[row] = project["id"]
            self.rows_by_id[project["id"]] = row
            self.listbox.append(row)
        if self.selected_id not in self.rows_by_id:
            self.selected_id = self.projects[0]["id"] if self.projects else ""
        if self.selected_id and self.selected_id in self.rows_by_id:
            self.listbox.select_row(self.rows_by_id[self.selected_id])
        else:
            self.detail.set_text("No saved projects for this Mac. Add one to create reusable Git/Xcode actions.")
        self._update_controls()
        return False

    def _row_selected(self, _box, row):
        self.selected_id = str(self.row_ids.get(row, "") or "") if row else ""
        project = self._selected()
        if project:
            lines = [
                f"{project['name']}",
                f"Mac path: {project['remote_path']}",
                f"Zorin path: {project.get('local_path') or '(sync not configured)'}",
                f"Sync excludes: {', '.join(project.get('sync_excludes') or []) or '(defaults only)'}",
                f"Workspace/project: {project.get('container') or '(auto from directory)'}",
                f"Scheme: {project.get('scheme') or '(not set)'}",
                f"Configuration: {project.get('configuration') or 'Debug'}",
                f"Destination: {project.get('destination') or '(Xcode default)'}",
            ]
            self.detail.set_text("\n".join(lines))
        self._update_controls()

    def _update_controls(self):
        profile = self._profile()
        ready = shell_ready(profile)
        running = self.busy or (self.proc is not None and self.proc.poll() is None)
        project = self._selected()
        selected = project is not None
        sync_ready = bool(selected and project.get("local_path")) if project else False
        for btn in self.action_buttons:
            btn.set_sensitive(ready and selected and not running)
        self.preview_sync_btn.set_sensitive(ready and sync_ready and not running)
        self.sync_btn.set_sensitive(ready and sync_ready and not running)
        self.edit_btn.set_sensitive(selected and not running)
        self.delete_btn.set_sensitive(selected and not running)
        self.cancel_btn.set_sensitive(self.proc is not None and self.proc.poll() is None)
        if profile is None:
            self.summary.set_text("Pair the selected Mac in System before using Projects.")
        elif not ready:
            self.summary.set_text("Re-pair this Mac in System with 'Enable Terminal / Project Actions' selected.")
        elif running:
            self.summary.set_text(self.busy_label or "Project operation is running. Output is streamed below when available.")
        else:
            self.summary.set_text("Projects run as the normal macOS user over pinned SSH. Project Sync is one-way Zorin → Mac, never deletes remote files, and protects newer Mac files.")

    def _open_editor(self, project):
        sid, host, name = self.current()
        profile = self._profile()
        if not sid or not profile:
            self.window._dialog("Projects", "Pair a Mac in System first.")
            return
        data = dict(project or {})
        dialog = Gtk.Window(title="Edit project" if project else "Add project", transient_for=self.window, modal=True, default_width=720, default_height=500)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10, margin_top=18, margin_bottom=18, margin_start=18, margin_end=18)
        dialog.set_child(box)
        note = Gtk.Label(label=f"Mac: {name or host}. Local path is on Zorin; remote/project paths are on that Mac.", xalign=0, wrap=True)
        note.add_css_class("zmb-muted"); box.append(note)
        grid = Gtk.Grid(column_spacing=10, row_spacing=8); box.append(grid)
        fields = [
            ("Name", "name", data.get("name", "")),
            ("Local Zorin project directory", "local_path", data.get("local_path", "")),
            ("Remote macOS project directory", "remote_path", data.get("remote_path", "")),
            ("Workspace / project (relative)", "container", data.get("container", "")),
            ("Scheme", "scheme", data.get("scheme", "")),
            ("Configuration", "configuration", data.get("configuration", "Debug")),
            ("Destination (optional)", "destination", data.get("destination", "")),
            ("Extra sync excludes (comma separated)", "sync_excludes", ", ".join(data.get("sync_excludes") or [])),
        ]
        entries = {}
        for row, (label, key, value) in enumerate(fields):
            grid.attach(Gtk.Label(label=label, xalign=0), 0, row, 1, 1)
            entry = Gtk.Entry(text=str(value), hexpand=True)
            entries[key] = entry; grid.attach(entry, 1, row, 1, 1)
        hint = Gtk.Label(
            label="Project Sync is Zorin → Mac only. It always excludes .git, DerivedData, .build, build, xcuserdata, *.xcuserstate and .DS_Store; it never uses --delete and skips files that are newer on the Mac.",
            xalign=0, wrap=True,
        )
        hint.add_css_class("zmb-muted"); box.append(hint)
        buttons = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8); buttons.set_halign(Gtk.Align.END); box.append(buttons)
        cancel = Gtk.Button(label="Cancel"); cancel.connect("clicked", lambda *_: dialog.close()); buttons.append(cancel)
        save = Gtk.Button(label="Save"); save.add_css_class("suggested-action"); buttons.append(save)

        def do_save(*_args):
            try:
                saved = upsert_project({
                    "id": data.get("id") or "",
                    "name": entries["name"].get_text(),
                    "server_id": sid,
                    "server_name": name,
                    "local_path": entries["local_path"].get_text(),
                    "remote_path": entries["remote_path"].get_text(),
                    "container": entries["container"].get_text(),
                    "scheme": entries["scheme"].get_text(),
                    "configuration": entries["configuration"].get_text(),
                    "destination": entries["destination"].get_text(),
                    "sync_excludes": entries["sync_excludes"].get_text(),
                })
            except Exception as exc:
                self.window._dialog("Project", str(exc), destructive=True); return
            self.selected_id = saved["id"]
            dialog.close(); self.refresh_projects()

        save.connect("clicked", do_save)
        dialog.present()

    def _delete_selected(self, *_args):
        project = self._selected()
        if not project:
            return
        self.window._confirm(
            "Delete project action profile",
            f"Delete the local ZorinMacBridge project profile '{project['name']}'? No files on the Mac are deleted.",
            "Delete",
            lambda: self._delete_confirmed(project["id"]),
        )

    def _delete_confirmed(self, project_id):
        delete_project(project_id)
        self.selected_id = ""
        self.refresh_projects()

    def _append_output(self, text: str):
        if not text:
            return False
        buf = self.output.get_buffer()
        end = buf.get_end_iter(); buf.insert(end, text)
        count = buf.get_char_count()
        if count > self.OUTPUT_LIMIT:
            cut = buf.get_iter_at_offset(count - self.OUTPUT_LIMIT)
            buf.delete(buf.get_start_iter(), cut)
        mark = buf.create_mark(None, buf.get_end_iter(), False)
        self.output.scroll_mark_onscreen(mark)
        buf.delete_mark(mark)
        return False

    def _run_action(self, action: str):
        if self.busy or (self.proc is not None and self.proc.poll() is None):
            return
        profile = self._profile(); project = self._selected()
        if not shell_ready(profile) or not project:
            self._update_controls(); return
        try:
            command = build_project_command(project, action)
            proc = start_shell_command(profile, command)
        except Exception as exc:
            self.window._dialog("Project action", str(exc), destructive=True); return
        self.proc = proc
        self.busy = True; self.busy_label = f"Project action {action} is running. Output is streamed below; Cancel remains available."
        self.run_serial += 1; token = self.run_serial; self._run_started = time.monotonic()
        self.output.get_buffer().set_text(f"$ {command}\n\n")
        self.progress.set_fraction(0.0); self.progress.pulse(); self.progress.set_text(f"Running {action}…")
        self._update_controls()

        def reader():
            try:
                assert proc.stdout is not None
                for line in proc.stdout:
                    if token != self.run_serial:
                        break
                    GLib.idle_add(self._append_output, line)
                code = proc.wait()
                GLib.idle_add(done, token, code, None)
            except Exception as exc:
                GLib.idle_add(done, token, -1, str(exc))

        def done(tok, code, error):
            if tok != self.run_serial:
                return False
            elapsed = time.monotonic() - self._run_started
            self.proc = None
            self.busy = False; self.busy_label = ""
            if error:
                self._append_output(f"\n[error] {error}\n")
                self.progress.set_text("Failed")
                self.window._log("ERROR", f"Project action {action}: {error}")
            else:
                self._append_output(f"\n[exit {code}] elapsed {elapsed:.1f}s\n")
                self.progress.set_fraction(1.0 if code == 0 else 0.0)
                self.progress.set_text("Completed" if code == 0 else f"Failed ({code})")
                self.window._log("INFO" if code == 0 else "ERROR", f"Project action {action} exited {code}")
            self._update_controls()
            return False

        threading.Thread(target=reader, daemon=True).start()

    def _format_sync_preview(self, preview: dict) -> str:
        lines = [
            "Project Sync preview (Zorin → Mac)",
            "Safety: no remote deletes; newer Mac files are protected by rsync --update.",
            "",
            f"Outgoing changes: {len(preview.get('outgoing') or [])}",
            f"Protected remote changes: {len(preview.get('protected') or [])}",
            f"Remote-only entries (left untouched): {len(preview.get('remote_only') or [])}",
            "",
        ]
        for title, key, prefix in [
            ("OUTGOING", "outgoing", "+ "),
            ("PROTECTED ON MAC", "protected", "! "),
            ("REMOTE-ONLY / UNTOUCHED", "remote_only", "= "),
        ]:
            items = preview.get(key) or []
            if not items:
                continue
            lines.append(f"[{title}]")
            for item in items[:300]:
                lines.append(prefix + item.get("path", ""))
            if len(items) > 300:
                lines.append(f"… {len(items) - 300} more")
            lines.append("")
        if preview.get("truncated"):
            lines.append("[preview truncated after safety limit]")
        return "\n".join(lines).rstrip() + "\n"

    def _preview_sync(self, then_sync: bool):
        if self.busy or (self.proc is not None and self.proc.poll() is None):
            return
        profile = self._profile(); project = self._selected()
        if not shell_ready(profile) or not project or not project.get("local_path"):
            self.window._dialog("Project Sync", "Configure a local Zorin project directory and pair Terminal / Project Actions first.", destructive=True)
            return
        self.busy = True
        self.busy_label = "Checking Project Sync changes over pinned SSH…"
        self.progress.set_fraction(0.0); self.progress.pulse(); self.progress.set_text("Previewing sync…")
        self.output.get_buffer().set_text("Checking local and remote project trees…\n")
        self._update_controls()
        token = self.run_serial = self.run_serial + 1

        def worker():
            try:
                result = preview_project_sync(profile, project, timeout=45)
                GLib.idle_add(done, token, result, None)
            except Exception as exc:
                GLib.idle_add(done, token, None, str(exc))

        def done(tok, result, error):
            if tok != self.run_serial:
                return False
            self.busy = False; self.busy_label = ""
            if error:
                self.progress.set_fraction(0.0); self.progress.set_text("Preview failed")
                self.output.get_buffer().set_text(f"[sync preview failed]\n{error}\n")
                self.window._log("ERROR", f"Project Sync preview: {error}")
                self._update_controls(); return False
            text = self._format_sync_preview(result)
            self.output.get_buffer().set_text(text)
            self.progress.set_fraction(1.0); self.progress.set_text("Preview ready")
            self._update_controls()
            if then_sync:
                protected = result.get("protected") or []
                if protected:
                    self.window._confirm(
                        "Project Sync protected Mac changes",
                        f"{len(protected)} path(s) are newer/different on the Mac. Sync will SKIP those paths, will NOT delete remote-only files, and will upload the remaining safe changes. Continue?",
                        "Sync safe changes",
                        lambda: self._start_sync(profile, project, [item.get("path", "") for item in protected]),
                    )
                else:
                    self._start_sync(profile, project, [])
            return False

        threading.Thread(target=worker, daemon=True).start()

    def _start_sync(self, profile, project, protected_paths):
        if self.busy or (self.proc is not None and self.proc.poll() is None):
            return
        try:
            proc = start_project_sync(profile, project, protected_paths=protected_paths)
        except Exception as exc:
            self.window._dialog("Project Sync", str(exc), destructive=True); return
        self.proc = proc
        self.busy = True; self.busy_label = "Syncing Zorin → Mac. Remote-only files are untouched and newer Mac files are skipped."
        self.run_serial += 1; token = self.run_serial; self._run_started = time.monotonic()
        self._append_output("\n[sync started]\n")
        self.progress.set_fraction(0.0); self.progress.pulse(); self.progress.set_text("Syncing to Mac…")
        self._update_controls()

        def reader():
            try:
                assert proc.stdout is not None
                for line in proc.stdout:
                    if token != self.run_serial:
                        break
                    GLib.idle_add(self._append_output, line)
                code = proc.wait()
                GLib.idle_add(done, token, code, None)
            except Exception as exc:
                GLib.idle_add(done, token, -1, str(exc))

        def done(tok, code, error):
            if tok != self.run_serial:
                return False
            elapsed = time.monotonic() - self._run_started
            self.proc = None; self.busy = False; self.busy_label = ""
            if error:
                self._append_output(f"\n[sync error] {error}\n")
                self.progress.set_fraction(0.0); self.progress.set_text("Sync failed")
                self.window._log("ERROR", f"Project Sync: {error}")
            else:
                self._append_output(f"\n[sync exit {code}] elapsed {elapsed:.1f}s\n")
                self.progress.set_fraction(1.0 if code == 0 else 0.0)
                self.progress.set_text("Sync completed" if code == 0 else f"Sync failed ({code})")
                self.window._log("INFO" if code == 0 else "ERROR", f"Project Sync exited {code}")
            self._update_controls()
            return False

        threading.Thread(target=reader, daemon=True).start()

    def cancel(self):
        if self.proc is None:
            return
        self.run_serial += 1
        proc = self.proc; self.proc = None
        self.busy = False; self.busy_label = ""
        terminate_shell_process(proc)
        self._append_output("\n[cancelled by user]\n")
        self.progress.set_fraction(0.0); self.progress.set_text("Cancelled")
        self._update_controls()
