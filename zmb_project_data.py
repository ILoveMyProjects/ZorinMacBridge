#!/usr/bin/env python3
from __future__ import annotations

import json
import os
from pathlib import Path, PurePosixPath
import shlex
import uuid

CONFIG_ROOT = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "zorinmacbridge"
PROJECTS_PATH = CONFIG_ROOT / "projects.json"
PROJECT_ACTIONS = {"git-status", "git-pull", "inspect", "open-xcode", "build", "test", "clean"}


class ProjectError(ValueError):
    pass


def _clean_text(value: str, field: str, *, max_len: int = 512) -> str:
    value = str(value or "").strip()
    if "\x00" in value or "\n" in value or "\r" in value:
        raise ProjectError(f"{field} contains an invalid control character")
    if len(value) > max_len:
        raise ProjectError(f"{field} is too long")
    return value


def validate_remote_path(value: str) -> str:
    value = _clean_text(value, "Remote project path", max_len=1024)
    if not value.startswith("/"):
        raise ProjectError("Remote project path must be an absolute macOS path")
    path = PurePosixPath(value)
    if ".." in path.parts:
        raise ProjectError("Remote project path must not contain '..'")
    return str(path)




def validate_local_path(value: str) -> str:
    value = _clean_text(value, "Local project path", max_len=2048)
    if not value:
        return ""
    path = Path(value).expanduser()
    if not path.is_absolute():
        raise ProjectError("Local project path must be an absolute Zorin path")
    return str(path)


def validate_sync_excludes(value) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        items = value.replace(",", "\n").splitlines()
    elif isinstance(value, (list, tuple)):
        items = [str(item) for item in value]
    else:
        raise ProjectError("Sync excludes must be a list or newline-separated text")
    result: list[str] = []
    seen: set[str] = set()
    for item in items:
        item = _clean_text(item, "Sync exclude", max_len=240)
        if item and item not in seen:
            seen.add(item); result.append(item)
    return result

def validate_container(value: str) -> str:
    value = _clean_text(value, "Workspace / project", max_len=512)
    if not value:
        return ""
    path = PurePosixPath(value)
    if path.is_absolute() or ".." in path.parts:
        raise ProjectError("Workspace / project must be relative to the project directory")
    if path.suffix not in {".xcworkspace", ".xcodeproj"}:
        raise ProjectError("Workspace / project must end in .xcworkspace or .xcodeproj")
    return str(path)


def normalize_project(data: dict) -> dict:
    project_id = _clean_text(data.get("id") or str(uuid.uuid4()), "Project id", max_len=80)
    name = _clean_text(data.get("name") or "", "Project name", max_len=120)
    if not name:
        raise ProjectError("Project name is required")
    server_id = _clean_text(data.get("server_id") or "", "Mac server id", max_len=160)
    if not server_id:
        raise ProjectError("Project must be assigned to a paired Mac")
    remote_path = validate_remote_path(data.get("remote_path") or "")
    container = validate_container(data.get("container") or "")
    scheme = _clean_text(data.get("scheme") or "", "Scheme", max_len=200)
    configuration = _clean_text(data.get("configuration") or "Debug", "Configuration", max_len=80) or "Debug"
    destination = _clean_text(data.get("destination") or "", "Destination", max_len=400)
    local_path = validate_local_path(data.get("local_path") or "")
    sync_excludes = validate_sync_excludes(data.get("sync_excludes") or [])
    return {
        "id": project_id,
        "name": name,
        "server_id": server_id,
        "server_name": _clean_text(data.get("server_name") or "", "Mac name", max_len=200),
        "remote_path": remote_path,
        "container": container,
        "scheme": scheme,
        "configuration": configuration,
        "destination": destination,
        "local_path": local_path,
        "sync_excludes": sync_excludes,
    }


def load_projects() -> list[dict]:
    try:
        raw = json.loads(PROJECTS_PATH.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return []
    except Exception:
        return []
    if not isinstance(raw, list):
        return []
    result: list[dict] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        try:
            result.append(normalize_project(item))
        except ProjectError:
            continue
    return sorted(result, key=lambda item: item["name"].lower())


def save_projects(projects: list[dict]) -> None:
    CONFIG_ROOT.mkdir(parents=True, exist_ok=True, mode=0o700)
    normalized = [normalize_project(item) for item in projects]
    tmp = PROJECTS_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(normalized, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.chmod(tmp, 0o600)
    os.replace(tmp, PROJECTS_PATH)


def upsert_project(project: dict) -> dict:
    normalized = normalize_project(project)
    projects = load_projects()
    by_id = {item["id"]: item for item in projects}
    by_id[normalized["id"]] = normalized
    save_projects(list(by_id.values()))
    return normalized


def delete_project(project_id: str) -> None:
    projects = [item for item in load_projects() if item.get("id") != project_id]
    save_projects(projects)


def projects_for_server(server_id: str) -> list[dict]:
    return [item for item in load_projects() if item.get("server_id") == server_id]


def _xcodebuild_args(project: dict) -> list[str]:
    args = ["/usr/bin/xcrun", "xcodebuild"]
    container = project.get("container") or ""
    if container.endswith(".xcworkspace"):
        args += ["-workspace", container]
    elif container.endswith(".xcodeproj"):
        args += ["-project", container]
    if project.get("scheme"):
        args += ["-scheme", project["scheme"]]
    if project.get("configuration"):
        args += ["-configuration", project["configuration"]]
    if project.get("destination"):
        args += ["-destination", project["destination"]]
    return args


def build_project_command(project: dict, action: str) -> str:
    """Build one quoted remote shell command for a saved project action."""
    project = normalize_project(project)
    if action not in PROJECT_ACTIONS:
        raise ProjectError(f"Unsupported project action: {action}")
    cwd = shlex.quote(project["remote_path"])
    prefix = f"cd {cwd} && "
    if action == "git-status":
        return prefix + "env GIT_TERMINAL_PROMPT=0 /usr/bin/git status --short --branch"
    if action == "git-pull":
        return prefix + "env GIT_TERMINAL_PROMPT=0 /usr/bin/git pull --ff-only"
    if action == "open-xcode":
        target = project.get("container") or "."
        return prefix + shlex.join(["/usr/bin/open", "-a", "Xcode", target])
    if action == "inspect":
        args = _xcodebuild_args(project) + ["-list"]
        return prefix + shlex.join(args)
    args = _xcodebuild_args(project)
    if action == "build":
        args.append("build")
    elif action == "test":
        args.append("test")
    elif action == "clean":
        args.append("clean")
    return prefix + shlex.join(args)
