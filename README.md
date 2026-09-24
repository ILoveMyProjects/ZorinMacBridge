# ZorinMacBridge

A LAN-only remote desktop bridge for a **Zorin/Linux development workstation → macOS machine** workflow.

Keep Linux as your main development desktop and use the Mac only when Xcode, the iOS Simulator, signing, or other macOS-only tooling is required.

**No cloud account, relay server, discovery service, telemetry, or Internet connection is required at runtime.**

> Status: early MVP / experimental. The code is intentionally small and auditable, but it is not an independently security-audited remote desktop product.

## Download and run — no Git required

Normal users do **not** need to clone this repository or install Python.

### Zorin OS / Ubuntu — client

**[Download the latest `.deb` installer](https://github.com/ILoveMyProjects/ZorinMacBridge/releases/latest/download/ZorinMacBridge-Client_linux-amd64.deb)**

Then double-click the downloaded `.deb` file and install it with the system package installer, or run:

```bash
sudo apt install ./ZorinMacBridge-Client_linux-amd64.deb
```

After installation, launch **ZorinMacBridge Client** from the application menu.

### macOS — server

Choose the build matching the Mac:

- **[Apple Silicon (M1/M2/M3/M4/…)](https://github.com/ILoveMyProjects/ZorinMacBridge/releases/latest/download/ZorinMacBridge-Server_macOS-arm64.dmg)**
- **[Intel Mac](https://github.com/ILoveMyProjects/ZorinMacBridge/releases/latest/download/ZorinMacBridge-Server_macOS-x86_64.dmg)**

Open the `.dmg`, launch **ZorinMacBridge Server**, choose the Mac's LAN IP and shared folder, enter a session password, then click **Start server**.

The macOS build is currently ad-hoc signed rather than Apple-notarized. On first launch macOS may require **Control-click → Open** and confirmation in Privacy & Security. The server also needs **Screen Recording** and **Accessibility** permissions.

> The download links above become active after the first tagged GitHub Release is built.

---
## What it does

- Streams the main macOS display to a Linux GUI client.
- Sends mouse movement, clicks, scrolling, and keyboard input to macOS.
- Maps common Linux shortcuts to macOS conventions.
- Synchronizes the **text clipboard** between Linux and macOS.
- Transfers individual files or entire directory trees in both directions.
- Restricts remote file access to a dedicated macOS share directory.
- Uses TLS with manual SHA-256 certificate fingerprint pinning.
- Accepts only literal private/LAN addresses; hostnames and public IPs are rejected.

## Intended workflow

```text
Linux / Zorin OS                    MacBook / macOS
(primary workstation)              (Xcode / iOS build machine)

┌──────────────────────┐            ┌────────────────────────┐
│ Browser / IDE / CLI  │            │ Xcode / Simulator      │
│                      │            │                        │
│ ZorinMac client      │◄── LAN ───►│ ZorinMac server        │
│                      │            │                        │
│ Local project files  │◄──────────►│ ~/ZorinMac-Share       │
└──────────────────────┘            └────────────────────────┘
```

The Mac remains a normal macOS machine. This project does **not** replace Xcode or allow iOS builds on Linux; it provides a remote desktop and file bridge so the Mac can be used from the Linux workstation.

## Keyboard mapping

By default, the client maps:

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

This makes normal desktop shortcuts feel natural from Linux while preserving a real macOS Control key for terminal work. For example, use **Right Ctrl+C** when you need to send `Control+C` to a terminal process on the Mac.

## Clipboard behavior

Text clipboard synchronization is built in:

- `Ctrl+V` pushes the current Linux text clipboard to macOS and then sends `⌘V`.
- After `Ctrl+C` or `Ctrl+X`, the client can pull the macOS text clipboard back to Linux.
- The GUI also provides explicit **Clipboard Linux → Mac** and **Clipboard Mac → Linux** buttons.

Current limitation: clipboard synchronization is text-only. Files are transferred through the file panel instead of the system clipboard.

## File sharing

The server exposes only:

```text
~/ZorinMac-Share
```

From Linux you can:

- browse the remote directory tree;
- create directories on the Mac;
- upload one or multiple files;
- upload a complete directory tree;
- download individual files;
- download complete directory trees.

Symlinks are skipped. The client does not receive arbitrary access to the Mac filesystem.

File transfer is explicit rather than automatic two-way synchronization. That is intentional: an early remote-development tool should not silently overwrite newer source files during conflicts.

---

# Build / run from source (developers)

The instructions below are for contributors and developers. Normal users should use the installers in **Download and run** above.

## 1. macOS server

### Requirements

- macOS
- Python 3
- the Mac and Linux workstation on the same trusted LAN

Clone the repository:

```bash
git clone https://github.com/ILoveMyProjects/ZorinMacBridge.git
cd zorin-mac-bridge
```

Install the Python environment:

```bash
chmod +x install_macos.sh run_server.sh
./install_macos.sh
```

Find the Mac's private LAN address, for example:

```text
192.168.1.50
```

Start the server bound to that address:

```bash
./run_server.sh 192.168.1.50
```

The server asks for a session password and prints a certificate fingerprint similar to:

```text
TLS SHA-256: AA:BB:CC:DD:...:FF
```

Copy that fingerprint to the Linux client. The password is not stored by default.

### macOS permissions

The process running the server needs these macOS permissions:

- **System Settings → Privacy & Security → Screen Recording**
- **System Settings → Privacy & Security → Accessibility**

macOS may require Terminal (or your terminal application) to be restarted after granting the permissions.

## 2. Linux / Zorin OS client

### Requirements

On Zorin OS / Ubuntu-family systems:

```bash
sudo apt update
sudo apt install -y python3 python3-venv python3-tk git
```

Clone and install:

```bash
git clone https://github.com/ILoveMyProjects/ZorinMacBridge.git
cd zorin-mac-bridge
chmod +x install_linux.sh run_client.sh
./install_linux.sh
```

Start the client:

```bash
./run_client.sh
```

Enter:

- the Mac's private IP address;
- port `45950` unless you changed it;
- the session password;
- the SHA-256 TLS fingerprint printed by the Mac server.

Then click **Connect**.

---

# Offline / LAN-only design

At runtime the project is designed to operate without Internet access.

The macOS server:

- listens only on the literal address supplied with `--bind`;
- does not perform DNS resolution;
- does not connect to a cloud service, relay, update server, or telemetry endpoint;
- rejects clients with public source addresses.

The Linux client:

- accepts only literal private/loopback/link-local IP addresses;
- rejects hostnames;
- rejects public IP addresses;
- connects directly to the specified Mac.

There is no account system, NAT traversal, UPnP, automatic port forwarding, cloud discovery, or vendor-operated relay.

### Important distinction: installation vs runtime

The `install_*.sh` scripts normally use `pip` to download Python packages from the Internet. **Runtime does not require that Internet access.**

For an air-gapped installation, pre-download the required Python wheels and install them with `pip --no-index --find-links`.

See [SECURITY.md](SECURITY.md) for the complete security model.

## TLS and authentication

- Minimum TLS version: TLS 1.2.
- The server creates a local self-signed certificate under `~/.zorin-mac-bridge/`.
- The client does not blindly trust that certificate.
- The user must enter the server's SHA-256 certificate fingerprint.
- The session password is sent only after TLS is established and the fingerprint matches.

Manual fingerprint verification is intentionally simple: compare the fingerprint shown directly on the Mac with the value entered on Linux.

## Network recommendation

Use this project only on a trusted LAN or VLAN. Do **not** expose port `45950` directly to the public Internet.

For additional isolation, use a firewall rule that permits port `45950` only from the Linux workstation's private IP address.

---

# Display quality

Default server settings:

```text
10 FPS
maximum width: 1920 px
JPEG quality: 72
```

They can be changed manually:

```bash
.venv/bin/python mac_server.py \
  --bind 192.168.1.50 \
  --fps 12 \
  --max-width 1920 \
  --quality 78
```

The current MVP uses macOS screen capture plus JPEG frames. It is not yet comparable to mature RDP/AVC/H.264 implementations for latency, bandwidth efficiency, or high-refresh-rate video.

# Project layout

```text
.
├── linux_client.py      # Linux GUI client
├── mac_server.py        # macOS server
├── protocol.py          # binary framing/protocol definitions
├── test_protocol.py     # protocol/file-security tests
├── install_linux.sh
├── install_macos.sh
├── run_client.sh
├── run_server.sh
├── requirements.txt
├── SECURITY.md
├── CONTRIBUTING.md
└── LICENSE
```

# Running tests

Create the environment first, then run:

```bash
.venv/bin/python -m compileall -q .
.venv/bin/python test_protocol.py
```

The test suite currently covers:

- protocol framing and buffering;
- private/LAN IP filtering;
- path traversal rejection;
- nested directory creation;
- upload → list → download round-trip with byte-for-byte verification.

GitHub Actions runs the protocol tests on every push and pull request.

# Known limitations

- Experimental MVP, not an independently audited security product.
- macOS is currently the only server platform.
- Linux is currently the only client platform.
- Text clipboard only; no image/file clipboard integration yet.
- No automatic two-way filesystem synchronization.
- No audio, microphone, camera, printer, USB, or smart-card forwarding.
- No multi-monitor selection UI yet.
- No H.264/AV1 hardware-accelerated video pipeline yet.
- The current implementation has been protocol-tested in a Linux environment, but real Screen Recording, Accessibility, CoreGraphics input injection, and `pbcopy`/`pbpaste` behavior must also be validated on real macOS hardware.

# Roadmap

Potential next steps:

- improve frame transport and latency;
- optional hardware video encoding;
- better multi-monitor support;
- safer project-folder synchronization with conflict detection;
- reconnect/resume behavior;
- configurable keyboard mapping profiles;
- packaging as native Linux/macOS applications;
- expanded automated tests and macOS CI coverage where practical.

# Contributing

Issues and pull requests are welcome. Please read [CONTRIBUTING.md](CONTRIBUTING.md) before submitting substantial changes.

For security issues, follow [SECURITY.md](SECURITY.md) instead of opening a public issue containing exploit details.

# License

MIT License. See [LICENSE](LICENSE).
