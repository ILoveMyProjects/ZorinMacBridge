from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ci = (ROOT / '.github/workflows/ci.yml').read_text(encoding='utf-8')
release = (ROOT / '.github/workflows/release.yml').read_text(encoding='utf-8')
gate = (ROOT / 'scripts/verify-macos-native-gate.sh').read_text(encoding='utf-8')
build = (ROOT / 'scripts/build-macos-streamer.sh').read_text(encoding='utf-8')

# Push/PR CI must compile the native macOS library on both the stable macOS 26
# Apple-Silicon image and the Xcode 27 image used to catch newer Swift/SDK failures.
assert 'macos-native-build:' in ci
assert '- macos-26' in ci
assert '- xcode-27' in ci
assert 'scripts/verify-macos-native-gate.sh' in ci

# A tagged release must be blocked by the same native gate before packaging.
assert 'macos-native-gate:' in release
assert 'needs: [release-source-check, macos-native-gate]' in release
assert release.count('scripts/verify-macos-native-gate.sh') >= 2

# There must be one canonical compiler path instead of a second copy of clang/swiftc
# flags embedded in the release workflow.
assert 'MACOSX_DEPLOYMENT_TARGET=13.0 swiftc' not in release
assert 'MACOSX_DEPLOYMENT_TARGET=13.0 clang' not in release
assert 'scripts/build-macos-streamer.sh' in gate
assert 'MACOSX_DEPLOYMENT_TARGET=13.0 swiftc' in build
assert 'MACOSX_DEPLOYMENT_TARGET=13.0 clang' in build

# The gate must validate architecture, linked frameworks and the ABI Python loads.
for required in (
    "file build/libzmb_streamer.dylib | grep -F 'arm64'",
    "otool -L build/libzmb_streamer.dylib",
    "nm -gU build/libzmb_streamer.dylib",
    '_zmb_streamer_create',
    '_zmb_streamer_run',
    '_zmb_streamer_stop',
    '_zmb_streamer_destroy',
    '_zmb_login_input_open',
    '_zmb_login_input_key',
    '_zmb_login_input_pointer',
    '_zmb_login_input_scroll',
):
    assert required in gate, required

print('macOS native CI/release gate regression checks passed')
