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

    # 0.15.19: the default release path is free/ad-hoc. Developer ID support is
    # retained only as an optional branch when all maintainer secrets exist.
    assert 'MACOS_RELEASE_SIGNING_MODE=adhoc' in workflow
    assert 'MACOS_RELEASE_SIGNING_MODE=developer-id' in workflow
    assert "scripts/sign-macos-transport.sh 'dist/ZorinMacBridge Server.app'" in workflow
    assert 'Missing secret: MACOS_DEVELOPER_ID_P12_BASE64' not in workflow
    assert 'Missing secret: MACOS_DEVELOPER_ID_PROFILE_BASE64' not in workflow
    assert 'scripts/sign-macos-release.sh' in workflow  # optional paid branch only
    assert 'embedded.provisionprofile' in release_signing
    assert 'com.apple.developer.persistent-content-capture' in release_signing
    assert 'com.apple.developer.hid.virtual.device' in release_signing

    # Free installs/updates are re-signed locally with one stable per-Mac DR.
    assert '--local-sign-app "$STAGED"' in installer
    assert 'stage_and_sign' in updater
    assert 'free stable per-Mac ad-hoc identity' in updater
    assert 'app_has_release_identity' in local_signing  # preserves optional Developer ID builds
    assert 'Optional Developer-ID releases keep their publisher signature.' in local_signing
    assert '--verify-release-app' in server  # optional verification CLI remains available

    # Local signing itself must never acquire paid credentials or restricted
    # entitlements. It remains certificate-free.
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

    print('free local-DR release path + optional Developer ID tests: OK')


if __name__ == '__main__':
    main()
