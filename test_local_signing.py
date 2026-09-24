from __future__ import annotations

import plistlib
import tempfile
from pathlib import Path

from mac_local_signing import APP_NAME, BUNDLE_ID, IDENTITY_MARKER_KEY, LocalIdentity, bundle_identifier


def main() -> None:
    token = 'ab' * 32
    identity = LocalIdentity(token=token)
    requirement = identity.requirement
    expression = identity.requirement_expression
    assert requirement.startswith('designated => ')
    assert not expression.startswith('designated => ')
    assert f'identifier "{BUNDLE_ID}"' in requirement
    assert f'identifier "{BUNDLE_ID}"' in expression
    assert f'info[{IDENTITY_MARKER_KEY}] = "{token}"' in requirement
    assert 'certificate leaf' not in requirement
    assert 'cdhash' not in requirement
    assert 'certificate leaf' not in expression
    assert 'cdhash' not in expression

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
    transport = Path('scripts/sign-macos-transport.sh').read_text(encoding='utf-8')
    local_signing = Path('mac_local_signing.py').read_text(encoding='utf-8')

    # No certificate/keychain/GitHub-secret signing setup remains.
    for forbidden in (
        'MACOS_CERTIFICATE_P12_BASE64', 'MACOS_CERTIFICATE_PASSWORD', 'MACOS_SIGNING_IDENTITY',
        'security create-keychain', 'security import', 'security add-trusted-cert',
        'set-key-partition-list', 'find-identity', 'find-key -t private',
    ):
        assert forbidden not in transport
    assert 'scripts/sign-macos-transport.sh --self-test' in workflow
    assert 'macos-signing-preflight:' in workflow
    assert 'needs: [macos-signing-preflight]' in workflow

    # CI transport and the installed copy use explicit DRs rather than the
    # build-bound default ad-hoc cdhash requirement.
    assert '--sign -' in transport
    assert '--requirements "$REQ"' in transport
    assert 'stable per-Mac explicit DR shape' in transport
    assert 'info[ZMBLocalIdentity]' in transport
    assert "'--sign', '-', '--requirements', str(req)" in local_signing
    assert 'ZMBLocalIdentity' in local_signing
    assert 'TOKEN_PATH' in local_signing
    assert 'certificate leaf' not in local_signing
    assert 'KEYCHAIN_PATH' not in local_signing
    assert 'CERT_PATH' not in local_signing
    assert 'P12_PATH' not in local_signing

    # codesign -R accepts a single requirement expression, not a requirement set
    # prefixed by `designated =>`. Keep separate files/values for signing vs. verification.
    assert 'LOCAL_EXPR=' in transport
    assert '-R "$LOCAL_EXPR"' in transport
    assert 'REQ_EXPR=' in transport
    assert '-R "$REQ_EXPR"' in transport
    assert '-R "$LOCAL_REQ"' not in transport
    assert '-R "$REQ"' not in transport
    assert '_write_requirement_expression' in local_signing
    assert "'-R', str(req_expr)" in local_signing

    assert "--local-sign-app 'build/local-sign-test/ZorinMacBridge Server.app'" in workflow
    assert "codesign -d -r- 'build/local-sign-test/ZorinMacBridge Server.app'" in workflow
    assert 'security add-trusted-cert' not in workflow
    assert 'setup-stable-signing-linux.sh' not in workflow

    assert '--local-sign-app' in installer
    assert 'security add-trusted-cert' not in installer
    assert "stage_and_sign(source_app, stage_root)" in updater
    assert "bootstrap_installed_app_identity()" in server
    assert 'gh auth login' not in installer

    # Never mutate individual PyInstaller binaries after their enclosing framework
    # bundles have been signed. The top-level app is the only item re-signed by
    # our transport/local-identity layers; recursive verification remains enabled.
    assert "find \"$APP/Contents\" -type f" not in transport
    assert "signing nested native libraries" not in transport
    assert "--deep --options runtime" not in transport
    assert "preserving PyInstaller nested signatures" in transport
    assert "nested framework remained valid" in transport
    assert "contents.rglob('*')" not in local_signing
    assert "'--deep', '--options', 'runtime'" not in local_signing
    assert 'No certificate, keychain setup, GitHub login, Developer ID, or Linux signing setup is required.' in installer

    print('local stable-DR architecture tests: OK')


if __name__ == '__main__':
    main()
