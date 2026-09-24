# Changelog

## 0.4.3

- Fixed repeated Screen Recording prompts when a client opened the video channel.
- Removed the separate `zmb-macos-streamer` capture subprocess.
- ScreenCaptureKit and VideoToolbox now run from a Swift dynamic library loaded into the main `ZorinMacBridge Server.app` process.
- Screen Recording TCC authorization now belongs to the same app process that the user grants in System Settings.
- Added a frozen macOS release self-test that verifies the in-process streaming dylib can be loaded before a release is published.
- Kept video on its own TLS connection; only the capture/encoding execution location changed.

## 0.4.2

- Fixed macOS `Check for updates` failing with `CERTIFICATE_VERIFY_FAILED` in frozen builds.
- Update HTTPS now uses a bundled `certifi` CA trust store with certificate and hostname verification enabled.
- Added frozen-app updater TLS self-tests to Linux and both macOS release builds.
- Release builds now explicitly package `certifi`.

## 0.4.1

- Fixed the Linux release verification step that falsely reported PyAV as missing even after a successful PyInstaller build.
- Replaced brittle archive-name greps with an executable-level `--self-test-video` release test.
- The frozen Linux client must now successfully initialize Pillow/Tk, PyAV, its bundled FFmpeg libraries, and an H.264 decoder before a release can be published.
- PyInstaller archive inspection is retained only as failure diagnostics, not as the release pass/fail criterion.

## 0.4.0

- Replaced the JPEG screenshot loop with a dedicated H.264 video architecture.
- Added a native macOS ScreenCaptureKit capture helper.
- Added VideoToolbox real-time H.264 encoding on macOS.
- Added PyAV/FFmpeg H.264 decoding on Linux.
- Split control, video, and file traffic into separate authenticated TLS connections so video backpressure cannot block mouse/keyboard input.
- Increased the default video target to 30 fps, up to 2560 px wide, 8 Mbit/s H.264.
- Added a persistent random server ID to LAN discovery.
- Added remembered Mac identity/fingerprint records on Linux.
- Added Linux system-keyring password storage when available.
- Added salted PBKDF2 password-verifier storage on the Mac; plaintext server password is not persisted.
- Added optional per-user launch at login and optional auto-start server behavior for dedicated Mac mini workflows.
- Added direct buttons for macOS Screen Recording and Accessibility settings.
- Kept update checks manual only.
- Documented stable macOS permission requirements, Developer ID signing, and Apple's restricted Persistent Content Capture entitlement.

## 0.3.6

- Fixed recursive PyInstaller bundle verification for the Pillow/Tk helper.
