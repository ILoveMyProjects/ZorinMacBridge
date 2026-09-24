# Security Policy and Design

ZorinMac-Bridge is intentionally designed as a small, LAN-only remote desktop bridge. It is an experimental project, not an independently audited enterprise remote-access product.

## Network model

- The server listens only on the literal IP supplied through `--bind`.
- Accepted address ranges are RFC1918 IPv4 (`10/8`, `172.16/12`, `192.168/16`), loopback, IPv4 link-local, IPv6 ULA, IPv6 link-local, and IPv6 loopback.
- Clients arriving from public source addresses are rejected.
- The client accepts only literal addresses from those same local/private ranges.
- Hostnames are rejected, so connecting to the Mac does not require DNS.
- There is no cloud discovery, relay service, telemetry, vendor account, automatic updater, NAT traversal, or UPnP.

## TLS and authentication

- Minimum TLS version: TLS 1.2.
- The server generates a local self-signed certificate under `~/.zorin-mac-bridge/`.
- The client requires manual SHA-256 certificate fingerprint pinning.
- The session password is transmitted only after the TLS channel has been established and the certificate fingerprint has matched.
- The session password is not stored by default.

## File access

- The remote file service is restricted to `~/ZorinMac-Share` by default.
- Remote paths must be relative; absolute paths and `..` traversal are rejected.
- Resolved paths are checked again to prevent escaping the share through existing symlinks.
- Symlinks are not listed or downloaded; Linux-side directory uploads skip symlinks.
- Uploads are written to a temporary `.part` file, checked against the declared byte size, flushed with `fsync`, and atomically renamed into place.

## Keyboard and clipboard

- Remote mouse and keyboard events are accepted only after an authenticated desktop session is established.
- macOS requires explicit Accessibility permission for input injection.
- Clipboard synchronization is text-only and limited to 2 MiB.
- Clipboard contents travel through the same pinned TLS connection on the LAN and are not sent to a third-party service.

## Deliberately absent features

The project does not install or implement:

- autostart or a LaunchAgent;
- hidden execution or persistence mechanisms;
- remote installation;
- microphone or camera access;
- Keychain access;
- whole-disk file browsing;
- router port opening;
- UPnP;
- automatic bidirectional file synchronization.

## Recommended deployment

Use the software only on a trusted LAN/VLAN. Do not expose TCP port `45950` directly to the public Internet.

Where possible, add a host/router/VLAN firewall rule allowing TCP `45950` only from the Linux workstation's private IP.

## Reporting a vulnerability

Please do not publish exploit details in a public issue before a fix can be prepared. If the repository owner publishes a private security-reporting method in GitHub, use that channel. Otherwise open a minimal issue asking for a private contact path without including sensitive exploit details.
