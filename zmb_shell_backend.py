#!/usr/bin/env python3
from __future__ import annotations

import os
from pathlib import Path
import re
import shlex
import shutil
import signal
import stat
import subprocess
import tempfile
from typing import Iterable

from zmb_admin_backend import (
    AdminError,
    CONFIG_ROOT,
    _load_profiles,
    _save_profiles,
    get_profile,
    profile_dir,
    profile_id,
    save_profile,
)

SHELL_MARKER_PREFIX = "ZorinMacBridge-Shell:"
SHELL_KEY_COMMENT_PREFIX = "ZorinMacBridge Shell"


def ensure_shell_tools() -> None:
    missing = [name for name in ("ssh", "ssh-keygen") if shutil.which(name) is None]
    if missing:
        raise AdminError("Missing OpenSSH tools: " + ", ".join(missing) + ". Install openssh-client on Zorin/Ubuntu.")


def shell_key_paths(profile_or_pid: dict | str) -> tuple[Path, Path]:
    pid = profile_or_pid if isinstance(profile_or_pid, str) else str(profile_or_pid.get("profile_id") or "")
    if not pid:
        raise AdminError("Pairing profile is missing profile_id")
    root = profile_dir(pid)
    return root / "shell_ed25519", root / "shell_ed25519.pub"


def ensure_shell_key(pid: str, device_id: str) -> tuple[Path, Path]:
    ensure_shell_tools()
    root = profile_dir(pid)
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    key, pub = shell_key_paths(pid)
    if not key.exists():
        proc = subprocess.run(
            [
                "ssh-keygen", "-t", "ed25519", "-a", "100", "-N", "",
                "-C", f"{SHELL_KEY_COMMENT_PREFIX} {device_id}", "-f", str(key),
            ],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=30,
            check=False,
        )
        if proc.returncode != 0:
            raise AdminError((proc.stderr or proc.stdout or "ssh-keygen failed").strip())
    os.chmod(key, 0o600)
    if not pub.exists():
        raise AdminError("Generated terminal SSH public key is missing")
    os.chmod(pub, 0o644)
    return key, pub


def shell_authorized_key(pubkey_text: str, device_id: str) -> str:
    """Return a user-shell authorized_keys line.

    The terminal key is intentionally a general *user* shell key, but forwarding
    and agent/X11 forwarding remain disabled. PTY is allowed because P3 embeds a
    real interactive terminal. This key never receives sudo privileges from ZMB.
    """
    parts = pubkey_text.strip().split()
    if len(parts) < 2 or parts[0] != "ssh-ed25519":
        raise AdminError("Unexpected terminal public key format; expected Ed25519")
    options = "no-agent-forwarding,no-port-forwarding,no-X11-forwarding,no-user-rc"
    return f"{options} {parts[0]} {parts[1]} {SHELL_MARKER_PREFIX}{device_id}"


def get_shell_profile(server_id: str, host: str) -> dict | None:
    """Return a profile while refreshing its transport address after DHCP changes."""
    profile = get_profile(server_id, host)
    if profile and host and str(profile.get("host") or "") != host:
        profile = save_profile(server_id, host, {**profile, "host": host})
    return profile


def shell_ready(profile: dict | None) -> bool:
    if not profile or not profile.get("shell_enabled"):
        return False
    try:
        key, pub = shell_key_paths(profile)
    except Exception:
        return False
    return key.is_file() and pub.is_file()


def shell_ssh_transport_argv(profile: dict) -> list[str]:
    """Build the pinned SSH transport argv without destination/TTY arguments.

    P4 reuses this exact transport for rsync so Project Sync receives the same
    pinned host key, dedicated user key, timeouts and forwarding restrictions as
    the P3 terminal instead of constructing a weaker second SSH configuration.
    """
    ensure_shell_tools()
    for field in ("host", "user", "known_hosts", "profile_id"):
        if not profile.get(field):
            raise AdminError(f"Pairing profile is missing {field}")
    if not shell_ready(profile):
        raise AdminError("Terminal access is not enabled for this Mac. Re-pair it in System with 'Enable Terminal / Project Actions' selected.")
    key, _pub = shell_key_paths(profile)
    host_alias = str(profile.get("host_key_alias") or ("zmb-" + str(profile["profile_id"])))
    return [
        "ssh",
        "-p", str(int(profile.get("ssh_port", 22))),
        "-i", str(key),
        "-o", "IdentitiesOnly=yes",
        "-o", "BatchMode=yes",
        "-o", "PasswordAuthentication=no",
        "-o", "KbdInteractiveAuthentication=no",
        "-o", "ConnectionAttempts=1",
        "-o", f"UserKnownHostsFile={os.path.expanduser(str(profile['known_hosts']))}",
        "-o", f"HostKeyAlias={host_alias}",
        "-o", "StrictHostKeyChecking=yes",
        "-o", "ConnectTimeout=6",
        "-o", "ServerAliveInterval=10",
        "-o", "ServerAliveCountMax=2",
        "-o", "ForwardAgent=no",
        "-o", "ClearAllForwardings=yes",
        "-o", "LogLevel=ERROR",
    ]


def shell_ssh_argv(profile: dict, *, tty: bool = False, command: str | None = None) -> list[str]:
    """Build a pinned SSH argv for the normal macOS user shell.

    This is deliberately separate from the restricted P0 admin key. The terminal
    key has no sudoers entry and therefore has only the privileges of the paired
    macOS user.
    """
    argv = shell_ssh_transport_argv(profile)
    if tty:
        argv += ["-tt", "-o", "RequestTTY=force"]
    else:
        argv += ["-T", "-o", "RequestTTY=no"]
    argv.append(f"{profile['user']}@{profile['host']}")
    if command is not None:
        argv.append(command)
    return argv


def run_shell_command(profile: dict, remote_command: str, *, timeout: int = 30) -> subprocess.CompletedProcess[str]:
    argv = shell_ssh_argv(profile, tty=False, command=remote_command)
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
        raise AdminError("ssh was not found; install openssh-client") from exc
    try:
        out, err = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        except Exception:
            proc.kill()
        out, err = proc.communicate()
        detail = (err or out or "").strip()
        msg = f"Terminal command timed out after {timeout}s"
        if detail:
            msg += f": {detail}"
        raise AdminError(msg) from exc
    return subprocess.CompletedProcess(argv, proc.returncode, out, err)


def start_shell_command(profile: dict, remote_command: str) -> subprocess.Popen[str]:
    """Start a streamable project action in its own local process group."""
    argv = shell_ssh_argv(profile, tty=False, command=remote_command)
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
        raise AdminError("ssh was not found; install openssh-client") from exc


def terminate_shell_process(proc: subprocess.Popen | None, *, wait: bool = True) -> None:
    if proc is None or proc.poll() is not None:
        return
    try:
        os.killpg(proc.pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    except Exception:
        try:
            proc.terminate()
        except Exception:
            return
    if not wait:
        return
    try:
        proc.wait(timeout=2)
        return
    except Exception:
        pass
    try:
        os.killpg(proc.pid, signal.SIGKILL)
    except Exception:
        try:
            proc.kill()
        except Exception:
            pass


def _askpass_env(password: str, workdir: Path) -> tuple[dict, Path, Path]:
    secret = workdir / ".shell-pairing-secret"
    askpass = workdir / ".zmb-shell-askpass"
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


def install_shell_access(profile: dict, password: str) -> dict:
    """Install/replace the dedicated user-shell key using one password SSH login."""
    if not password:
        raise AdminError("The macOS account password is required to enable Terminal / Project Actions")
    pid = str(profile.get("profile_id") or "")
    device_id = str(profile.get("device_id") or "")
    if not pid or not device_id:
        raise AdminError("Pairing profile is incomplete")
    key, pub = ensure_shell_key(pid, device_id)
    marker = f"{SHELL_MARKER_PREFIX}{device_id}"
    line = shell_authorized_key(pub.read_text(encoding="utf-8"), device_id)
    remote = (
        "umask 077; mkdir -p ~/.ssh; chmod 700 ~/.ssh; touch ~/.ssh/authorized_keys; "
        "tmp=$(/usr/bin/mktemp ~/.ssh/.zmb-shell-authorized_keys.XXXXXX) || exit 73; "
        f"/usr/bin/awk -v marker={shlex.quote(marker)} 'index($0, marker)==0 {{print}}' ~/.ssh/authorized_keys > \"$tmp\" || {{ rm -f \"$tmp\"; exit 74; }}; "
        f"printf '%s\\n' {shlex.quote(line)} >> \"$tmp\" || {{ rm -f \"$tmp\"; exit 74; }}; "
        "chmod 600 \"$tmp\"; mv -f \"$tmp\" ~/.ssh/authorized_keys"
    )
    root = profile_dir(pid)
    env, secret, askpass = _askpass_env(password, root)
    try:
        argv = [
            "ssh", "-p", str(int(profile.get("ssh_port", 22))),
            "-o", f"UserKnownHostsFile={os.path.expanduser(str(profile['known_hosts']))}",
            "-o", f"HostKeyAlias={profile.get('host_key_alias', 'zmb-' + pid)}",
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
            f"{profile['user']}@{profile['host']}", remote,
        ]
        proc = subprocess.Popen(
            argv,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env=env,
            start_new_session=True,
        )
        try:
            out, err = proc.communicate(timeout=25)
        except subprocess.TimeoutExpired as exc:
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except Exception:
                proc.kill()
            out, err = proc.communicate()
            raise AdminError("Enabling Terminal / Project Actions timed out") from exc
    finally:
        for path in (secret, askpass):
            try:
                path.unlink()
            except OSError:
                pass
    if proc.returncode != 0:
        raise AdminError((err or out or f"SSH exited with {proc.returncode}").strip())

    updated = dict(profile)
    updated["shell_enabled"] = True
    updated["shell_key"] = str(key)
    saved = save_profile(str(updated.get("server_id") or ""), str(updated.get("host") or ""), updated)

    # Verify the shell key using a non-interactive identity command.
    check = run_shell_command(saved, "/usr/bin/id -un", timeout=12)
    if check.returncode != 0 or (check.stdout or "").strip() != str(saved.get("user") or ""):
        raise AdminError("Terminal key was installed but user-shell verification failed")
    return saved


def revoke_shell_access(profile: dict) -> None:
    """Remove this device's shell key using the already-authenticated shell key."""
    if not shell_ready(profile):
        return
    marker = f"{SHELL_MARKER_PREFIX}{profile.get('device_id', '')}"
    if not marker.endswith(":") and profile.get("device_id"):
        remote = (
            "set -e; keys=~/.ssh/authorized_keys; [ -f \"$keys\" ] || exit 0; "
            "tmp=$(/usr/bin/mktemp ~/.ssh/.zmb-shell-revoke.XXXXXX) || exit 73; "
            f"/usr/bin/awk -v marker={shlex.quote(marker)} 'index($0, marker)==0 {{print}}' \"$keys\" > \"$tmp\"; "
            "chmod 600 \"$tmp\"; mv -f \"$tmp\" \"$keys\""
        )
        proc = run_shell_command(profile, remote, timeout=15)
        if proc.returncode != 0:
            raise AdminError((proc.stderr or proc.stdout or "Could not revoke terminal key").strip())
    updated = dict(profile)
    updated["shell_enabled"] = False
    save_profile(str(updated.get("server_id") or ""), str(updated.get("host") or ""), updated)
