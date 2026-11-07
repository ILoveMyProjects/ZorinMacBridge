from __future__ import annotations

import ipaddress
import socket
import threading
import time
from dataclasses import dataclass, replace

DESKTOP_SERVICE_TYPE = '_zorinmacbridge._tcp.local.'
ADMIN_SERVICE_TYPE = '_zorinmacbridge-admin._tcp.local.'
# Backward-compatible name used by older tests/imports.
SERVICE_TYPE = DESKTOP_SERVICE_TYPE

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


def _clean_hostname(value: str) -> str:
    value = str(value or '').strip().rstrip('.')
    return value[:255]


@dataclass(frozen=True)
class DiscoveredServer:
    name: str
    ip: str
    port: int
    version: str = ''
    server_id: str = ''
    paired_only: bool = False
    hostname: str = ''
    desktop_available: bool = True
    admin_available: bool = False
    admin_port: int = 22

    @property
    def label(self) -> str:
        suffix = f' · v{self.version}' if self.version else ''
        if self.paired_only:
            suffix += ' · paired/offline'
        elif self.desktop_available and self.admin_available:
            suffix += ' · desktop + system'
        elif self.desktop_available:
            suffix += ' · desktop'
        elif self.admin_available:
            suffix += ' · system online'
        host = self.hostname.rstrip('.') if self.hostname else ''
        location = f'{host} [{self.ip}]' if host and host != self.ip else self.ip
        return f'{self.name} — {location}:{self.port}{suffix}'


def desktop_connect_strategy(*, desktop_available: bool, system_available: bool, paired_only: bool = False) -> str:
    """Choose the first recovery path for Desktop Connect.

    A live P0/System advertisement proves that the Mac is already awake.  In
    that state a missing Desktop advertisement means that the Aqua or
    LoginWindow desktop endpoint needs recovery; sending Wake-on-LAN is both
    misleading and ineffective.  Wake is reserved for a saved/offline Mac
    where no live System service is visible.
    """
    if desktop_available and not paired_only:
        return 'connect'
    if system_available and not paired_only:
        return 'recover-online'
    return 'wake'


class LanAdvertiser:
    """Advertise the desktop server over LAN mDNS only while this object is running."""

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
        service_name = f'{host}.{DESKTOP_SERVICE_TYPE}'
        properties = {
            b'app': b'ZorinMacBridge',
            b'platform': b'macOS',
            b'version': self.version.encode('utf-8', 'replace'),
            b'server_id': self.server_id.encode('ascii', 'ignore'),
            b'role': b'desktop',
        }
        info = ServiceInfo(
            DESKTOP_SERVICE_TYPE,
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


def _decode_properties(info) -> dict[str, str]:
    return {
        (k.decode('utf-8', 'replace') if isinstance(k, bytes) else str(k)):
        (v.decode('utf-8', 'replace') if isinstance(v, bytes) else str(v))
        for k, v in (info.properties or {}).items()
    }


def _merge_server(existing: DiscoveredServer | None, incoming: DiscoveredServer) -> DiscoveredServer:
    if existing is None:
        return incoming
    # Prefer the live desktop endpoint for the desktop port/version. The system
    # advertisement is still valuable because it survives a crashed user app.
    desktop = incoming if incoming.desktop_available else existing if existing.desktop_available else None
    newest = incoming
    return DiscoveredServer(
        name=(desktop.name if desktop else newest.name) or existing.name,
        ip=(desktop.ip if desktop else newest.ip) or existing.ip,
        port=(desktop.port if desktop else existing.port or newest.port),
        version=(desktop.version if desktop else newest.version or existing.version),
        server_id=incoming.server_id or existing.server_id,
        paired_only=False,
        hostname=incoming.hostname or existing.hostname,
        desktop_available=existing.desktop_available or incoming.desktop_available,
        admin_available=existing.admin_available or incoming.admin_available,
        admin_port=(incoming.admin_port if incoming.admin_available else existing.admin_port),
    )


def discover_servers(timeout: float = 3.5) -> list[DiscoveredServer]:
    """Discover desktop and P0 system services on the local multicast domain.

    The scan has a strict overall wall-clock budget. Resolution is performed only
    inside that budget so repeated/stale Bonjour names cannot create an arbitrarily
    long discovery worker.
    """
    from zeroconf import IPVersion, ServiceBrowser, ServiceListener, Zeroconf

    total = max(1.0, min(float(timeout), 8.0))
    deadline = time.monotonic() + total
    names: set[tuple[str, str]] = set()
    names_lock = threading.Lock()

    class Listener(ServiceListener):
        def add_service(self, zc, type_, name):
            with names_lock:
                names.add((type_, name))

        def update_service(self, zc, type_, name):
            with names_lock:
                names.add((type_, name))

        def remove_service(self, zc, type_, name):
            with names_lock:
                names.discard((type_, name))

    zc = Zeroconf(ip_version=IPVersion.V4Only)
    listener = Listener()
    browsers = [
        ServiceBrowser(zc, DESKTOP_SERVICE_TYPE, listener),
        ServiceBrowser(zc, ADMIN_SERVICE_TYPE, listener),
    ]
    try:
        # Let browse callbacks collect names, but reserve part of the budget for
        # resolution. This sleep is in a worker thread, never the GTK thread.
        browse_time = min(1.8, max(0.5, total * 0.55))
        time.sleep(browse_time)
        # Zeroconf invokes listener callbacks from its own threads. Iterate over a
        # locked snapshot so add/remove callbacks cannot race this resolver loop.
        with names_lock:
            names_snapshot = sorted(names)
        found: dict[str, DiscoveredServer] = {}
        for type_, service_name in names_snapshot:
            remaining = deadline - time.monotonic()
            if remaining <= 0.05:
                break
            per_lookup_ms = max(80, min(650, int(remaining * 1000)))
            info = zc.get_service_info(type_, service_name, timeout=per_lookup_ms)
            if info is None:
                continue
            properties = _decode_properties(info)
            display_name = service_name.removesuffix('.' + type_).rstrip('.')
            sid = properties.get('server_id', '').strip()
            hostname = _clean_hostname(getattr(info, 'server', '') or '')
            is_admin = type_ == ADMIN_SERVICE_TYPE
            for address in info.parsed_addresses(IPVersion.V4Only):
                if not _private_ipv4(address):
                    continue
                item = DiscoveredServer(
                    name=display_name,
                    ip=address,
                    port=(45950 if is_admin else int(info.port)),
                    version=properties.get('version', ''),
                    server_id=sid,
                    hostname=hostname,
                    desktop_available=not is_admin,
                    admin_available=is_admin,
                    admin_port=(int(info.port) if is_admin else 22),
                )
                # server_id is the stable machine identity. Service name is the
                # fallback for pre-P0/legacy servers that do not advertise one.
                key = f'id:{sid}' if sid else f'name:{display_name.lower()}:{hostname.lower()}'
                found[key] = _merge_server(found.get(key), item)
        return sorted(found.values(), key=lambda item: (item.name.lower(), item.hostname.lower(), item.ip, item.port))
    finally:
        for browser in browsers:
            try:
                browser.cancel()
            except Exception:
                pass
        zc.close()
