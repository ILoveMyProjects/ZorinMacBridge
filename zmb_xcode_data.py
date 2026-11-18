#!/usr/bin/env python3
from __future__ import annotations

import json


def parse_xcode_overview(text: str) -> tuple[dict[str, str], list[dict[str, str]]]:
    """Parse the constrained zmb-admin xcode-overview response.

    The key/value header is deliberately simple for shell generation. The JSON
    payload is the unmodified `xcrun simctl list --json` result when simctl is
    available.
    """
    marker = "---SIMCTL-JSON---"
    head, sep, tail = text.partition(marker)
    meta: dict[str, str] = {}
    for raw in head.splitlines():
        if "=" in raw:
            key, value = raw.split("=", 1)
            meta[key.strip()] = value.strip()
    devices: list[dict[str, str]] = []
    if not sep:
        return meta, devices
    try:
        payload = json.loads(tail.strip() or "{}")
    except json.JSONDecodeError as exc:
        meta["simctl_error"] = f"Could not parse simctl JSON: {exc}"
        return meta, devices
    runtime_names = {
        str(item.get("identifier", "")): str(item.get("name") or item.get("version") or item.get("identifier") or "")
        for item in (payload.get("runtimes") or []) if isinstance(item, dict)
    }
    for runtime_id, rows in (payload.get("devices") or {}).items():
        if not isinstance(rows, list):
            continue
        for row in rows:
            if not isinstance(row, dict) or row.get("isAvailable") is False:
                continue
            udid = str(row.get("udid") or "")
            if not udid:
                continue
            devices.append({
                "name": str(row.get("name") or "Simulator"),
                "udid": udid,
                "state": str(row.get("state") or "Unknown"),
                "runtime": runtime_names.get(str(runtime_id), str(runtime_id).split(".")[-1]),
            })
    devices.sort(key=lambda d: (d["state"] != "Booted", d["runtime"], d["name"]))
    return meta, devices
