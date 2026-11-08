from pathlib import Path
from discovery import _private_ipv4, DiscoveredServer, _merge_server
from settings import PasswordVerifier
from types import SimpleNamespace
from mac_server import video_options_from_auth
from updates import _version_tuple


def main() -> None:
    assert _private_ipv4('192.168.1.50')
    assert _private_ipv4('10.0.0.2')
    assert _private_ipv4('172.16.4.10')
    assert not _private_ipv4('8.8.8.8')
    assert not _private_ipv4('github.com')
    assert _version_tuple('v0.4.0') > _version_tuple('0.3.9')
    item = DiscoveredServer('Dev-Mac', '192.168.1.50', 45950, '0.4.0', 'server-123', hostname='dev-mac.local', desktop_available=True)
    assert 'dev-mac.local [192.168.1.50]:45950' in item.label
    assert item.server_id == 'server-123'
    admin = DiscoveredServer('Dev-Mac', '192.168.1.51', 45950, server_id='server-123', hostname='dev-mac.local', desktop_available=False, admin_available=True, admin_port=22)
    merged = _merge_server(item, admin)
    assert merged.server_id == 'server-123'
    assert merged.desktop_available and merged.admin_available
    assert merged.hostname == 'dev-mac.local'
    verifier = PasswordVerifier.from_password('correct horse battery staple', iterations=10_000)
    assert verifier.verify('correct horse battery staple')
    assert not verifier.verify('wrong')
    defaults = SimpleNamespace(fps=30.0, max_width=1920, bitrate=8_000_000)
    fps, width, bitrate, quality, display_id, target_width, target_height = video_options_from_auth(
        {'quality': 'Ultra', 'display_id': 1234, 'video': {'fps': 999, 'max_width': 99999, 'bitrate': 999_000_000, 'target_width': 3840, 'target_height': 2160}},
        defaults,
    )
    assert (fps, width, bitrate, quality, display_id, target_width, target_height) == (60.0, 3840, 40_000_000, 'Ultra', 1234, 3840, 2160)
    rejected = video_options_from_auth({'video': {'target_width': 3000, 'target_height': 2000}}, defaults)
    assert rejected[-2:] == (0, 0)
    server_source = Path('mac_server.py').read_text(encoding='utf-8')
    assert 'The server will not request permission from a remote Connect.' in server_source
    assert 'native_messages' in server_source
    assert 'CGRequestScreenCaptureAccess' in server_source
    assert 'CGRequestPostEventAccess' in server_source
    assert 'permission_api_self_test' in server_source
    assert 'video_options_from_auth' in server_source
    client_source = Path('linux_client.py').read_text(encoding='utf-8')
    assert 'A read timeout is therefore an idle tick' in client_source
    assert "except (socket.timeout, ssl.SSLWantReadError):" in client_source
    assert 'VIDEO_QUALITY_PRESETS' in client_source
    workflow = Path('.github/workflows/release.yml').read_text(encoding='utf-8')
    assert 'MACOS_RELEASE_SIGNING_MODE=adhoc' in workflow
    assert 'MACOS_RELEASE_SIGNING_MODE=developer-id' in workflow
    assert "scripts/sign-macos-transport.sh 'dist/ZorinMacBridge Server.app'" in workflow
    assert 'scripts/sign-macos-release.sh' in workflow
    assert 'Missing secret: MACOS_DEVELOPER_ID_P12_BASE64' not in workflow
    print('support tests: OK')


if __name__ == '__main__':
    main()
