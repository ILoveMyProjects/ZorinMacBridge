from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
workflow = (ROOT / '.github/workflows/release.yml').read_text(encoding='utf-8')
requirements = (ROOT / 'mac_requirements.py').read_text(encoding='utf-8')
installer = (ROOT / 'install-macos.sh').read_text(encoding='utf-8')
updater = (ROOT / 'updates.py').read_text(encoding='utf-8')
source_gate = (ROOT / 'scripts/verify-release-source.sh').read_text(encoding='utf-8')

# Managed Apple signing is optional and may never make the whole tagged release unavailable.
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
assert 'continue-on-error: true' in workflow
assert 'steps.managed_signing.outcome' in workflow
assert 'falling back to the working Aqua/ad-hoc macOS artifact' in workflow
assert 'ZorinMacBridge-Server_macOS-arm64-signing.txt' in workflow

# Install/update converts free transport signing into one stable per-Mac ad-hoc DR.
assert '--local-sign-app "$STAGED"' in installer
assert 'stage_and_sign' in updater

# Normal Aqua READY remains available on the free path. Reboot-to-LoginWindow
# input readiness is provided independently by the bundled driver-backed virtual HID.
assert "'prelogin_virtual_hid'" in requirements
assert "'virtual_hid_input_ready'" in requirements
assert 'pre-login READY requires driver-backed virtual HID input plus current capture proof' in source_gate
assert 'resilient Aqua fallback' in source_gate
assert "VIRTUAL_HID_VERSION='8.8.0'" in workflow
assert 'Karabiner-DriverKit-VirtualHIDDevice-' in workflow

print('resilient Aqua release + Mac virtual HID pre-login regression checks: OK')
