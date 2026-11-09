from __future__ import annotations

import datetime as dt
import hashlib
from pathlib import Path

from mac_release_signing import (
    BUNDLE_ID,
    ReleaseSigningError,
    REQUIRED_RESTRICTED_ENTITLEMENTS,
    release_entitlements_from_profile,
    validate_profile_payload,
)

ROOT = Path(__file__).resolve().parents[1]

TEAM = 'A1B2C3D4E5'
CERT = b'fake-developer-id-certificate-der'
CERT_SHA1 = hashlib.sha1(CERT).hexdigest().upper()


def payload(**overrides):
    base = {
        'Name': 'ZorinMacBridge Developer ID',
        'ExpirationDate': dt.datetime.now(dt.timezone.utc) + dt.timedelta(days=180),
        'ProvisionsAllDevices': True,
        'DeveloperCertificates': [CERT],
        'TeamIdentifier': [TEAM],
        'Entitlements': {
            'com.apple.application-identifier': f'{TEAM}.{BUNDLE_ID}',
            'com.apple.developer.team-identifier': TEAM,
            'com.apple.developer.persistent-content-capture': True,
            'com.apple.developer.hid.virtual.device': True,
        },
    }
    base.update(overrides)
    return base


info = validate_profile_payload(payload(), certificate_sha1=CERT_SHA1)
assert info.team_identifier == TEAM
assert info.application_identifier == f'{TEAM}.{BUNDLE_ID}'
ents = release_entitlements_from_profile(payload())
assert ents['com.apple.application-identifier'] == f'{TEAM}.{BUNDLE_ID}'
for key in REQUIRED_RESTRICTED_ENTITLEMENTS:
    assert ents[key] is True

bad = payload()
bad['Entitlements'] = dict(bad['Entitlements'])
bad['Entitlements']['com.apple.developer.persistent-content-capture'] = False
try:
    validate_profile_payload(bad)
    raise AssertionError('missing Persistent Content Capture grant was accepted')
except ReleaseSigningError:
    pass

bad = payload()
bad['Entitlements'] = dict(bad['Entitlements'])
bad['Entitlements'].pop('com.apple.developer.hid.virtual.device')
try:
    validate_profile_payload(bad)
    raise AssertionError('missing Virtual HID grant was accepted')
except ReleaseSigningError:
    pass

try:
    validate_profile_payload(payload(ProvisionsAllDevices=False, ProvisionedDevices=['TEST-MAC']))
    raise AssertionError('device-limited development profile was accepted')
except ReleaseSigningError:
    pass

try:
    validate_profile_payload(payload(ExpirationDate=dt.datetime.now(dt.timezone.utc) - dt.timedelta(seconds=1)))
    raise AssertionError('expired profile was accepted')
except ReleaseSigningError:
    pass

try:
    validate_profile_payload(payload(), certificate_sha1='00' * 20)
    raise AssertionError('signing certificate not present in profile was accepted')
except ReleaseSigningError:
    pass

workflow = (ROOT / '.github/workflows/release.yml').read_text(encoding='utf-8')
sign_script = (ROOT / 'scripts/sign-macos-release.sh').read_text(encoding='utf-8')
installer = (ROOT / 'install-macos.sh').read_text(encoding='utf-8')
updater = (ROOT / 'updates.py').read_text(encoding='utf-8')
local_signing = (ROOT / 'mac_local_signing.py').read_text(encoding='utf-8')
gui = (ROOT / 'mac_server_gui.py').read_text(encoding='utf-8')
manifest = (ROOT / 'native/macos/signing/ZorinMacBridge.release-entitlements.plist').read_text(encoding='utf-8')

# Developer ID/profile validation remains available, but is optional. The
# workflow must have a fully free ad-hoc branch when no Apple secrets exist.
for marker in (
    'MACOS_RELEASE_SIGNING_MODE=adhoc',
    'MACOS_RELEASE_SIGNING_MODE=developer-id',
    'Apple Developer credentials not configured; using free ad-hoc transport signing.',
    "scripts/sign-macos-transport.sh 'dist/ZorinMacBridge Server.app'",
    'scripts/sign-macos-release.sh',
):
    assert marker in workflow, marker
for forbidden in (
    'Missing secret: MACOS_DEVELOPER_ID_P12_BASE64',
    'Missing secret: MACOS_DEVELOPER_ID_P12_PASSWORD',
    'Missing secret: MACOS_DEVELOPER_ID_PROFILE_BASE64',
):
    assert forbidden not in workflow, forbidden

# Optional publisher signing still embeds/validates the restricted capabilities
# when a maintainer deliberately configures the credentials.
assert 'Contents/embedded.provisionprofile' in sign_script
assert '--options runtime' in sign_script
assert '--timestamp' in sign_script
assert '--entitlements "$ENTITLEMENTS"' in sign_script
assert 'codesign --force' in sign_script
assert 'codesign --force --deep' not in sign_script
for key in REQUIRED_RESTRICTED_ENTITLEMENTS:
    assert key in manifest

# Default install/update must work without Developer ID and apply a stable local
# ad-hoc identity. Optional Developer ID builds are preserved unchanged.
assert '--local-sign-app "$STAGED"' in installer
assert 'stage_and_sign' in updater
assert 'MACOS_RELEASE_SIGNING_MODE=developer-id' in workflow
assert 'app_has_release_identity' in local_signing
assert 'Optional Developer-ID releases keep their publisher signature.' in local_signing
assert '--verify-release-app' in gui
assert 'release_identity_summary' in gui

print('optional Developer ID + mandatory free signing regression checks passed')
