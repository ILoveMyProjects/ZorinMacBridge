#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import signal
import stat
import subprocess
import tempfile

CONFIG_ROOT = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "zorinmacbridge" / "admin"
PROFILES_PATH = CONFIG_ROOT / "profiles.json"
REMOTE_PREFIX = "zmb-admin"
PROTOCOL_VERSION = "2"

ADMIN_ACTIONS = {
    "status", "start-zmb", "stop-zmb", "restart-zmb", "repair-launch-agent",
    "pairing-list", "logs", "check-update", "update-now", "sleep", "reboot", "shutdown",
}
AUTO_UPDATE_MODES = {"off", "notify", "install"}


class AdminError(RuntimeError):
    pass


def _run(argv: list[str], *, timeout: int = 25, env: dict | None = None, input_data: str | None = None) -> subprocess.CompletedProcess[str]:
    """Run a helper with a hard wall-clock timeout.

    The process is placed in its own session so a timeout can terminate the whole
    process group (for example ssh plus any helper it spawned), not only the
    immediate wrapper process. This keeps a failed remote action from leaving the
    GTK client stuck in a permanent busy state.
    """
    try:
        proc = subprocess.Popen(
            argv,
            text=True,
            stdin=subprocess.PIPE if input_data is not None else subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=env,
            start_new_session=True,
        )
    except FileNotFoundError as exc:
        raise AdminError(f"Required command not found: {argv[0]}") from exc

    try:
        stdout, stderr = proc.communicate(input=input_data, timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass
        stdout, stderr = proc.communicate()
        detail = (stderr or stdout or '').strip()
        message = f"Timed out after {timeout}s while running {Path(argv[0]).name}"
        if detail:
            message += f": {detail}"
        raise AdminError(message) from exc

    return subprocess.CompletedProcess(argv, proc.returncode, stdout, stderr)


def ensure_tools() -> None:
    missing = [name for name in ("ssh", "ssh-keygen", "ssh-keyscan") if shutil.which(name) is None]
    if missing:
        raise AdminError("Missing OpenSSH tools: " + ", ".join(missing) + ". Install openssh-client on Zorin/Ubuntu.")


def _load_profiles() -> dict:
    try:
        data = json.loads(PROFILES_PATH.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            return data
    except FileNotFoundError:
        pass
    except Exception:
        pass
    return {}


def _save_profiles(data: dict) -> None:
    CONFIG_ROOT.mkdir(parents=True, exist_ok=True, mode=0o700)
    tmp = PROFILES_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.chmod(tmp, 0o600)
    os.replace(tmp, PROFILES_PATH)


def profile_id(server_id: str, host: str) -> str:
    value = (server_id or "").strip() or f"host:{host.strip()}"
    return hashlib.sha256(value.encode("utf-8", "surrogatepass")).hexdigest()[:24]


def profile_dir(pid: str) -> Path:
    return CONFIG_ROOT / "devices" / pid


def get_profile(server_id: str, host: str) -> dict | None:
    pid = profile_id(server_id, host)
    item = _load_profiles().get(pid)
    if not isinstance(item, dict):
        return None
    item = dict(item)
    item["profile_id"] = pid
    return item


def list_profiles() -> list[dict]:
    """Return locally paired administration profiles for recovery/offline selection."""
    out: list[dict] = []
    for pid, value in _load_profiles().items():
        if not isinstance(value, dict):
            continue
        item = dict(value)
        item["profile_id"] = str(pid)
        out.append(item)
    return sorted(out, key=lambda item: str(item.get("server_name") or item.get("host") or "").lower())


def save_profile(server_id: str, host: str, profile: dict) -> dict:
    pid = profile_id(server_id, host)
    data = _load_profiles()
    item = dict(profile)
    item["server_id"] = server_id
    item["host"] = host
    item["profile_id"] = pid
    data[pid] = item
    _save_profiles(data)
    return item


def forget_profile(server_id: str, host: str) -> None:
    pid = profile_id(server_id, host)
    data = _load_profiles()
    data.pop(pid, None)
    _save_profiles(data)


def update_profile_host(server_id: str, old_host: str, new_host: str) -> dict | None:
    item = get_profile(server_id, old_host)
    if not item:
        return get_profile(server_id, new_host)
    item["host"] = new_host
    return save_profile(server_id, new_host, item)


def _validate_user(user: str) -> str:
    user = user.strip()
    if not re.fullmatch(r"[A-Za-z0-9._-]+", user):
        raise AdminError("Invalid macOS short username")
    return user


def _validate_host(host: str) -> str:
    host = host.strip()
    if not host or len(host) > 255 or any(ch.isspace() for ch in host):
        raise AdminError("Invalid Mac host/IP")
    return host


def _validate_port(port: int) -> int:
    port = int(port)
    if port < 1 or port > 65535:
        raise AdminError("Invalid SSH port")
    return port


def _key_paths(pid: str) -> tuple[Path, Path, Path]:
    root = profile_dir(pid)
    return root / "admin_ed25519", root / "admin_ed25519.pub", root / "known_hosts"


def _ensure_key(pid: str, device_id: str) -> tuple[Path, Path, Path]:
    ensure_tools()
    root = profile_dir(pid)
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    key, pub, known = _key_paths(pid)
    if not key.exists():
        p = _run([
            "ssh-keygen", "-t", "ed25519", "-a", "100", "-N", "",
            "-C", f"ZorinMacBridge Admin {device_id}", "-f", str(key),
        ], timeout=30)
        if p.returncode != 0:
            raise AdminError((p.stderr or p.stdout or "ssh-keygen failed").strip())
    os.chmod(key, 0o600)
    if not pub.exists():
        raise AdminError("Generated SSH public key is missing")
    if not known.exists():
        known.touch(mode=0o600)
    os.chmod(known, 0o600)
    return key, pub, known


def scan_host_keys(host: str, port: int = 22) -> dict:
    """Return exact ssh-keyscan lines plus fingerprints for GUI verification."""
    ensure_tools()
    host = _validate_host(host)
    port = _validate_port(port)
    p = _run(["ssh-keyscan", "-T", "4", "-p", str(port), host], timeout=7)
    lines = [line.strip() for line in p.stdout.splitlines() if line.strip() and not line.startswith("#")]
    if not lines:
        detail = (p.stderr or "No SSH host key returned").strip()
        raise AdminError(f"Could not read the Mac SSH host key: {detail}")

    fingerprints: list[dict[str, str]] = []
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", delete=False) as fp:
        for line in lines:
            fp.write(line + "\n")
        temp_name = fp.name
    try:
        q = _run(["ssh-keygen", "-lf", temp_name, "-E", "sha256"], timeout=7)
        if q.returncode != 0:
            raise AdminError((q.stderr or q.stdout or "Could not fingerprint SSH host key").strip())
        rows = q.stdout.splitlines()
        for idx, row in enumerate(rows):
            # e.g. 256 SHA256:abc host (ED25519). ssh-keygen preserves input order.
            match = re.match(r"\s*\d+\s+(SHA256:\S+)\s+.*\(([^)]+)\)\s*$", row)
            if match and idx < len(lines):
                fingerprints.append({"fingerprint": match.group(1), "type": match.group(2), "line": lines[idx]})
    finally:
        try:
            os.unlink(temp_name)
        except OSError:
            pass
    if not fingerprints:
        raise AdminError("The Mac returned SSH keys, but their SHA-256 fingerprint could not be parsed")
    preferred = next((x for x in fingerprints if "ED25519" in x["type"].upper()), fingerprints[0])
    return {"lines": lines, "fingerprints": fingerprints, "preferred": preferred}


def _restricted_authorized_key(pubkey_text: str, device_id: str) -> str:
    parts = pubkey_text.strip().split()
    if len(parts) < 2 or parts[0] != "ssh-ed25519":
        raise AdminError("Unexpected public key format; expected Ed25519")
    options = 'restrict,command="/usr/local/libexec/zmb-admin-ssh"'
    return f"{options} {parts[0]} {parts[1]} ZorinMacBridge-Admin:{device_id}"


def _askpass_env(password: str, workdir: Path) -> tuple[dict, Path, Path]:
    secret = workdir / ".pairing-secret"
    askpass = workdir / ".zmb-askpass"
    secret.write_text(password, encoding="utf-8")
    os.chmod(secret, 0o600)
    askpass.write_text("#!/bin/sh\nexec /bin/cat \"$ZMB_ASKPASS_SECRET\"\n", encoding="utf-8")
    os.chmod(askpass, stat.S_IRUSR | stat.S_IWUSR | stat.S_IXUSR)
    env = os.environ.copy()
    env.update({
        "SSH_ASKPASS": str(askpass),
        "SSH_ASKPASS_REQUIRE": "force",
        "ZMB_ASKPASS_SECRET": str(secret),
        "DISPLAY": env.get("DISPLAY") or ":0",
    })
    return env, secret, askpass


def pair_device(*, server_id: str, server_name: str, host: str, user: str, password: str,
                ssh_port: int = 22, desktop_port: int = 45950,
                scanned_host_keys: list[str] | None = None) -> dict:
    """Pair from GUI. Password is used only for this one SSH login and is never persisted."""
    ensure_tools()
    host = _validate_host(host)
    user = _validate_user(user)
    ssh_port = _validate_port(ssh_port)
    desktop_port = _validate_port(desktop_port)
    if not password:
        raise AdminError("The macOS account password is required for first pairing")
    scan = {"lines": scanned_host_keys} if scanned_host_keys else scan_host_keys(host, ssh_port)
    lines = list(scan.get("lines") or [])
    if not lines:
        raise AdminError("No verified SSH host key supplied")

    pid = profile_id(server_id, host)
    old = get_profile(server_id, host) or {}
    device_id = str(old.get("device_id") or f"dev-{pid[:16]}")
    key, pub, known = _ensure_key(pid, device_id)
    host_key_alias = f"zmb-{pid}"
    known_host_token = host_key_alias if ssh_port == 22 else f"[{host_key_alias}]:{ssh_port}"
    aliased_lines = []
    for line in lines:
        parts = line.split()
        if len(parts) < 3:
            continue
        aliased_lines.append(" ".join([known_host_token, *parts[1:]]))
    if not aliased_lines:
        raise AdminError("Verified SSH host-key data is malformed")
    known.write_text("\n".join(aliased_lines) + "\n", encoding="utf-8")
    os.chmod(known, 0o600)

    restricted = _restricted_authorized_key(pub.read_text(encoding="utf-8"), device_id)
    marker = f"ZorinMacBridge-Admin:{device_id}"
    # Replace any previous key for this deterministic device id instead of only
    # checking the comment marker. This makes re-pairing recover correctly after
    # the local Ed25519 key was regenerated. The command runs as the target user,
    # not root, and uses mktemp+mv for an atomic authorized_keys update.
    remote = (
        "umask 077; mkdir -p ~/.ssh; chmod 700 ~/.ssh; touch ~/.ssh/authorized_keys; "
        "tmp=$(/usr/bin/mktemp ~/.ssh/.zmb-authorized_keys.XXXXXX) || exit 73; "
        f"/usr/bin/awk -v marker={shlex.quote(marker)} 'index($0, marker)==0 {{print}}' ~/.ssh/authorized_keys > \"$tmp\" || {{ rm -f \"$tmp\"; exit 74; }}; "
        f"printf '%s\n' {shlex.quote(restricted)} >> \"$tmp\" || {{ rm -f \"$tmp\"; exit 74; }}; "
        "chmod 600 \"$tmp\"; mv -f \"$tmp\" ~/.ssh/authorized_keys"
    )

    root = profile_dir(pid)
    env, secret, askpass = _askpass_env(password, root)
    try:
        argv = [
            "ssh",
            "-p", str(ssh_port),
            "-o", f"UserKnownHostsFile={known}",
            "-o", f"HostKeyAlias={host_key_alias}",
            "-o", "StrictHostKeyChecking=yes",
            "-o", "PubkeyAuthentication=no",
            "-o", "PreferredAuthentications=keyboard-interactive,password",
            "-o", "NumberOfPasswordPrompts=1",
            "-o", "ConnectionAttempts=1",
            "-o", "ConnectTimeout=6",
            "-o", "ServerAliveInterval=5",
            "-o", "ServerAliveCountMax=1",
            "-o", "RequestTTY=no",
            "-o", "LogLevel=ERROR",
            f"{user}@{host}", remote,
        ]
        p = _run(argv, timeout=25, env=env)
    finally:
        for path in (secret, askpass):
            try:
                path.unlink()
            except OSError:
                pass
    if p.returncode != 0:
        detail = (p.stderr or p.stdout or f"SSH exited with {p.returncode}").strip()
        raise AdminError("First pairing failed: " + detail)

    candidate = {
        "server_id": server_id,
        "server_name": server_name or host,
        "host": host,
        "profile_id": pid,
        "user": user,
        "ssh_port": ssh_port,
        "desktop_port": desktop_port,
        "key": str(key),
        "known_hosts": str(known),
        "device_id": device_id,
        "host_key_alias": host_key_alias,
    }

    # Verify the restricted key and P0 helper before committing a paired profile.
    # The device id is deterministic for this profile, so a retry does not append
    # duplicate authorized_keys entries if the helper was temporarily unavailable.
    data = get_status_for_profile(candidate)
    if data.get("protocol") != PROTOCOL_VERSION:
        raise AdminError("Pairing key was installed, but the Mac P0 helper returned an unexpected protocol")
    return save_profile(server_id, host, candidate)


def _remote_command(action: str, arg: str | None = None) -> str:
    if action in ADMIN_ACTIONS and arg is None:
        return f"{REMOTE_PREFIX} {action}"
    if action == "set-auto-update" and arg in AUTO_UPDATE_MODES:
        return f"{REMOTE_PREFIX} set-auto-update {arg}"
    if action == "unpair" and arg and re.fullmatch(r"[A-Za-z0-9._-]+", arg):
        return f"{REMOTE_PREFIX} unpair {arg}"
    raise AdminError(f"Unsupported admin action: {action}")


def _ssh_base(profile: dict) -> list[str]:
    for k in ("host", "user", "key", "known_hosts"):
        if not profile.get(k):
            raise AdminError(f"Pairing profile is missing {k}")
    return [
        "ssh", "-p", str(profile.get("ssh_port", 22)),
        "-i", os.path.expanduser(str(profile["key"])),
        "-o", "IdentitiesOnly=yes",
        "-o", "BatchMode=yes",
        "-o", "PasswordAuthentication=no",
        "-o", "KbdInteractiveAuthentication=no",
        "-o", "ConnectionAttempts=1",
        "-o", f"UserKnownHostsFile={os.path.expanduser(str(profile['known_hosts']))}",
        "-o", f"HostKeyAlias={profile.get('host_key_alias', 'zmb-' + str(profile.get('profile_id', 'device')))}",
        "-o", "StrictHostKeyChecking=yes",
        "-o", "ConnectTimeout=5",
        "-o", "ServerAliveInterval=5",
        "-o", "ServerAliveCountMax=1",
        "-o", "RequestTTY=no",
        "-o", "LogLevel=ERROR",
        f"{profile['user']}@{profile['host']}",
    ]


def run_action_for_profile(profile: dict, action: str, *, arg: str | None = None, timeout: int = 18) -> subprocess.CompletedProcess[str]:
    ensure_tools()
    return _run(_ssh_base(profile) + [_remote_command(action, arg)], timeout=timeout)


def run_action(*, server_id: str, host: str, action: str, arg: str | None = None, timeout: int = 18) -> subprocess.CompletedProcess[str]:
    profile = get_profile(server_id, host)
    if not profile:
        raise AdminError("This Mac is not paired for System administration")
    # Discovery can reveal a new DHCP address for the same persistent server id.
    if host and profile.get("host") != host:
        profile = save_profile(server_id, host, {**profile, "host": host})
    return run_action_for_profile(profile, action, arg=arg, timeout=timeout)


def parse_status(text: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for raw in text.splitlines():
        if "=" not in raw:
            continue
        key, value = raw.split("=", 1)
        key = key.strip()
        if key:
            out[key] = value.strip()
    return out


def get_status_for_profile(profile: dict) -> dict[str, str]:
    p = run_action_for_profile(profile, "status", timeout=12)
    if p.returncode != 0:
        raise AdminError((p.stderr or p.stdout or f"status exited {p.returncode}").strip())
    data = parse_status(p.stdout)
    if data.get("protocol") != PROTOCOL_VERSION:
        raise AdminError(f"Unexpected P0 protocol: {data.get('protocol', 'missing')}")
    return data


def get_status(*, server_id: str, host: str) -> dict[str, str]:
    profile = get_profile(server_id, host)
    if not profile:
        raise AdminError("This Mac is not paired for System administration")
    if host and profile.get("host") != host:
        profile = save_profile(server_id, host, {**profile, "host": host})
    return get_status_for_profile(profile)


def status_summary(data: dict[str, str]) -> str:
    if data.get("admin_daemon_loaded") != "1":
        return "DEGRADED — system service is not loaded"
    try:
        age = int(data.get("admin_heartbeat_age_seconds", "-1"))
    except ValueError:
        age = -1
    if age < 0 or age > 60:
        return "DEGRADED — system service heartbeat is stale"
    if data.get("user_session_available") != "1":
        return "SYSTEM ONLINE — no logged-in GUI session"
    if data.get("zmb_app_running") == "1" and data.get("zmb_port_listening") == "1":
        return "READY — system and remote desktop are online"
    return "SYSTEM ONLINE — desktop service needs recovery"


def reboot_readiness(data: dict[str, str]) -> tuple[bool, list[str]]:
    notes: list[str] = []
    fv = data.get("filevault", "unknown")
    eligible = data.get("filevault_ssh_unlock_eligible") == "1"
    if fv == "off":
        notes.append("FileVault is off.")
        return True, notes
    if fv == "on" and eligible:
        notes.append("FileVault is on and this Mac reports eligibility for Apple's SSH unlock-after-restart path.")
        notes.append("Network availability in preboot is still required.")
        return True, notes
    if fv == "on":
        notes.append("FileVault is on and remote unlock is not currently detected as eligible.")
        notes.append("A local unlock may be required after reboot.")
        return False, notes
    notes.append("FileVault state is unknown; reboot may require local access.")
    return False, notes


def revoke_current_device(*, server_id: str, host: str) -> None:
    profile = get_profile(server_id, host)
    if not profile:
        return
    device_id = str(profile.get("device_id") or "")
    if device_id:
        p = run_action_for_profile(profile, "unpair", arg=device_id, timeout=15)
        if p.returncode != 0:
            raise AdminError((p.stderr or p.stdout or "Could not revoke paired key").strip())
    forget_profile(server_id, host)
