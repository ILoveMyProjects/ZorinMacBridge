# Changelog

## 0.3.3

- Fixed a diagnostics bug where desktop-session failures could be hidden by the very short streaming socket timeout, causing the Linux client to report only `Server closed the connection`.
- Server-side error packets now temporarily use a safe write timeout so the real failure reaches the client before disconnect.
- Linux client preserves the last server-reported error and includes it in the final disconnect reason.
- macOS server now prefers the absolute system path `/usr/sbin/screencapture` instead of relying only on GUI-process `PATH`.
- Added first-frame diagnostics to the macOS log: CoreGraphics initialization, capture backend, first capture, and first frame sent.
- Added a macOS Screen Recording preflight before the server starts. If permission is missing, the app requests permission and tells the user to enable it and reopen the app instead of accepting desktop sessions that immediately fail.
- Added a non-blocking Accessibility permission warning for remote mouse/keyboard control.
- Screen-capture failures now include a direct macOS Privacy & Security remediation hint.

## 0.3.2

- Replaced the old "open GitHub Releases" update flow with a real user-initiated in-app updater.
- Linux client now downloads the latest `.deb`, verifies its SHA-256 checksum, requests administrator authorization through PolicyKit/`pkexec`, and installs the package.
- macOS server now downloads the correct Apple Silicon/Intel `.dmg`, verifies SHA-256, requests standard macOS administrator authorization, and replaces the app in `/Applications`.
- Both apps offer a restart after a successful update. Restarting the macOS GUI does **not** start the remote-desktop server; the user must still click **Start server** manually.
- Update downloads/installations never run automatically or in the background.
- Added clearer updater progress/error reporting to the existing logs/status UI.

## 0.3.1

- Added a **Logs** tab to the Linux client with timestamps, severity levels, copy, save, and clear actions.
- Client logs now show LAN discovery, TCP connect, TLS handshake, certificate fingerprint verification, authentication, session disconnect reason, update checks, and file-transfer failures.
- Passwords are never written to the client log.
- The macOS server now forwards session exceptions (for example screen-capture failures) to the connected client before closing the connection when possible.
- macOS server GUI logs now include accepted connections, TLS completion, authentication role, and desktop-session lifecycle.
- Fixed README installer URLs to use the repository's `master` branch.


## 0.3.0 - 2026-09-24

Desktop UX and controlled-discovery update:

- added dedicated Client and Server application icons;
- added one-command installers for Zorin/Ubuntu and macOS;
- macOS installer detects Apple Silicon vs Intel automatically and never starts the server automatically;
- added manual-only GitHub release update checks with no background polling;
- added local mDNS/Bonjour server discovery while the manually started server is running;
- discovery fills private IP/port but does not bypass TLS fingerprint verification;
- added best-effort system tray / macOS menu-bar controls;
- kept the macOS server explicitly manual with no LaunchAgent, daemon, login item, or service;
- updated release packaging so app icons and new runtime dependencies are bundled.

## 0.2.0 - 2026-09-24

End-user distribution update:

- added a macOS server GUI so the server can be launched as an app;
- added GitHub Actions release builds for Apple Silicon and Intel macOS `.dmg` installers;
- added a Zorin/Ubuntu x86_64 `.deb` client package and standalone Linux binary;
- added permanent `releases/latest/download/...` links to the README;
- moved `git clone` instructions into the developer/source-build section;
- kept the runtime LAN-only design unchanged.

## 0.1.0 - 2026-09-24

Initial public MVP:

- Linux → macOS remote desktop;
- mouse, keyboard, and scrolling;
- left Ctrl → macOS Command shortcut mapping;
- real macOS Control through right Ctrl;
- bidirectional text clipboard;
- restricted directory/file transfer through `~/ZorinMac-Share`;
- TLS with manual SHA-256 fingerprint pinning;
- literal private-IP-only connection policy;
- protocol, path-security, and transfer round-trip tests.
