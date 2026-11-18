#!/usr/bin/env python3
from __future__ import annotations

from zmb_admin_backend import normalize_tls_fingerprint, recover_desktop_tls_fingerprint_for_profile
from settings import client_record, save_client_record


class DesktopTrustMismatch(RuntimeError):
    def __init__(self, expected: str, received: str) -> None:
        self.expected = expected
        self.received = received
        super().__init__(f"Desktop TLS identity changed: expected {expected}, received {received}")


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
    existing = normalize_tls_fingerprint(client_record(server_id).get("fingerprint"))
    if existing and existing != fingerprint:
        # Once paired, Desktop trust is pinning, not TOFU-on-every-refresh. A
        # changed certificate must be surfaced and blocked instead of silently
        # replacing the identity learned during the original System pairing.
        raise DesktopTrustMismatch(existing, fingerprint)
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


def trusted_desktop_fingerprint(server_id: str) -> str:
    """Return only Desktop trust already persisted for this stable server id."""
    if not str(server_id or "").strip():
        return ""
    return normalize_tls_fingerprint(client_record(str(server_id)).get("fingerprint"))


def ensure_desktop_trust(profile: dict | None, status: dict | None = None) -> str:
    """Ensure Desktop TLS trust without ever asking the user for another SHA.

    Trust may come from P0 status/profile after a verified pairing. If an older
    Mac helper has not exposed it yet, recover the certificate fingerprint only
    through the already-pinned SSH profile. No discovery/TLS TOFU fallback is
    allowed here. Existing persisted trust always wins and is never silently
    replaced by a later status refresh.
    """
    if isinstance(profile, dict):
        server_id = str(profile.get("server_id") or "").strip()
        existing = trusted_desktop_fingerprint(server_id)
        if existing:
            candidate = normalize_tls_fingerprint(
                (status or {}).get("desktop_tls_fingerprint") if isinstance(status, dict) else profile.get("desktop_tls_fingerprint")
            )
            if candidate and candidate != existing:
                raise DesktopTrustMismatch(existing, candidate)
            return existing
    fingerprint = persist_desktop_trust(profile, status)
    if fingerprint:
        return fingerprint
    if not isinstance(profile, dict):
        return ""
    fingerprint = recover_desktop_tls_fingerprint_for_profile(profile)
    updated = {**profile, "desktop_tls_fingerprint": fingerprint}
    return persist_desktop_trust(updated, {"desktop_tls_fingerprint": fingerprint})
