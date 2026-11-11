from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ci = (ROOT / '.github/workflows/ci.yml').read_text(encoding='utf-8')
release = (ROOT / '.github/workflows/release.yml').read_text(encoding='utf-8')
gate = (ROOT / 'scripts/verify-macos-native-gate.sh').read_text(encoding='utf-8')
build = (ROOT / 'scripts/build-macos-streamer.sh').read_text(encoding='utf-8')

# Target machine profile: Apple Silicon M1+, macOS 26+ only.
assert 'macos-native-build:' in ci
assert '- macos-15' not in ci
assert '- macos-26' in ci
assert '- xcode-27' not in ci
assert 'scripts/verify-macos-native-gate.sh' in ci

# Tagged releases are blocked by the same native gate and packaged on the
# oldest supported M1 runner to avoid accidentally raising runtime requirements.
assert 'macos-native-gate:' in release
assert 'needs: [release-source-check, macos-native-gate]' in release
assert release.count('scripts/verify-macos-native-gate.sh') >= 2
assert '''macos-arm64:
    needs: [release-source-check, macos-native-gate]
    runs-on: macos-26''' in release

# One canonical compiler path, arm64 only, min deployment target 26.0.
assert 'MACOSX_DEPLOYMENT_TARGET=26.0' in build
assert '-target arm64-apple-macos26.0' in build
assert '-target arm64-apple-macos26.0' in build
assert 'MACOSX_DEPLOYMENT_TARGET=13.0' not in build
assert 'LSMinimumSystemVersion 26.0' in release

# Gate validates architecture, minos, linked frameworks and Python ABI.
for required in (
    "file build/libzmb_streamer.dylib | grep -F 'arm64'",
    "expected 26.0",
    "otool -L build/libzmb_streamer.dylib",
    "nm -gU build/libzmb_streamer.dylib",
    '_zmb_streamer_create',
    '_zmb_streamer_run',
    '_zmb_streamer_stop',
    '_zmb_streamer_destroy',
    '_zmb_corehid_input_open',
    '_zmb_corehid_input_key',
    '_zmb_corehid_input_pointer',
):
    assert required in gate, required

print('macOS 26+ Apple-Silicon native CI/release gate regression checks passed')
