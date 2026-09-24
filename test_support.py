from discovery import _private_ipv4, DiscoveredServer
from updates import _version_tuple


def main() -> None:
    assert _private_ipv4('192.168.1.50')
    assert _private_ipv4('10.0.0.2')
    assert _private_ipv4('172.16.4.10')
    assert not _private_ipv4('8.8.8.8')
    assert not _private_ipv4('github.com')
    assert _version_tuple('v0.3.0') > _version_tuple('0.2.9')
    item = DiscoveredServer('Dev-Mac', '192.168.1.50', 45950, '0.3.0')
    assert '192.168.1.50:45950' in item.label
    print('support tests: OK')


if __name__ == '__main__':
    main()
