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

# Normal Aqua READY remains available on the free path, but reboot-to-LoginWindow
# READY now explicitly requires the Apple-managed Developer ID/provisioning path.
assert "'prelogin_release_identity'" in requirements
assert "'prelogin_release_identity_ready'" in requirements
assert 'pre-login READY requires Apple-managed Developer ID/provisioning' in source_gate
assert 'free Aqua-only signing path' in source_gate

print('free Aqua signing + managed pre-login gate regression checks: OK')
