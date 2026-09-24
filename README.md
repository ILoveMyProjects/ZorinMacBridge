# ZorinMacBridge

<p align="center">
  <img src="assets/client.png" width="190" alt="ZorinMacBridge Client icon">
  &nbsp;&nbsp;&nbsp;&nbsp;
  <img src="assets/server.png" width="190" alt="ZorinMacBridge Server icon">
</p>

A LAN-first remote desktop bridge for a **Zorin OS / Linux development workstation → macOS development machine** workflow.

Keep Linux as your main workstation for web/app development and open the Mac only when Xcode, the iOS Simulator, signing, or another macOS-only tool is required.

The remote desktop session itself does **not** require a cloud account, vendor relay, telemetry service, or Internet connection. Optional LAN discovery uses local mDNS/Bonjour only. GitHub is contacted only when you explicitly install/update the app or manually choose **Check for updates**.

> **Status:** early MVP / experimental. The code is intentionally small and auditable, but it has not received an independent security audit.

## Install

### Zorin OS / Ubuntu — Client

Copy and paste one command:

```bash
curl -fsSL https://raw.githubusercontent.com/ILoveMyProjects/ZorinMacBridge/master/install-linux.sh | bash
```

The installer downloads the latest `.deb`, verifies its published SHA-256 checksum, installs it with `apt`, and adds **ZorinMacBridge Client** to the application menu.

To update later, either use **Help → Check for updates** in the app or run the same command again.

### macOS — Server

> The Apple machine runs **macOS**, not iOS. iOS is the target platform you build for in Xcode.

Copy and paste one command on the Mac:

```bash
curl -fsSL https://raw.githubusercontent.com/ILoveMyProjects/ZorinMacBridge/master/install-macos.sh | bash
```

The installer automatically detects Apple Silicon vs Intel, downloads the correct `.dmg`, verifies its published SHA-256 checksum, and installs **ZorinMacBridge Server** into `/Applications`.

**The installer does not start the server.** Open **ZorinMacBridge Server** manually only when you want remote access.

The macOS build is currently ad-hoc signed rather than Apple-notarized. On first launch macOS may require **Control-click → Open** and confirmation under **System Settings → Privacy & Security**.

The server needs these explicit macOS permissions:

- **Screen Recording**
- **Accessibility**

## Normal workflow

```text
Zorin OS / Linux                       MacBook / macOS
main workstation                      Xcode / iOS build machine

┌────────────────────────┐            ┌────────────────────────┐
│ Browser / IDE / CLI    │            │ Xcode / Simulator      │
│                        │            │                        │
│ ZorinMacBridge Client  │◄── LAN ───►│ ZorinMacBridge Server  │
│                        │            │   manually started      │
│ Local project files    │◄──────────►│ Shared folder           │
└────────────────────────┘            └────────────────────────┘
```

1. Open **ZorinMacBridge Server** on the Mac.
2. Choose the Mac LAN IP, shared folder, and a session password.
3. Click **Start server**.
4. Open **ZorinMacBridge Client** on Linux.
5. The client automatically performs a short LAN scan. You can also click **Find Macs**.
6. Select the discovered Mac or enter its private IP manually.
7. Compare/paste the TLS SHA-256 fingerprint displayed by the Mac.
8. Connect.
9. When finished, click **Stop server** or close the macOS app.

## The Mac server is manual — not a service

ZorinMacBridge deliberately does **not** install or create:

- a LaunchAgent;
- a LaunchDaemon;
- a login item;
- a system service;
- hidden persistence;
- a background update checker.

The server listens only after you manually open the application and click **Start server**. Closing the application stops the server.

## LAN auto-discovery

When **Advertise this Mac to ZorinMacBridge clients on the local LAN** is enabled, a running server publishes an mDNS/Bonjour service named:

```text
_zorinmacbridge._tcp.local.
```

This advertisement exists only while the server is running. `Stop server` or exiting the app removes it.

The client scans the local multicast domain and fills in the Mac's **private IP and port**. Discovery does **not** automatically trust the machine and does not replace TLS verification. The TLS certificate fingerprint still has to match the value displayed by the Mac server.

No cloud discovery server is involved.

## Remote desktop controls

- macOS screen streaming to Linux;
- mouse movement and clicks;
- scrolling;
- keyboard input;
- Linux-friendly shortcut mapping;
- bidirectional text clipboard;
- file and complete-folder transfer.

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

This keeps normal Linux desktop muscle memory while preserving the real Control key for terminal work. For example, use **Right Ctrl+C** when you need macOS `Control+C` in Terminal.

## Clipboard

Text clipboard synchronization is built in:

- `Ctrl+V` pushes the Linux clipboard to macOS before sending `⌘V`;
- after copy/cut, the client can pull the macOS text clipboard back to Linux;
- explicit **Clipboard Linux → Mac** and **Clipboard Mac → Linux** buttons are available.

Clipboard synchronization is currently text-only.

## Shared files and folders

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

File transfer is explicit rather than automatic two-way source synchronization. This avoids silently overwriting newer project files when both machines have changed the same path.

## App icons and tray / menu-bar integration

The release packages include dedicated Client and Server icons.

- The Linux `.deb` installs the client icon for the application menu/taskbar.
- The macOS `.app`/`.dmg` embeds the server icon.
- The applications also attempt to expose a small tray/menu-bar icon with common actions such as show window, discovery/start/stop, update check, and quit.

Tray support is best-effort because Linux desktop environments differ. If the tray backend is unavailable, the normal application window still works.

## Updates

There is **no automatic or background update check**.

Choose **Help → Check for updates** manually. Only then does the app contact the GitHub Releases API. If a newer version exists, the app offers to open the release page.

You can also update by re-running the one-command installer:

### Linux

```bash
curl -fsSL https://raw.githubusercontent.com/ILoveMyProjects/ZorinMacBridge/master/install-linux.sh | bash
```

### macOS

```bash
curl -fsSL https://raw.githubusercontent.com/ILoveMyProjects/ZorinMacBridge/master/install-macos.sh | bash
```

The macOS installer updates the app in `/Applications` but **does not start it automatically**.

## Security model

- direct LAN connection;
- literal private IPs only for remote desktop connections;
- public IPs rejected;
- TLS 1.2+;
- manual SHA-256 certificate fingerprint pinning;
- password authentication after TLS verification;
- no cloud relay;
- no vendor account;
- no telemetry;
- no automatic router configuration or UPnP;
- file access constrained to the configured share directory;
- server runs only when manually started.

Optional mDNS/Bonjour discovery broadcasts basic service presence on the local network while the server is running. It does not send the session password or clipboard/file contents.

See [SECURITY.md](SECURITY.md) for details.

## Releases

Manual package downloads remain available:

- [Linux amd64 `.deb`](https://github.com/ILoveMyProjects/ZorinMacBridge/releases/latest/download/ZorinMacBridge-Client_linux-amd64.deb)
- [macOS Apple Silicon `.dmg`](https://github.com/ILoveMyProjects/ZorinMacBridge/releases/latest/download/ZorinMacBridge-Server_macOS-arm64.dmg)
- [macOS Intel `.dmg`](https://github.com/ILoveMyProjects/ZorinMacBridge/releases/latest/download/ZorinMacBridge-Server_macOS-x86_64.dmg)

GitHub Actions builds these packages automatically for version tags such as `v0.3.0`.

---

# Build / run from source

Normal users should use the one-command installers above. The steps below are for contributors.

## Linux client from source

```bash
git clone https://github.com/ILoveMyProjects/ZorinMacBridge.git
cd ZorinMacBridge
chmod +x scripts/setup-source-linux.sh run_client.sh
./scripts/setup-source-linux.sh
./run_client.sh
```

## macOS server from source

```bash
git clone https://github.com/ILoveMyProjects/ZorinMacBridge.git
cd ZorinMacBridge
chmod +x scripts/setup-source-macos.sh
./scripts/setup-source-macos.sh
./.venv/bin/python mac_server_gui.py
```

## Tests

```bash
python test_protocol.py
python test_support.py
python -m compileall -q .
```

## License

MIT — see [LICENSE](LICENSE).

## Troubleshooting connection failures

The Linux client includes a **Logs** tab. If **Connect** immediately changes to **Disconnected**, open **Logs** and inspect the most recent entries. The log records the TCP connection, TLS handshake, TLS fingerprint verification, authentication result, server-reported session errors, and disconnect reason. Session passwords are never written to the log.

Use **Copy all** to copy the log into a bug report, or **Save…** to write it to a local `.log` file.
