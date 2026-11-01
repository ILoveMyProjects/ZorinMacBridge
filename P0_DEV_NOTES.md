# P0 source integration notes

This tree is a direct source-level integration of unattended administration into ZorinMacBridge 0.7.0.

There is no runtime patcher, `dpkg-divert`, APT post-hook, second launcher, or separate admin GUI.

## Linux

`linux_client_gtk.py` imports `SystemController` from `zmb_system_page.py` and adds a normal `System` page to the existing GTK stack. `zmb_admin_backend.py` owns device pairing, pinned SSH host keys, profiles, status parsing, and the restricted admin transport.

Paired devices are merged into the normal machine selector when mDNS is unavailable, marked `paired/offline`, so recovery remains possible when the desktop server itself is down.

The release `.deb` contains both P0 modules and depends on `openssh-client`.

## macOS

`mac_server_gui.py` exposes local **Enable / Repair** and **Disable P0** controls. `mac_admin_setup.py` requests macOS administrator authorization and executes the helper installer bundled under `native/macos/admin/`.

The root LaunchDaemon has no network socket. Remote administration is carried by macOS Remote Login (SSH). Per-device Ed25519 keys are stored with `restrict` plus a forced command, and the forced-command gateway only allows the ZorinMacBridge admin command set.

The actual ScreenCaptureKit desktop server remains in the logged-in user's GUI session.

## Release

GitHub Actions keeps producing the same artifact names:

- `ZorinMacBridge-Client_linux-amd64.deb`
- `ZorinMacBridge-Server_macOS-arm64.dmg`
- `ZorinMacBridge-Server_macOS-x86_64.dmg`

The existing in-app updater therefore does not need a second update mechanism.
