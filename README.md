# ZorinMacBridge

<p align="center">
  <img src="assets/client.png" width="190" alt="ZorinMacBridge Client icon">
  &nbsp;&nbsp;&nbsp;&nbsp;
  <img src="assets/server.png" width="190" alt="ZorinMacBridge Server icon">
</p>

A LAN-first remote desktop bridge for a **Zorin OS / Linux workstation → macOS development machine** workflow.

The intended setup is simple: keep Linux as the main machine for web/app development, and use the Mac only for Xcode, iOS Simulator, signing, builds, or other Apple-only tooling.

The remote session does **not** require a vendor cloud, relay, account, telemetry service, or Internet connection. Optional LAN discovery uses local mDNS/Bonjour only. GitHub is contacted for installation/update checks; P0 can optionally check every 6 hours and, if you choose **Install automatically**, install verified Mac releases without requiring a local monitor.

> **Status:** experimental. This project is still under active development and has not received an independent security audit.

## Native Zorin/GNOME client UI

v0.5 moves the Linux client to **GTK4 + libadwaita**. On Zorin OS / Ubuntu, controls, spacing, dialogs, switches and file browsing now use the desktop's native GTK/libadwaita theme instead of the old raw Tkinter interface.

The `.deb` declares the required Ubuntu/Zorin runtime packages, so the one-command installer still handles everything through `apt`. The client uses system symbolic icons and native file dialogs.


## Install

### Zorin OS / Ubuntu — Client

Copy and paste one command:

```bash
curl -fsSL https://raw.githubusercontent.com/ILoveMyProjects/ZorinMacBridge/master/install-linux.sh | bash
```

The installer downloads the latest `.deb`, verifies its published SHA-256 checksum, installs it with `apt`, and adds **ZorinMacBridge Client** to the application menu.

### macOS — Server

> **Apple Silicon only (arm64). Intel Macs are intentionally unsupported.**
>
> The Apple computer runs **macOS**. iOS is the target platform you build for in Xcode.

Copy and paste one command on the Mac:

```bash
curl -fsSL https://raw.githubusercontent.com/ILoveMyProjects/ZorinMacBridge/master/install-macos.sh | bash
```

The macOS server supports **Apple Silicon (arm64) only**. The installer verifies the Mac architecture, downloads the arm64 `.dmg`, verifies SHA-256, and installs **ZorinMacBridge Server** into `/Applications`. Intel Macs are not supported.

Starting with v0.6.7, installation uses a **stable explicit macOS designated requirement (DR)** instead of a certificate/keychain identity. No Developer ID, local certificate, GitHub signing secret, or Linux signing setup is required for this private/internal workflow. Each Mac creates one local random identity marker, injects it into the staged app, and re-signs the app ad-hoc with the same explicit DR on every update. GitHub Actions runs a real `codesign` preflight that proves this DR shape before the long macOS builds start.

## Remote desktop architecture

The old prototype captured individual JPEG screenshots and sent them on the same connection used for mouse and keyboard input. That design could eventually fill the TLS socket buffer and block/disconnect the whole remote session.

v0.4 changes the architecture:

```text
Linux client                         macOS server

control TLS socket   ──────────────► mouse / keyboard / clipboard
video TLS socket     ◄────────────── H.264 stream
file TLS sockets     ◄─────────────► files / folders
```

The video path uses:

- **ScreenCaptureKit** for continuous macOS display capture **inside the main server process**;
- **VideoToolbox** for real-time H.264 encoding on the Mac;
- **PyAV/FFmpeg** for H.264 decoding on Linux;
- a dedicated TLS video connection so slow video rendering cannot block mouse/keyboard traffic.

A completely static macOS desktop can legitimately produce no new ScreenCaptureKit/H.264 data for a while. The Linux client therefore treats video socket read timeouts as idle intervals rather than disconnects. TCP keepalive and the control channel are used to detect real peer loss.

The Linux client exposes four per-connection quality presets:

| Quality | Max width | FPS | Target bitrate |
|---|---:|---:|---:|
| Low | 1280 px | 15 | 3 Mbit/s |
| Balanced | 1920 px | 30 | 8 Mbit/s |
| High | 2560 px | 30 | 14 Mbit/s |
| Ultra | 3840 px | 60 | 25 Mbit/s |

The Mac never upscales the captured display; these are upper bounds. The server clamps client requests to sane limits before starting VideoToolbox.

## Normal setup

### First setup on the Mac

1. Install **ZorinMacBridge Server**.
2. Open it locally once.
3. In the macOS server window, use **Request Screen Recording Access** if the status says *Not granted*, then approve **ZorinMacBridge Server** in macOS. v0.4.3+ keeps capture inside that same app process.
4. Use **Request Mouse/Keyboard Access** if input control is not granted. The app requests permission for synthetic Core Graphics events and opens the relevant macOS privacy pane when needed.
5. Enter a session password of at least 12 characters.
6. Keep **Remember password verifier on this Mac** enabled.
7. Click **Start server**.
8. If you want unattended recovery, use **Enable / Repair** in the **Unattended access / recovery** section and keep the shown SSH fingerprint for the first Zorin pairing.

The Mac stores only a salted PBKDF2 password verifier; it does not store the plaintext session password.

### First connection from Zorin/Linux

1. Open **ZorinMacBridge Client**.
2. Select the discovered Mac.
3. Verify/paste the Mac TLS SHA-256 fingerprint once.
4. Enter the session password.
5. Keep **Remember this Mac** enabled.
6. Connect.
7. For unattended recovery, open **System → Pair / Configure**, verify the Mac SSH fingerprint, and complete the one-time device pairing.

After a successful connection, the client stores:

- the trusted TLS fingerprint in its local config;
- the session password in the Linux desktop system keyring when available;
- the Mac's persistent random server ID, so credentials are associated with the machine rather than only its current IP address.

On later connections, discovery can restore the saved fingerprint/password automatically.

## Passwords: do not mix these up

ZorinMacBridge uses two different credentials:

- **ZorinMacBridge session password** — an app-specific password for the normal Desktop connection. Create it on the Mac during command-line installation or in the Server window. It should be different from your macOS login password. The Mac stores only a salted PBKDF2 verifier.
- **macOS account password** — the normal password you use to log in/unlock the Mac. It is used only once by **System → Pair / Configure** to authenticate the first SSH connection and install the restricted Ed25519 device key. The Zorin client does not save this password.

`install-macos.sh` now performs unattended setup by default: it enables Remote Login (SSH), installs/repairs P0, verifies that SSH was enabled, and prompts for the ZorinMacBridge session password. Use `./install-macos.sh --app-only` only when you intentionally want to install the app without P0/SSH setup.

## LAN discovery and changing DHCP addresses

The Zorin client discovers both the normal desktop service and the P0 system service with Bonjour/mDNS. Each Mac has a persistent `server_id` UUID. The current IP address is treated only as a transport address and may change after DHCP renewals or reboots.

When P0 is enabled, the system LaunchDaemon advertises `_zorinmacbridge-admin._tcp` independently of the user-session desktop app. This lets **Find Macs** rediscover the same Mac by `server_id`, hostname, and its current IP even if the desktop streamer has crashed. Saved TLS and SSH host-key pinning still verify the machine before trusted connections are accepted.

The project deliberately does not use a network-interface MAC address as the persistent identity. A Mac can have multiple interfaces and modern macOS can use private Wi-Fi addresses; the application-level UUID plus pinned cryptographic identities are more appropriate.

## Unattended access / recovery (P0)

The remote desktop still runs in the logged-in macOS user session because ScreenCaptureKit and GUI input belong to that session. v0.8.0 adds an **optional** system recovery layer so a failed desktop service does not force you to reconnect a monitor.

On the Mac, open **ZorinMacBridge Server → Unattended access / recovery → Enable / Repair** once. macOS asks for administrator authorization. The app then:

- keeps the existing user LaunchAgent for the actual desktop streamer;
- installs a small root LaunchDaemon for heartbeat/recovery only;
- enables macOS Remote Login (SSH) as the administration transport;
- opens **no custom privileged network port**;
- shows the Mac SSH host fingerprint for first pairing.

On Zorin, select the same Mac in the normal selector and open **System → Pair / Configure**. Verify the displayed SSH fingerprint against the Mac, then enter the macOS short username/password once. The password is used only for that first SSH login and is not saved. The generated Ed25519 key is restricted with a forced command, so it cannot be used as a general shell key or for SSH tunnelling.

After pairing, the **System** page provides health/status, ZMB start/stop/restart, startup repair, logs, sleep, reboot and shutdown. A paired Mac remains in the same normal selector as **paired/offline** when its desktop mDNS advertisement disappears, so the System page can still be used to recover the desktop service.

The system layer does not bypass macOS login, FileVault, Screen Recording, or Accessibility. If there is no logged-in GUI session, system administration can remain available but there is no normal desktop to stream. Reboot is blocked by the helper when FileVault state indicates that local unlock may be required.

### Remote Login and Full Disk Access on current macOS

macOS may refuse `systemsetup -setremotelogin on` unless the invoking process has Full Disk Access. ZorinMacBridge therefore treats this as a one-time local action rather than a failed P0 installation. If setup reports `REMOTE_LOGIN=manual_required`, open **System Settings → General → Sharing → Remote Login** and turn it on. The P0 helpers, pairing files, recovery service, and update service are already installed; rerunning P0 is optional.

## Stable macOS permissions

macOS controls Screen Recording and mouse/keyboard control through its privacy/TCC system. ZorinMacBridge cannot silently grant itself those permissions. In v0.5.2+, the server window has explicit **Request Screen Recording Access** and **Request Mouse/Keyboard Access** buttons. Those APIs are invoked only by a local click on the Mac; a remote connection never triggers a privacy prompt.

For permissions to survive application updates reliably, macOS needs to recognize the new build as the same application. Ad-hoc signed builds do not provide a stable code identity across changed versions. For private/internal use, ZorinMacBridge uses a persistent self-signed code-signing identity created locally on each Mac. The private key never leaves that Mac. The project keeps the bundle identifier stable as:

```text
com.ilovemyprojects.zorinmacbridge.server
```

For a true VNC/remote-desktop product, Apple also documents the restricted **Persistent Content Capture** entitlement:

```text
com.apple.developer.persistent-content-capture
```

Apple requires explicit approval before an app can use that entitlement. Do not add it to release signing until Apple has approved the entitlement for the developer account.

The transport signature from GitHub is not used as the installed identity. Before installation, v0.6.7+ injects that Mac's persistent local identity marker and re-signs the staged app with the same explicit designated requirement, so later updates keep the same DR without certificates or keychains.

## Remote controls

- continuous H.264 macOS screen stream;
- explicit **Capture keyboard & mouse** switch in the Linux client;
- mouse movement, left/right/middle click, drag and drop when input capture is enabled;
- scrolling and keyboard input when input capture is enabled;
- Linux-friendly shortcut mapping;
- bidirectional text clipboard;
- file and complete-folder transfer;
- client-selectable H.264 quality presets;
- optional automatic reconnect after unexpected LAN/session drops.

The client starts in **view-only mode**. Enable **Capture keyboard & mouse** when you want local input to control the Mac. Turning it off immediately releases any keys/modifiers/buttons the client believes are held down.

### Full-screen remote desktop

- **Double-click** the remote desktop image to enter full-screen mode.
- While full-screen, a top status bar shows connection state, selected video quality, input-capture state, and the exit instructions.
- Leave full-screen with **Alt+Esc**, the **Exit Full Screen** button, or by **double-clicking the top status bar**.
- Double-clicking the remote image while already full-screen is sent to the Mac instead of exiting, so normal remote double-click actions remain usable.
- **Alt+Esc is reserved locally** while full-screen is active and is not sent to the Mac.
- Entering or leaving full-screen releases held remote input state to avoid a stuck Alt/Ctrl/Command key.

### Video quality and reconnect

Use **Video quality** in the connection card to choose Low, Balanced, High, or Ultra. The selected profile applies to the next video connection/reconnection.

**Auto reconnect** is enabled by default. If the control session ends unexpectedly, the client retries with bounded backoff. Choosing **Disconnect** manually cancels pending reconnect attempts.

### Keyboard mapping

| Linux keyboard | Remote macOS |
|---|---|
| Left `Ctrl` | `Command (⌘)` |
| Right `Ctrl` | real macOS `Control` |
| `Ctrl+C` | `⌘C` |
| `Ctrl+V` | `⌘V` |
| `Ctrl+A` | `⌘A` |
| `Ctrl+S` | `⌘S` |
| `Ctrl+Z` | `⌘Z` |
| `Ctrl+F` | `⌘F` |

Use **Right Ctrl+C** when you need real macOS `Control+C`, for example to interrupt a process in Terminal.

## Clipboard

Text clipboard synchronization is built in:

- `Ctrl+V` pushes the Linux clipboard to macOS before sending `⌘V`;
- after copy/cut, the client can pull the macOS text clipboard back to Linux;
- explicit **Clipboard Linux → Mac** and **Clipboard Mac → Linux** buttons are available.

Clipboard synchronization is currently text-only.

## Shared files and folders

The Linux **Files** page is now a native GTK/libadwaita-style browser with system folder/file icons, the current remote path, upload/download actions, parent navigation, refresh, and native Zorin/GNOME file/folder pickers.

The default macOS share is:

```text
~/ZorinMac-Share
```

From Linux you can:

- browse the remote directory tree;
- create folders;
- upload one or many files;
- upload a complete directory tree;
- download individual files;
- download complete directory trees.

Remote file access is restricted to the configured share root. Symlinks are skipped.

On the Mac server, click **Open Shared Folder** (or press **Command+Shift+O**) to open the configured share directly in Finder. The default is `~/ZorinMac-Share`.

File transfer is explicit rather than automatic two-way source synchronization. This avoids silently overwriting newer project files when both machines have changed the same path.

## LAN auto-discovery

While the server is running, it can advertise:

```text
_zorinmacbridge._tcp.local.
```

The advertisement contains basic LAN connection metadata and a persistent random server ID. It does **not** publish the session password, TLS private key, clipboard contents, or shared files.

Discovery does not automatically create trust. On first pairing, verify the TLS fingerprint shown by the Mac. After that successful pairing, the Linux client can remember the trusted fingerprint locally.

## Updates

The normal desktop updater is still available through **Help → Check for updates**.

With P0 enabled and the Mac paired, the Zorin **System** tab also provides:

- **Check update** — query the official GitHub release from Zorin;
- **Update now** — download, verify and install the Mac update remotely with no local password prompt;
- **Automatic updates: Off / Notify only / Install automatically**.

The scheduled Mac updater runs every 6 hours. `Notify only` records whether a newer release exists and exposes it in the System tab. `Install automatically` downloads the Apple Silicon `.dmg`, verifies the release SHA-256, verifies the macOS transport signature and bundle identifier, reapplies the Mac's persistent local designated requirement, makes a backup, installs the app, refreshes the bundled P0 helpers, and restarts the desktop service when a user GUI session exists. A failed install rolls the application bundle back.

The P0 updater is a separate system LaunchDaemon from the recovery daemon so replacing/reloading recovery components cannot terminate the update transaction that is performing the replacement. No additional privileged network listener is opened; remote control still travels through the restricted SSH key.

On Linux, the regular in-app update path continues to use the verified `.deb` and system package manager.

You can also re-run the one-command installers.


## Stable macOS permissions across updates

There is **no signing setup to run on Zorin/Linux**. Do not create GitHub signing secrets and do not run a signing helper.

For v0.6+, each Mac automatically owns one persistent local signing identity. The one-command installer and the in-app updater re-sign the staged server app with that same identity before placing it in `/Applications`. The private key never leaves the Mac.

The first migration from an older v0.5.x build can require Screen Recording and mouse/keyboard approval once because the code identity genuinely changes at that boundary. After migration, normal v0.6+ updates reuse the same identity on that Mac.

See [`SIGNING.md`](SIGNING.md) for the architecture.

## Security model

- LAN/private-address connections only;
- TLS 1.2+;
- pinned SHA-256 server certificate fingerprint;
- password authentication after TLS verification;
- separate video/control/file channels;
- no cloud relay;
- no vendor account;
- no telemetry;
- no UPnP/router configuration;
- file access constrained to the configured share directory;
- plaintext server password is not stored on the Mac;
- remembered Linux passwords use the desktop system keyring when available;
- P0 is optional and uses native macOS SSH rather than a new privileged network listener;
- P0 device keys are Ed25519 keys restricted to a fixed command gateway;
- the system helper accepts only a closed administration command set and does not bypass macOS TCC/FileVault.

See [SECURITY.md](SECURITY.md).

## Releases

Manual downloads:

- [Linux amd64 `.deb`](https://github.com/ILoveMyProjects/ZorinMacBridge/releases/latest/download/ZorinMacBridge-Client_linux-amd64.deb)
- [macOS Apple Silicon `.dmg`](https://github.com/ILoveMyProjects/ZorinMacBridge/releases/latest/download/ZorinMacBridge-Server_macOS-arm64.dmg)

---

# Build / run from source

Normal users should use the installers above.

## Linux client

The source client uses the native Zorin/Ubuntu GTK stack. The setup script installs the required system packages, then `run_client.sh` launches the GTK4/libadwaita client.

```bash
git clone https://github.com/ILoveMyProjects/ZorinMacBridge.git
cd ZorinMacBridge
chmod +x scripts/setup-source-linux.sh run_client.sh
./scripts/setup-source-linux.sh
./run_client.sh
```

## macOS server

macOS 13+ and Xcode Command Line Tools are required for the native in-process ScreenCaptureKit/VideoToolbox streaming library.

```bash
git clone https://github.com/ILoveMyProjects/ZorinMacBridge.git
cd ZorinMacBridge
chmod +x scripts/setup-source-macos.sh scripts/build-macos-streamer.sh
./scripts/setup-source-macos.sh
./.venv/bin/python mac_server_gui.py
```

## Tests

```bash
python test_protocol.py
python test_support.py
python test_updater.py
python test_native_ui.py
python test_local_signing.py
python test_p0_admin.py
python -m compileall -q .
swiftc -frontend -parse native/macos/ZMBStreamerLib.swift
```

The final macOS compilation of the native streaming dylib is performed on the macOS GitHub Actions runners because ScreenCaptureKit and VideoToolbox are macOS frameworks. The dylib is loaded into `ZorinMacBridge Server.app` with `ctypes`; it is not launched as a child capture process.

## Troubleshooting

The Linux client includes a **Logs** tab. Connection logs show TCP, TLS, fingerprint verification, authentication, control-channel state, H.264 video-channel state, decode errors, and disconnect reasons. Passwords are never written to the logs.

If macOS asks for Screen Recording **when you press Connect**, make sure you are on v0.4.3 or newer. v0.4.0-v0.4.2 launched a separate native capture executable, which could be treated as separate TCC-responsible code. v0.4.3 runs ScreenCaptureKit in the main server process instead.

v0.6.7+ re-signs installed updates with the same explicit designated requirement on each Mac. If permissions change after a normal v0.6.7+ update, treat that as a bug and capture the server log plus the `Code signing:` line from the server UI.

If video works but mouse/keyboard does not, check the Mac server log for:

```text
Accessibility permission: granted
```

If it says Accessibility is not granted, enable **ZorinMacBridge Server** under **System Settings → Privacy & Security → Accessibility**, then quit and reopen the application.

## License

MIT — see [LICENSE](LICENSE).

## macOS privacy permissions and updates

Screen Recording and Accessibility are macOS TCC permissions tied to the app's code identity.
The release artifact's transport identity is not the installed identity in v0.6+: the staged app is re-signed locally before installation.
ZorinMacBridge never bypasses TCC and, starting with v0.5.1, a remote **Connect** will not
trigger the Screen Recording prompt: if permission is missing, the video channel fails with
a clear message and the permission must be granted locally on the Mac.

Stable permissions across v0.6+ updates use the automatic per-Mac identity described in [`SIGNING.md`](SIGNING.md); no Linux signing command is required.

### macOS privacy permissions across updates

macOS installs from **v0.6 onward use a persistent per-Mac code identity**.
The release workflow produces an ad-hoc transport artifact with an explicit transport DR; the installer/updater applies that Mac's persistent local DR before installation.
After the one-time migration from an older build, Screen Recording and mouse/keyboard privacy grants are intended to remain associated with the same installed app identity across normal v0.6+ updates.
See `SIGNING.md`.
