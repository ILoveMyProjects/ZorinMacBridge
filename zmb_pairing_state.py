#!/usr/bin/env python3
from __future__ import annotations

from zmb_admin_backend import normalize_tls_fingerprint
from settings import save_client_record


def persist_desktop_trust(profile: dict | None, status: dict | None = None) -> str:
    """Persist desktop TLS trust learned through an already-pinned P0 SSH channel.

    Returns the canonical SHA-256 fingerprint when trust was persisted, otherwise
    an empty string. Discovery metadata is intentionally never accepted here.
    """
    if not isinstance(profile, dict):
        return ""
    status = status if isinstance(status, dict) else {}
    fingerprint = normalize_tls_fingerprint(
        status.get("desktop_tls_fingerprint") or profile.get("desktop_tls_fingerprint")
    )
    server_id = str(profile.get("server_id") or "").strip()
    host = str(profile.get("host") or "").strip()
    if not fingerprint or not server_id or not host:
        return ""
    try:
        port = int(profile.get("desktop_port") or 45950)
    except (TypeError, ValueError):
        return ""
    if not (1 <= port <= 65535):
        return ""
    save_client_record(
        server_id,
        name=str(profile.get("server_name") or host).strip() or host,
        ip=host,
        port=port,
        fingerprint=fingerprint,
    )
    return fingerprint
