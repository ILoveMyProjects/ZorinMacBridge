from __future__ import annotations

import plistlib
import tempfile
from pathlib import Path

from mac_local_signing import APP_NAME, BUNDLE_ID, LocalIdentity, bundle_identifier


def main() -> None:
    identity = LocalIdentity(
        keychain=Path('/tmp/example.keychain-db'),
        keychain_password='not-used-in-test',
        cert_sha1='A1' * 20,
        cert_sha256='B2' * 32,
        common_name='ZorinMacBridge Local Stable Code Signing',
    )
    requirement = identity.requirement
    assert f'identifier "{BUNDLE_ID}"' in requirement
    assert f'certificate leaf = H"{identity.cert_sha1}"' in requirement

    with tempfile.TemporaryDirectory() as tmp:
        app = Path(tmp) / APP_NAME
        info = app / 'Contents' / 'Info.plist'
        info.parent.mkdir(parents=True)
        with info.open('wb') as fh:
            plistlib.dump({'CFBundleIdentifier': BUNDLE_ID}, fh)
        assert bundle_identifier(app) == BUNDLE_ID

    workflow = Path('.github/workflows/release.yml').read_text(encoding='utf-8')
    installer = Path('install-macos.sh').read_text(encoding='utf-8')
    updater = Path('updates.py').read_text(encoding='utf-8')
    server = Path('mac_server_gui.py').read_text(encoding='utf-8')

    # No GitHub signing secret setup remains. Release artifacts are transport-
    # signed, while installed copies get a persistent per-Mac identity.
    assert 'MACOS_CERTIFICATE_P12_BASE64' not in workflow
    assert 'MACOS_CERTIFICATE_PASSWORD' not in workflow
    assert 'MACOS_SIGNING_IDENTITY' not in workflow
    assert 'scripts/sign-macos-transport.sh' in workflow
    transport = Path('scripts/sign-macos-transport.sh').read_text(encoding='utf-8')
    local_signing = Path('mac_local_signing.py').read_text(encoding='utf-8')
    assert 'security list-keychains -d user -s "$KEYCHAIN"' in transport
    assert 'mapfile' not in transport
    assert 'readarray' not in transport
    assert 'security default-keychain -d user -s "$KEYCHAIN"' in transport
    assert 'security find-key -t private "$KEYCHAIN"' in transport
    assert '-t agg -f pkcs12' in transport
    assert '-t cert -f pkcs12' not in transport
    assert "_ensure_keychain_searchable(KEYCHAIN_PATH)" in local_signing
    assert 'security import "$TMP/identity.p12"' in transport
    assert ' -A ' in transport or ' -A \\' in transport
    assert '--sign "$IDENTITY"' in transport
    assert "'-A'" in local_signing
    assert "'-t', 'agg', '-f', 'pkcs12'" in local_signing
    assert "'-t', 'cert', '-f', 'pkcs12'" not in local_signing
    assert "'find-key', '-t', 'private', str(KEYCHAIN_PATH)" in local_signing
    assert "--local-sign-app 'build/local-sign-test/ZorinMacBridge Server.app'" in workflow
    assert 'security add-trusted-cert -d -r trustRoot -p codeSign' in workflow
    assert 'setup-stable-signing-linux.sh' not in workflow

    assert '--local-sign-app' in installer
    assert "stage_and_sign(source_app, stage_root)" in updater
    assert "bootstrap_installed_app_identity()" in server
    assert 'gh auth login' not in installer
    assert 'No GitHub login, GitHub secret, Developer ID, or Linux signing setup is required.' in installer

    print('local signing architecture tests: OK')


if __name__ == '__main__':
    main()
