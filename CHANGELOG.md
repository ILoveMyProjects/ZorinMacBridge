# Changelog

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
