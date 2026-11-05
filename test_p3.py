#!/usr/bin/env python3
from __future__ import annotations

import tempfile
from pathlib import Path

import zmb_project_data as projects
from zmb_project_data import ProjectError, build_project_command, normalize_project
from zmb_shell_backend import shell_authorized_key


def expect_error(fn, *args, **kwargs):
    try:
        fn(*args, **kwargs)
    except Exception:
        return
    raise AssertionError(f"expected {fn.__name__} to fail")


# Terminal keys are intentionally user-shell keys, but must not gain root helper
# privileges or SSH forwarding. PTY is allowed by not using OpenSSH 'restrict'.
line = shell_authorized_key(
    "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAITestKey material@example",
    "dev-12345678",
)
assert "ZorinMacBridge-Shell:dev-12345678" in line
assert "command=" not in line
assert "no-port-forwarding" in line
assert "no-agent-forwarding" in line
assert "no-X11-forwarding" in line
assert "restrict," not in line

base = normalize_project({
    "name": "Demo",
    "server_id": "server-uuid",
    "server_name": "MacBook",
    "remote_path": "/Users/dev/Projects/My App",
    "container": "App.xcworkspace",
    "scheme": "My App",
    "configuration": "Debug",
    "destination": "platform=iOS Simulator,name=iPhone 16 Pro",
})

cmd = build_project_command(base, "build")
assert "cd '/Users/dev/Projects/My App' &&" in cmd
assert "xcodebuild" in cmd
assert "-workspace App.xcworkspace" in cmd
assert "-scheme 'My App'" in cmd
assert "-destination 'platform=iOS Simulator,name=iPhone 16 Pro'" in cmd
assert cmd.endswith(" build")

pull = build_project_command(base, "git-pull")
assert "GIT_TERMINAL_PROMPT=0" in pull
assert "git pull --ff-only" in pull

# Shell metacharacters remain quoted instead of becoming syntax.
adversarial = normalize_project({
    **base,
    "id": "attack-test",
    "remote_path": "/Users/dev/Projects/a; touch PWNED",
    "scheme": "A; touch PWNED",
})
adv_cmd = build_project_command(adversarial, "build")
assert "cd '/Users/dev/Projects/a; touch PWNED' &&" in adv_cmd
assert "-scheme 'A; touch PWNED'" in adv_cmd

expect_error(normalize_project, {**base, "container": "../Escape.xcodeproj"})
expect_error(normalize_project, {**base, "remote_path": "relative/path"})
expect_error(build_project_command, base, "rm-everything")

with tempfile.TemporaryDirectory() as td:
    old_root, old_path = projects.CONFIG_ROOT, projects.PROJECTS_PATH
    try:
        projects.CONFIG_ROOT = Path(td)
        projects.PROJECTS_PATH = Path(td) / "projects.json"
        saved = projects.upsert_project({**base, "id": "p1"})
        assert saved["id"] == "p1"
        loaded = projects.load_projects()
        assert len(loaded) == 1 and loaded[0]["name"] == "Demo"
        projects.delete_project("p1")
        assert projects.load_projects() == []
    finally:
        projects.CONFIG_ROOT, projects.PROJECTS_PATH = old_root, old_path

root = Path(__file__).resolve().parent
client = (root / "linux_client_gtk.py").read_text(encoding="utf-8")
system = (root / "zmb_system_page.py").read_text(encoding="utf-8")
terminal = (root / "zmb_terminal_page.py").read_text(encoding="utf-8")
project_page = (root / "zmb_projects_page.py").read_text(encoding="utf-8")
release = (root / ".github/workflows/release.yml").read_text(encoding="utf-8")

assert "TerminalController" in client
assert "ProjectsController" in client
assert "self.stack.add_titled(self.terminal_page, 'terminal', 'Terminal')" in client
assert "self.stack.add_titled(self.projects_page, 'projects', 'Projects')" in client
assert "Enable Terminal / Project Actions" in system
assert "Vte.Terminal()" in terminal
assert "spawn_async" in terminal
assert "Cancel running action" in project_page
assert "git pull --ff-only" in (root / "zmb_project_data.py").read_text(encoding="utf-8")
assert "gir1.2-vte-3.91" in release
assert "zmb_shell_backend.py" in release and "zmb_projects_page.py" in release
assert "test_p3.py" in release
print("P3 terminal/projects tests: OK")
