from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
streamer = (ROOT / 'scripts/build-macos-streamer.sh').read_text(encoding='utf-8')
probe_build = (ROOT / 'scripts/build-macos-capability-probe.sh').read_text(encoding='utf-8')
install = (ROOT / 'install-macos.sh').read_text(encoding='utf-8')
admin_install = (ROOT / 'native/macos/admin/install-admin.sh').read_text(encoding='utf-8')
setup = (ROOT / 'scripts/setup-source-macos.sh').read_text(encoding='utf-8')
release = (ROOT / '.github/workflows/release.yml').read_text(encoding='utf-8')
ci = (ROOT / '.github/workflows/ci.yml').read_text(encoding='utf-8')
swift = (ROOT / 'native/macos/ZMBStreamerLib.swift').read_text(encoding='utf-8')

for script in (install, admin_install, setup):
    assert 'Apple Silicon (arm64)' in script
    assert 'requires macOS 26.0 or later' in script
    assert 'MACOS_MAJOR < 26' in script

assert 'MACOSX_DEPLOYMENT_TARGET=26.0' in streamer
assert 'arm64-apple-macos26.0' in streamer
assert 'MACOSX_DEPLOYMENT_TARGET=26.0' in probe_build
assert 'arm64-apple-macos26.0' in probe_build
assert 'LSMinimumSystemVersion 26.0' in release
assert 'runs-on: macos-26' in release
assert '- macos-26' in ci
assert '- macos-15' not in ci
assert 'runs-on: macos-15' not in release

# No production streamer fallback for unsupported historical systems remains.
assert '@available(macOS 13.0' not in swift
assert '#available(macOS 13.0' not in swift
assert '#available(macOS 14.0' not in swift
assert 'macos<14' not in swift

# Tagged GitHub releases build installable packages for both machines.
assert 'dpkg-deb --root-owner-group --build' in release
assert 'ZorinMacBridge-Client_linux-amd64.deb' in release
assert "hdiutil create -volname 'ZorinMacBridge Server'" in release
assert 'ZorinMacBridge-Server_macOS-arm64.dmg' in release

print('macOS 26+ / Apple Silicon target contract tests: OK')
