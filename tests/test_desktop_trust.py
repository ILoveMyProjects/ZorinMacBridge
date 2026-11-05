from __future__ import annotations

import tempfile
from pathlib import Path

import settings
import zmb_pairing_state as pairing
from zmb_pairing_state import DesktopTrustMismatch, ensure_desktop_trust, persist_desktop_trust, trusted_desktop_fingerprint

ROOT = Path(__file__).resolve().parents[1]


def fp(byte: str) -> str:
    return ':'.join([byte] * 32).upper()


def main() -> None:
    first = fp('AA')
    changed = fp('BB')
    profile = {
        'server_id': 'server-desktop-trust',
        'server_name': 'MacBookAir',
        'host': '192.168.1.181',
        'desktop_port': 45950,
    }

    with tempfile.TemporaryDirectory() as td:
        old_config = settings.CLIENT_CONFIG
        old_recover = pairing.recover_desktop_tls_fingerprint_for_profile
        try:
            settings.CLIENT_CONFIG = Path(td) / 'clients.json'

            # First verified System pairing establishes Desktop trust once.
            assert persist_desktop_trust(profile, {'desktop_tls_fingerprint': first}) == first
            assert trusted_desktop_fingerprint(profile['server_id']) == first

            # A later refresh with the same identity is accepted.
            assert ensure_desktop_trust(profile, {'desktop_tls_fingerprint': first}) == first

            # A changed TLS certificate must never silently replace pinned trust.
            try:
                persist_desktop_trust(profile, {'desktop_tls_fingerprint': changed})
            except DesktopTrustMismatch as exc:
                assert exc.expected == first
                assert exc.received == changed
            else:
                raise AssertionError('changed Desktop TLS identity was silently accepted')
            assert trusted_desktop_fingerprint(profile['server_id']) == first

            # Recovery for an older helper happens through the already-pinned SSH
            # profile and persists trust; it does not require a user-entered SHA.
            settings.CLIENT_CONFIG.unlink()
            calls = []
            def fake_recover(item):
                calls.append(dict(item))
                return first
            pairing.recover_desktop_tls_fingerprint_for_profile = fake_recover
            assert ensure_desktop_trust(profile) == first
            assert calls and calls[0]['server_id'] == profile['server_id']
            assert trusted_desktop_fingerprint(profile['server_id']) == first
        finally:
            pairing.recover_desktop_tls_fingerprint_for_profile = old_recover
            settings.CLIENT_CONFIG = old_config

    client = (ROOT / 'linux_client_gtk.py').read_text(encoding='utf-8')
    backend = (ROOT / 'zmb_admin_backend.py').read_text(encoding='utf-8')
    pairing_src = (ROOT / 'zmb_pairing_state.py').read_text(encoding='utf-8')
    ci = (ROOT / '.github/workflows/ci.yml').read_text(encoding='utf-8')
    release = (ROOT / '.github/workflows/release.yml').read_text(encoding='utf-8')

    assert "fp_label = Gtk.Label(label='Desktop trust'" in client
    assert 'self.fp_entry.set_editable(False)' in client
    assert 'self.fp_entry.set_can_focus(False)' in client
    assert "self.fp_entry.connect('changed'" not in client
    assert 'def _connection_params(self):' in client
    assert 'expected_fp = normalize_fp(trusted_desktop_fingerprint(sid))' in client
    assert 'Pair this Mac once in the System tab' in client
    assert 'No manual SHA entry is required or accepted.' in client
    assert 'recover_desktop_tls_fingerprint_for_profile' in backend
    assert 'StrictHostKeyChecking=yes' in backend
    assert 'class DesktopTrustMismatch' in pairing_src
    assert 'scripts/run-tests.sh' in ci
    assert 'scripts/run-tests.sh' in release
    assert (ROOT / 'VERSION').read_text(encoding='utf-8').strip() == '0.14.10'

    print('desktop trust tests: OK')


if __name__ == '__main__':
    main()
