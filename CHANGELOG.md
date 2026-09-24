# Changelog

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
