#!/usr/bin/env python3
from __future__ import annotations

import tempfile
from pathlib import Path

import zmb_sync_backend as sync
from zmb_project_data import normalize_project


def expect_error(fn, *args, **kwargs):
    try:
        fn(*args, **kwargs)
    except Exception:
        return
    raise AssertionError(f"expected {fn.__name__} to fail")


with tempfile.TemporaryDirectory() as td:
    local = Path(td) / "My App; safe"
    local.mkdir()
    (local / "main.swift").write_text("print('hi')\n", encoding="utf-8")
    project = normalize_project({
        "name": "Demo",
        "server_id": "server-uuid",
        "server_name": "MacBook",
        "local_path": str(local),
        "remote_path": "/Users/dev/Projects/My App; safe",
        "container": "App.xcworkspace",
        "scheme": "Demo",
        "sync_excludes": ["node_modules/", "Secrets.local"],
    })
    assert project["local_path"] == str(local)
    assert project["sync_excludes"] == ["node_modules/", "Secrets.local"]

    old = sync.shell_ssh_transport_argv
    sync.shell_ssh_transport_argv = lambda profile: [
        "ssh", "-i", "/tmp/key with spaces", "-o", "StrictHostKeyChecking=yes",
    ]
    try:
        profile = {"user": "dev", "host": "192.168.1.10"}
        argv = sync.build_rsync_argv(profile, project, direction="push", dry_run=True)
        joined = "\n".join(argv)
        assert argv[0] == "rsync"
        assert "--dry-run" in argv
        assert "--update" in argv
        assert "--delete" not in argv
        assert ".git/" in argv
        assert "DerivedData/" in argv
        assert "node_modules/" in argv
        assert "Secrets.local" in argv
        assert "StrictHostKeyChecking=yes" in joined
        remote = argv[-1]
        assert remote.startswith("dev@192.168.1.10:")
        assert "'/Users/dev/Projects/My App; safe/'" in remote

        protected_argv = sync.build_rsync_argv(profile, project, direction="push", dry_run=False, protected_paths=["Sources/Conflict.swift"])
        assert "/Sources/Conflict.swift" in protected_argv
        assert "--delete" not in protected_argv

        reverse = sync.build_rsync_argv(profile, project, direction="pull-preview", dry_run=True)
        assert reverse[-2].startswith("dev@192.168.1.10:")
        assert reverse[-1].endswith("/")
    finally:
        sync.shell_ssh_transport_argv = old

items = sync.parse_itemized_output(">f.st......|Sources/App.swift\ncd+++++++++|NewDir/\nnoise\n")
assert [x["path"] for x in items] == ["Sources/App.swift", "NewDir/"]

assert sync.normalize_excludes("node_modules/, Secrets.local\n*.tmp") == ["node_modules/", "Secrets.local", "*.tmp"]
expect_error(sync.normalize_local_path, "relative/project")
expect_error(sync.normalize_excludes, ["bad\x00pattern"])


# Local rsync behavioral regression: equal-mtime divergent files must be treated
# as a conflict by two-way dry-run and the real push must be able to exclude it.
import os
import shutil
import subprocess
if shutil.which("rsync"):
    with tempfile.TemporaryDirectory() as td:
        left = Path(td) / "left"; right = Path(td) / "right"
        left.mkdir(); right.mkdir()
        (left / "Conflict.swift").write_text("LOCAL\n", encoding="utf-8")
        (right / "Conflict.swift").write_text("REMOTE-DIFFERENT-SIZE\n", encoding="utf-8")
        stamp = 1_700_000_000
        os.utime(left / "Conflict.swift", (stamp, stamp)); os.utime(right / "Conflict.swift", (stamp, stamp))
        fmt = "--out-format=%i|%n"
        push = subprocess.run(["rsync", "-rlpt", "--update", "--dry-run", "--itemize-changes", fmt, str(left) + "/", str(right) + "/"], text=True, stdout=subprocess.PIPE, check=True)
        rev = subprocess.run(["rsync", "-rlpt", "--update", "--dry-run", "--itemize-changes", fmt, str(right) + "/", str(left) + "/"], text=True, stdout=subprocess.PIPE, check=True)
        assert any(x["path"] == "Conflict.swift" for x in sync.parse_itemized_output(push.stdout))
        assert any(x["path"] == "Conflict.swift" for x in sync.parse_itemized_output(rev.stdout))
        subprocess.run(["rsync", "-rlpt", "--update", "--exclude", "/Conflict.swift", str(left) + "/", str(right) + "/"], check=True)
        assert (right / "Conflict.swift").read_text(encoding="utf-8") == "REMOTE-DIFFERENT-SIZE\n"

root = Path(__file__).resolve().parent
projects_page = (root / "zmb_projects_page.py").read_text(encoding="utf-8")
shell_backend = (root / "zmb_shell_backend.py").read_text(encoding="utf-8")
release = (root / ".github/workflows/release.yml").read_text(encoding="utf-8")
ci = (root / ".github/workflows/ci.yml").read_text(encoding="utf-8")

assert "Preview sync" in projects_page
assert "Sync Zorin → Mac" in projects_page
assert "preview_project_sync" in projects_page
assert "start_project_sync" in projects_page
assert "shell_ssh_transport_argv" in shell_backend
assert "zmb_sync_backend.py" in release
assert "test_p4.py" in release and "test_p4.py" in ci
assert "rsync" in release and "rsync" in ci
print("P4 Project Sync tests: OK")
