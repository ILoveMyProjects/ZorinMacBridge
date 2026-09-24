from __future__ import annotations

import ipaddress
import socket
import time
from dataclasses import dataclass

SERVICE_TYPE = '_zorinmacbridge._tcp.local.'

V4_ALLOWED = tuple(ipaddress.ip_network(n) for n in (
    '10.0.0.0/8', '172.16.0.0/12', '192.168.0.0/16',
    '127.0.0.0/8', '169.254.0.0/16',
))


def _private_ipv4(text: str) -> bool:
    try:
        ip = ipaddress.ip_address(text)
    except ValueError:
        return False
    return ip.version == 4 and any(ip in network for network in V4_ALLOWED)


def _clean_name(value: str) -> str:
    cleaned = ''.join(ch if ch.isalnum() or ch in '-_' else '-' for ch in value.strip())
    return cleaned.strip('-_')[:48] or 'Mac'


@dataclass(frozen=True)
class DiscoveredServer:
    name: str
    ip: str
    port: int
    version: str = ''
    server_id: str = ''

    @property
    def label(self) -> str:
        suffix = f' · v{self.version}' if self.version else ''
        return f'{self.name} — {self.ip}:{self.port}{suffix}'


class LanAdvertiser:
    """Advertise the server over LAN mDNS only while this object is running."""

    def __init__(self, ip: str, port: int, version: str, server_id: str = '') -> None:
        self.ip = ip
        self.port = int(port)
        self.version = version
        self.server_id = server_id
        self._zc = None
        self._info = None

    def start(self) -> None:
        if self._zc is not None:
            return
        from zeroconf import IPVersion, ServiceInfo, Zeroconf

        if not _private_ipv4(self.ip):
            raise ValueError('mDNS advertisement requires a private IPv4 address.')
        host = _clean_name(socket.gethostname().split('.')[0])
        service_name = f'{host}.{SERVICE_TYPE}'
        properties = {
            b'app': b'ZorinMacBridge',
            b'platform': b'macOS',
            b'version': self.version.encode('utf-8', 'replace'),
            b'server_id': self.server_id.encode('ascii', 'ignore'),
        }
        info = ServiceInfo(
            SERVICE_TYPE,
            service_name,
            addresses=[socket.inet_aton(self.ip)],
            port=self.port,
            properties=properties,
            server=f'{host}.local.',
        )
        zc = Zeroconf(ip_version=IPVersion.V4Only)
        zc.register_service(info, allow_name_change=True)
        self._zc = zc
        self._info = info

    def stop(self) -> None:
        zc, info = self._zc, self._info
        self._zc = None
        self._info = None
        if zc is None:
            return
        try:
            if info is not None:
                zc.unregister_service(info)
        finally:
            zc.close()


def discover_servers(timeout: float = 2.5) -> list[DiscoveredServer]:
    """Discover running ZorinMacBridge servers on the local multicast domain."""
    from zeroconf import IPVersion, ServiceBrowser, ServiceListener, Zeroconf

    names: set[str] = set()

    class Listener(ServiceListener):
        def add_service(self, zc, type_, name):
            names.add(name)

        def update_service(self, zc, type_, name):
            names.add(name)

        def remove_service(self, zc, type_, name):
            names.discard(name)

    zc = Zeroconf(ip_version=IPVersion.V4Only)
    browser = ServiceBrowser(zc, SERVICE_TYPE, Listener())
    try:
        time.sleep(max(0.2, float(timeout)))
        found: dict[tuple[str, int], DiscoveredServer] = {}
        for service_name in sorted(names):
            info = zc.get_service_info(SERVICE_TYPE, service_name, timeout=1200)
            if info is None:
                continue
            properties = {
                (k.decode('utf-8', 'replace') if isinstance(k, bytes) else str(k)):
                (v.decode('utf-8', 'replace') if isinstance(v, bytes) else str(v))
                for k, v in (info.properties or {}).items()
            }
            display_name = service_name.removesuffix('.' + SERVICE_TYPE).rstrip('.')
            for address in info.parsed_addresses(IPVersion.V4Only):
                if not _private_ipv4(address):
                    continue
                item = DiscoveredServer(
                    name=display_name,
                    ip=address,
                    port=int(info.port),
                    version=properties.get('version', ''),
                    server_id=properties.get('server_id', ''),
                )
                found[(item.ip, item.port)] = item
        return sorted(found.values(), key=lambda item: (item.name.lower(), item.ip, item.port))
    finally:
        try:
            browser.cancel()
        except Exception:
            pass
        zc.close()
