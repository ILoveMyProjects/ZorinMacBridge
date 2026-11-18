# P0 source integration notes

This tree is a direct source-level integration of unattended administration into ZorinMacBridge 0.13.0.

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
- `ZorinMacBridge-Server_macOS-arm64.dmg` (Apple Silicon only)

The regular in-app updater remains available, while P0 adds a headless macOS update path through the same release/checksum/signature rules. A dedicated `com.ilovemyprojects.zorinmacbridge.update` LaunchDaemon runs every six hours and obeys the stored `AUTO_UPDATE=off|notify|install` mode. Remote `Check update` / `Update now` commands use the same restricted SSH gateway as other P0 administration.


## Discovery identity

P0 advertises `_zorinmacbridge-admin._tcp` from the system LaunchDaemon. The normal user-session desktop advertises `_zorinmacbridge._tcp`. Both carry the same persistent `server_id`; the client merges them and refreshes the current DHCP IP. The UUID is a correlation handle, not a trust secret: TLS certificate pinning and SSH host-key pinning remain authoritative. MAC addresses are not used as persistent machine identity.


## 0.9.0 feature layer

P0 remains the constrained recovery/authentication substrate. v0.9.0 builds P1/P2 on top of it: machine-state/power dashboard, recovery controls, and user-session Xcode/Simulator commands. The admin key remains a ForcedCommand key and the newly accepted arguments are limited to fixed toggles and validated Simulator UUIDs.


## 0.10.0 P3 layer

P3 adds a second SSH identity for interactive developer work. The P0 admin key remains restricted by `ForcedCommand` and the closed sudoers allow-list. The P3 shell key authenticates only as the normal paired macOS user, disables SSH forwarding, and receives no ZorinMacBridge sudo privileges. `Terminal` uses VTE/PTY for an interactive session; `Projects` stores only non-secret local metadata and executes quoted user-level Git/Xcode commands over that shell identity.


## 0.11.0 P4 layer

P4 adds safe project synchronization without widening P0. The Linux client runs `rsync` over the already pinned P3 normal-user SSH identity. Sync is Zorin-to-Mac only, never uses `--delete`, protects newer remote files with `--update`, and exact-excludes conflicts found by the reverse dry-run preview. The P0 ForcedCommand/sudoers surface is unchanged.


## 0.12.0 P5 layer

P5 changes only the desktop streaming path. It adds adaptive video profiles, latest-frame client buffering, live stream telemetry, video-only reconfiguration, and bounded native encoder/write queues. P0, sudoers, pairing keys, SSH ForcedCommand rules, Project Sync, and the file/administration trust boundaries are unchanged.


## 0.13.0 P6 layer

P6 extends the constrained recovery layer rather than widening it into a shell. `zmb-admin diagnose` emits structured issue records for launchd, heartbeat, system Bonjour, SSH availability, app/signature/backup state, LaunchAgent/desktop state and interrupted update state. `recover-all` may only perform a fixed safe repair sequence. It never changes FileVault/users, never auto-accepts a host key, and never starts an unverified app. The Linux System page also classifies transport failures that occur before the Mac helper can answer.
