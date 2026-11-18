#!/usr/bin/env python3
from __future__ import annotations

from dataclasses import dataclass

SEVERITY_ORDER = {"critical": 0, "warning": 1, "info": 2}


@dataclass(frozen=True)
class RecoveryIssue:
    severity: str
    code: str
    repairable: bool
    summary: str


def parse_recovery_report(text: str) -> tuple[dict[str, str], list[RecoveryIssue]]:
    meta: dict[str, str] = {}
    issues: list[RecoveryIssue] = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if key != "issue":
            if key:
                meta[key] = value
            continue
        parts = value.split("|", 3)
        if len(parts) != 4:
            continue
        severity, code, repairable, summary = (part.strip() for part in parts)
        if severity not in SEVERITY_ORDER or not code:
            continue
        issues.append(RecoveryIssue(severity, code, repairable == "1", summary))
    issues.sort(key=lambda item: (SEVERITY_ORDER.get(item.severity, 99), item.code))
    return meta, issues


def recovery_summary(meta: dict[str, str], issues: list[RecoveryIssue]) -> str:
    overall = meta.get("overall", "unknown")
    if not issues:
        return "HEALTHY — no recovery issues detected" if overall == "healthy" else overall.upper()
    critical = sum(1 for item in issues if item.severity == "critical")
    warning = sum(1 for item in issues if item.severity == "warning")
    repairable = sum(1 for item in issues if item.repairable)
    parts: list[str] = []
    if critical:
        parts.append(f"{critical} critical")
    if warning:
        parts.append(f"{warning} warning")
    parts.append(f"{repairable} auto-repairable")
    return f"{overall.upper()} — " + ", ".join(parts)


def render_recovery_report(meta: dict[str, str], issues: list[RecoveryIssue]) -> str:
    lines = [recovery_summary(meta, issues), ""]
    if not issues:
        lines.append("No issues detected by P6 diagnostics.")
    else:
        for item in issues:
            marker = "AUTO" if item.repairable else "MANUAL"
            lines.append(f"[{item.severity.upper()}] {item.code} [{marker}]")
            lines.append(f"  {item.summary}")
    checked = meta.get("checked_at")
    if checked:
        lines.extend(["", f"checked_at={checked}"])
    return "\n".join(lines)


def classify_transport_error(message: str) -> RecoveryIssue:
    text = (message or "").strip()
    lower = text.lower()
    if "host key verification failed" in lower or "remote host identification has changed" in lower:
        return RecoveryIssue("critical", "SSH_HOST_KEY_CHANGED", False, "The pinned SSH host key no longer matches. Verify the Mac locally before re-pairing; do not auto-accept a new key.")
    if "connection refused" in lower:
        return RecoveryIssue("critical", "SSH_CONNECTION_REFUSED", False, "The Mac answered on the network but SSH/Remote Login is not accepting connections.")
    if "no route to host" in lower or "network is unreachable" in lower:
        return RecoveryIssue("critical", "NETWORK_UNREACHABLE", False, "The stored/discovered Mac address is not reachable from Zorin. Re-run discovery and check the LAN/router.")
    if "timed out" in lower or "operation timed out" in lower or "connection timed out" in lower:
        return RecoveryIssue("warning", "SSH_TIMEOUT", False, "The Mac did not answer SSH before the timeout. It may be asleep, offline, on a new DHCP address, or the LAN may be unavailable.")
    if "permission denied" in lower:
        return RecoveryIssue("critical", "SSH_AUTH_FAILED", False, "SSH reached the Mac but the paired recovery key was rejected. Re-pair after verifying the host fingerprint.")
    return RecoveryIssue("warning", "RECOVERY_TRANSPORT_FAILED", False, text or "The recovery transport failed before Mac diagnostics could run.")
