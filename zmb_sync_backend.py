#!/usr/bin/env python3
from __future__ import annotations

import os
from pathlib import Path
import shlex
import shutil
import signal
import subprocess
from typing import Iterable

from zmb_admin_backend import AdminError
from zmb_project_data import normalize_project
from zmb_shell_backend import shell_ssh_transport_argv


class SyncError(RuntimeError):
    pass


DEFAULT_EXCLUDES = (
    ".git/",
    "DerivedData/",
    ".build/",
    "build/",
    "xcuserdata/",
    "*.xcuserstate",
    ".DS_Store",
)

MAX_PREVIEW_ITEMS = 5000


def ensure_sync_tools() -> None:
    missing = [name for name in ("rsync", "ssh") if shutil.which(name) is None]
    if missing:
        raise SyncError("Missing sync tools: " + ", ".join(missing) + ". Install rsync and openssh-client on Zorin.")


def normalize_local_path(value: str) -> str:
    value = str(value or "").strip()
    if not value:
        raise SyncError("Local project directory is required for Project Sync")
    if "\x00" in value or "\n" in value or "\r" in value:
        raise SyncError("Local project directory contains an invalid control character")
    path = Path(value).expanduser()
    if not path.is_absolute():
        raise SyncError("Local project directory must be an absolute Zorin path")
    return str(path)


def normalize_excludes(value: str | Iterable[str] | None) -> list[str]:
    if value is None:
        raw: list[str] = []
    elif isinstance(value, str):
        raw = value.replace(",", "\n").splitlines()
    else:
        raw = [str(item) for item in value]
    result: list[str] = []
    seen: set[str] = set()
    for item in raw:
        item = item.strip()
        if not item:
            continue
        if "\x00" in item or "\n" in item or "\r" in item:
            raise SyncError("Sync exclude pattern contains an invalid control character")
        if len(item) > 240:
            raise SyncError("Sync exclude pattern is too long")
        if item not in seen:
            seen.add(item)
            result.append(item)
    return result


def effective_excludes(project: dict) -> list[str]:
    user = normalize_excludes(project.get("sync_excludes") or [])
    result = list(DEFAULT_EXCLUDES)
    for item in user:
        if item not in result:
            result.append(item)
    return result


def _checked_project(project: dict, *, require_local_exists: bool = True) -> dict:
    project = normalize_project(project)
    local = normalize_local_path(project.get("local_path") or "")
    path = Path(local)
    if require_local_exists:
        if not path.exists():
            raise SyncError(f"Local project directory does not exist: {local}")
        if not path.is_dir():
            raise SyncError(f"Local project path is not a directory: {local}")
    project["local_path"] = local
    project["sync_excludes"] = normalize_excludes(project.get("sync_excludes") or [])
    return project


def _remote_spec(profile: dict, remote_path: str) -> str:
    # rsync passes the path to a remote shell. Keep it single-quoted so spaces and
    # shell metacharacters in legitimate project names remain data, not syntax.
    path = remote_path.rstrip("/") + "/"
    return f"{profile['user']}@{profile['host']}:{shlex.quote(path)}"


def build_rsync_argv(profile: dict, project: dict, *, direction: str = "push", dry_run: bool = False, protected_paths: Iterable[str] | None = None) -> list[str]:
    project = _checked_project(project)
    if direction not in {"push", "pull-preview"}:
        raise SyncError(f"Unsupported sync direction: {direction}")
    ssh_transport = shell_ssh_transport_argv(profile)
    local = project["local_path"].rstrip(os.sep) + os.sep
    remote = _remote_spec(profile, project["remote_path"])
    argv = [
        "rsync",
        "-rlpt",
        "--human-readable",
        "--itemize-changes",
        "--out-format=%i|%n",
        "--update",
        "--partial",
        "--timeout=30",
    ]
    if dry_run:
        argv.append("--dry-run")
    for pattern in effective_excludes(project):
        argv += ["--exclude", pattern]
    if direction == "push":
        for rel in protected_paths or []:
            rel = str(rel or "").strip().lstrip("/")
            if not rel or "\x00" in rel or "\n" in rel or "\r" in rel:
                continue
            # Anchor the exclusion at the sync root so a protected remote file is
            # not overwritten even in the equal-mtime/different-content edge case.
            argv += ["--exclude", "/" + rel]
    argv += ["-e", shlex.join(ssh_transport), "--"]
    if direction == "push":
        argv += [local, remote]
    else:
        argv += [remote, local]
    return argv


def _run_capture(argv: list[str], *, timeout: int) -> subprocess.CompletedProcess[str]:
    try:
        proc = subprocess.Popen(
            argv,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            start_new_session=True,
        )
    except FileNotFoundError as exc:
        raise SyncError(f"{argv[0]} was not found") from exc
    try:
        out, err = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except Exception:
            proc.kill()
        out, err = proc.communicate()
        detail = (err or out or "").strip()
        raise SyncError(f"Project Sync preview timed out after {timeout}s" + (f": {detail}" if detail else "")) from exc
    return subprocess.CompletedProcess(argv, proc.returncode, out, err)


def parse_itemized_output(text: str) -> list[dict[str, str]]:
    result: list[dict[str, str]] = []
    for raw in (text or "").splitlines():
        if "|" not in raw:
            continue
        code, path = raw.split("|", 1)
        code = code.strip()
        path = path.strip()
        if not code or not path or path in {"./", "."}:
            continue
        # rsync may emit informational lines in unusual locales. Itemized change
        # records are 11 chars and start with a transfer/update marker.
        if len(code) < 2 or code[0] not in {">", "<", "c", "h", ".", "*"}:
            continue
        result.append({"code": code, "path": path})
        if len(result) >= MAX_PREVIEW_ITEMS:
            break
    return result


def preview_project_sync(profile: dict, project: dict, *, timeout: int = 45) -> dict:
    ensure_sync_tools()
    project = _checked_project(project)

    push = _run_capture(build_rsync_argv(profile, project, direction="push", dry_run=True), timeout=timeout)
    if push.returncode != 0:
        detail = (push.stderr or push.stdout or f"rsync exited with {push.returncode}").strip()
        raise SyncError("Push preview failed: " + detail)

    reverse = _run_capture(build_rsync_argv(profile, project, direction="pull-preview", dry_run=True), timeout=timeout)
    if reverse.returncode != 0:
        detail = (reverse.stderr or reverse.stdout or f"rsync exited with {reverse.returncode}").strip()
        raise SyncError("Conflict preview failed: " + detail)

    outgoing = parse_itemized_output(push.stdout)
    incoming = parse_itemized_output(reverse.stdout)
    local_root = Path(project["local_path"])
    protected: list[dict[str, str]] = []
    remote_only: list[dict[str, str]] = []
    for item in incoming:
        rel = item["path"].rstrip("/")
        if not rel:
            continue
        if (local_root / rel).exists():
            protected.append(item)
        else:
            remote_only.append(item)

    return {
        "outgoing": outgoing,
        "protected": protected,
        "remote_only": remote_only,
        "truncated": len(outgoing) >= MAX_PREVIEW_ITEMS or len(incoming) >= MAX_PREVIEW_ITEMS,
        "excludes": effective_excludes(project),
    }


def start_project_sync(profile: dict, project: dict, *, protected_paths: Iterable[str] | None = None) -> subprocess.Popen[str]:
    """Start safe one-way Zorin -> Mac sync.

    No --delete is used. --update protects a newer destination file on the Mac.
    The caller should normally preview first and show protected conflicts.
    """
    ensure_sync_tools()
    project = _checked_project(project)
    argv = build_rsync_argv(profile, project, direction="push", dry_run=False, protected_paths=protected_paths)
    try:
        return subprocess.Popen(
            argv,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
            start_new_session=True,
        )
    except FileNotFoundError as exc:
        raise SyncError("rsync was not found; install rsync on Zorin") from exc
