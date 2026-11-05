from __future__ import annotations

import tempfile
from pathlib import Path

import settings
from settings import client_record
from zmb_admin_backend import normalize_tls_fingerprint
from zmb_pairing_state import persist_desktop_trust

ROOT = Path(__file__).resolve().parent


def main() -> None:
    raw = 'aa:' + ':'.join(['bb'] * 31)
    canonical = normalize_tls_fingerprint(raw)
    assert canonical == raw.upper()
    assert normalize_tls_fingerprint('missing') == ''
    assert normalize_tls_fingerprint('SHA256:not-a-cert-fingerprint') == ''

    with tempfile.TemporaryDirectory() as td:
        old = settings.CLIENT_CONFIG
        try:
            settings.CLIENT_CONFIG = Path(td) / 'clients.json'
            profile = {
                'server_id': 'server-12345678',
                'server_name': 'MacBookAir',
                'host': '192.168.1.181',
                'desktop_port': 45950,
            }
            status = {'desktop_tls_fingerprint': canonical}
            saved = persist_desktop_trust(profile, status)
            assert saved == canonical
            rec = client_record('server-12345678')
            assert rec['fingerprint'] == canonical
            assert rec['ip'] == '192.168.1.181'
            assert rec['port'] == 45950

            # Untrusted/invalid metadata must never establish Desktop trust.
            settings.CLIENT_CONFIG.unlink()
            assert persist_desktop_trust(profile, {'desktop_tls_fingerprint': 'missing'}) == ''
            assert client_record('server-12345678') == {}
        finally:
            settings.CLIENT_CONFIG = old

    admin = (ROOT / 'native/macos/admin/zmb-admin').read_text(encoding='utf-8')
    backend = (ROOT / 'zmb_admin_backend.py').read_text(encoding='utf-8')
    pairing = (ROOT / 'zmb_pairing_state.py').read_text(encoding='utf-8')
    system = (ROOT / 'zmb_system_page.py').read_text(encoding='utf-8')
    client = (ROOT / 'linux_client_gtk.py').read_text(encoding='utf-8')
    release = (ROOT / '.github/workflows/release.yml').read_text(encoding='utf-8')
    ci = (ROOT / '.github/workflows/ci.yml').read_text(encoding='utf-8')

    assert 'desktop_tls_fingerprint()' in admin
    assert "printf 'desktop_tls_fingerprint=%s\\n' \"$desktop_tls_fp\"" in admin
    assert 'server-cert.pem' in admin
    assert 'openssl x509' in admin
    assert 'candidate["desktop_tls_fingerprint"] = desktop_tls_fingerprint' in backend
    assert 'save_client_record(' in pairing
    assert 'never accepted here' in pairing
    assert 'self.window.pairing_state_changed(profile, include_system=True)' in system
    assert 'self.window.pairing_state_changed(self._profile(), status=data, include_system=False)' in system
    assert 'def pairing_state_changed(' in client
    assert 'self.terminal_controller.selection_changed()' in client
    assert 'self.projects_controller.refresh_projects()' in client
    assert 'self.xcode_controller.selection_changed()' in client
    assert 'Mac paired ✓ · Desktop is not connected. Connect Desktop to use Files.' in client
    assert 'self._set_tls_fingerprint(fingerprint)' in client
    assert 'zmb_pairing_state.py' in release
    assert 'test_pairing_sync.py' in release
    assert 'test_pairing_sync.py' in ci
    assert (ROOT / 'VERSION').read_text(encoding='utf-8').strip() == '0.14.2'

    print('pairing state sync tests: OK')


if __name__ == '__main__':
    main()
