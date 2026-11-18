from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SERVER = (ROOT / 'mac_server.py').read_text(encoding='utf-8')
CLIENT = (ROOT / 'linux_client.py').read_text(encoding='utf-8')
RUNTIME = (ROOT / 'zmb_client_runtime.py').read_text(encoding='utf-8')
P0 = (ROOT / 'native/macos/admin/zmb-system-daemon').read_text(encoding='utf-8')
WORKFLOW = (ROOT / '.github/workflows/release.yml').read_text(encoding='utf-8')

assert "'runtime_version': runtime_version()" in SERVER
assert "resource_path('VERSION')" in SERVER
assert "client.server_runtime_version = ''" in RUNTIME
assert 'Pre-login runtime version skew:' in CLIENT
assert 'runtime-version=PASS' in CLIENT
assert 'runtime-version=FAIL' in CLIENT
assert 'installed_app_version()' in P0
assert '"version=$version"' in P0
assert 'linux-amd64:\n    needs: [release-source-check, macos-native-gate]' in WORKFLOW

print('runtime version skew guard regression tests: OK')
