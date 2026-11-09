from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
workflow = (ROOT / '.github/workflows/release.yml').read_text(encoding='utf-8')
requirements = (ROOT / 'mac_requirements.py').read_text(encoding='utf-8')
installer = (ROOT / 'install-macos.sh').read_text(encoding='utf-8')
updater = (ROOT / 'updates.py').read_text(encoding='utf-8')
source_gate = (ROOT / 'scripts/verify-release-source.sh').read_text(encoding='utf-8')

# No paid Apple secret may be mandatory.
for forbidden in (
    'Missing secret: MACOS_DEVELOPER_ID_P12_BASE64',
    'Missing secret: MACOS_DEVELOPER_ID_P12_PASSWORD',
    'Missing secret: MACOS_DEVELOPER_ID_PROFILE_BASE64',
):
    assert forbidden not in workflow

# Default CI path signs for transport without certificates and still builds DMG.
assert 'MACOS_RELEASE_SIGNING_MODE=adhoc' in workflow
assert "scripts/sign-macos-transport.sh 'dist/ZorinMacBridge Server.app'" in workflow
assert 'release/ZorinMacBridge-Server_macOS-arm64.dmg' in workflow

# Developer ID is optional, not removed.
assert 'MACOS_RELEASE_SIGNING_MODE=developer-id' in workflow
assert 'scripts/sign-macos-release.sh' in workflow

# Install/update converts free transport signing into one stable per-Mac ad-hoc DR.
assert '--local-sign-app "$STAGED"' in installer
assert 'stage_and_sign' in updater

# READY may depend on actual LoginWindow proof, but never on paid signing.
assert "'prelogin_release_identity'" not in requirements
assert 'paid Developer ID/provisioning' in source_gate

print('free release signing contract tests: OK')
