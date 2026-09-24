# Security Policy and Design

ZorinMacBridge is intentionally designed as a small LAN-first remote desktop bridge. It is an experimental project, not an independently audited enterprise remote-access product.

## Server lifecycle and persistence

The macOS server is intentionally manual.

The project does not install or implement:

- LaunchAgents or LaunchDaemons;
- login items;
- system services;
- hidden persistence;
- automatic startup;
- background update checks.

The server socket is created only after the user launches the macOS application and clicks **Start server**. Clicking **Stop server** or exiting the application stops the listener.

## Network model

- The remote-desktop server listens only on the literal IP selected by the user.
- Accepted ranges are RFC1918 IPv4 (`10/8`, `172.16/12`, `192.168/16`), loopback, IPv4 link-local, IPv6 ULA, IPv6 link-local, and IPv6 loopback where supported.
- Clients arriving from public source addresses are rejected.
- The client accepts only literal addresses from local/private ranges.
- The remote-desktop connection does not require DNS, a vendor account, cloud relay, NAT traversal, UPnP, or router port forwarding.

## Optional LAN discovery

When enabled in the server GUI, the running server advertises `_zorinmacbridge._tcp.local.` using mDNS/Bonjour.

Important properties:

- advertisement exists only while the server is running;
- traffic is local multicast discovery, not a vendor/cloud discovery service;
- the advertisement includes basic service identity, private address/port and application version;
- it does not include the session password, clipboard contents, files, or project data;
- discovery does not establish trust.

The Linux client uses discovery only to populate the private IP and port. TLS fingerprint verification remains separate.

If you do not want the Mac to advertise its presence on the LAN, disable **Advertise this Mac…** and enter the private IP manually.

## TLS and authentication

- Minimum TLS version: TLS 1.2.
- The server generates a local self-signed certificate under `~/.zorin-mac-bridge/`.
- The client requires SHA-256 certificate fingerprint pinning.
- mDNS discovery does not replace or disable fingerprint verification.
- The session password is transmitted only after TLS is established and the expected certificate fingerprint matches.
- The session password is not stored by default.

## File access

- The default remote share is `~/ZorinMac-Share`.
- Remote paths must be relative; absolute paths and `..` traversal are rejected.
- Resolved paths are checked to prevent escaping the share through symlinks.
- Symlinks are not listed or downloaded; Linux-side directory uploads skip symlinks.
- Uploads use a temporary `.part` file and are atomically renamed after the declared byte count is verified.

## Keyboard and clipboard

- Remote mouse and keyboard events are accepted only after authentication.
- macOS requires explicit Accessibility permission for input injection.
- Clipboard synchronization is text-only and limited to 2 MiB.
- Clipboard contents travel through the pinned TLS connection and are not sent to a third-party service.

## Manual update checks

The applications do not check for updates at startup or in the background.

When the user explicitly selects **Check for updates**, the application sends an HTTPS request to the public GitHub Releases API for `ILoveMyProjects/ZorinMacBridge`. If the user then explicitly approves installation, the updater downloads the matching release package and checksum file from GitHub, verifies SHA-256, and invokes the operating system's normal administrator-authorization mechanism before replacing the installed package/app.

The updater never runs at startup or on a timer. It does not install a daemon, service, LaunchAgent, login item, or background updater.

If this behavior is not desired, do not use the update-check command. The remote desktop and file transfer continue to work on an isolated LAN without Internet access.

The one-command installation/update scripts also contact GitHub because they download the latest public release package.

SHA-256 files protect against accidental corruption or mismatch between downloaded files. They are published in the same GitHub Release as the binaries; they are not a substitute for independent release signing. The current macOS build is ad-hoc signed rather than Developer-ID signed/notarized.

## Tray / menu-bar integration

Tray/menu-bar integration is user-interface functionality only. It does not create a background service. The tray icon exists only while the application process is running. Exiting the application terminates it.

## Deliberately absent features

The project does not implement:

- remote installation;
- microphone or camera access;
- Keychain access;
- whole-disk file browsing;
- automatic router port opening;
- UPnP;
- automatic bidirectional file synchronization;
- cloud relay or vendor-operated discovery;
- hidden autostart or persistence.

## Recommended deployment

Use the software only on a trusted LAN/VLAN. Do not expose TCP port `45950` directly to the public Internet.

Where possible, add a host/router/VLAN firewall rule allowing TCP `45950` only from the Linux workstation's private IP.

## Reporting a vulnerability

Please do not publish exploit details in a public issue before a fix can be prepared. If the repository owner enables GitHub private vulnerability reporting, use that channel. Otherwise open a minimal issue asking for a private contact path without including sensitive exploit details.
