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
    assert f'info[{IDENTITY_MARKER_KEY}] = "{token}"' in requirement
    assert 'certificate leaf' not in requirement
    assert 'cdhash' not in requirement

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
    local_signing = Path('mac_local_signing.py').read_text(encoding='utf-8')
    release_signing = Path('mac_release_signing.py').read_text(encoding='utf-8')

    # Stage C production releases use Developer ID + an Apple-issued profile.
    assert 'MACOS_DEVELOPER_ID_P12_BASE64' in workflow
    assert 'MACOS_DEVELOPER_ID_PROFILE_BASE64' in workflow
    assert 'scripts/sign-macos-release.sh' in workflow
    assert '--codesign-identity "$MACOS_CODESIGN_IDENTITY"' in workflow
    assert 'embedded.provisionprofile' in release_signing
    assert 'com.apple.developer.persistent-content-capture' in release_signing
    assert 'com.apple.developer.hid.virtual.device' in release_signing

    # The installed production bundle must never be converted back to ad-hoc.
    assert '--local-sign-app' not in installer
    assert 'stage_and_sign' not in updater
    assert 'app_has_release_identity' in local_signing
    assert 'Developer-ID/provisioned release' in local_signing
    assert '--verify-release-app' in server

    # Keep the certificate-free stable DR implementation only as a legacy/source
    # fallback; it must not acquire release credentials or restricted entitlements.
    for forbidden in (
        'MACOS_DEVELOPER_ID_P12_BASE64',
        'MACOS_DEVELOPER_ID_P12_PASSWORD',
        'com.apple.developer.persistent-content-capture',
        'com.apple.developer.hid.virtual.device',
    ):
        assert forbidden not in local_signing
    assert "'--sign', '-', '--requirements', str(req)" in local_signing
    assert 'ZMBLocalIdentity' in local_signing
    assert 'TOKEN_PATH' in local_signing

    print('legacy local-DR fallback + Stage C production signing tests: OK')


if __name__ == '__main__':
    main()
