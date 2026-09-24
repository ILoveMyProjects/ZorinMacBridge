from discovery import _private_ipv4, DiscoveredServer
from settings import PasswordVerifier
from updates import _version_tuple


def main() -> None:
    assert _private_ipv4('192.168.1.50')
    assert _private_ipv4('10.0.0.2')
    assert _private_ipv4('172.16.4.10')
    assert not _private_ipv4('8.8.8.8')
    assert not _private_ipv4('github.com')
    assert _version_tuple('v0.4.0') > _version_tuple('0.3.9')
    item = DiscoveredServer('Dev-Mac', '192.168.1.50', 45950, '0.4.0', 'server-123')
    assert '192.168.1.50:45950' in item.label
    assert item.server_id == 'server-123'
    verifier = PasswordVerifier.from_password('correct horse battery staple', iterations=10_000)
    assert verifier.verify('correct horse battery staple')
    assert not verifier.verify('wrong')
    print('support tests: OK')


if __name__ == '__main__':
    main()
