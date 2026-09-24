# Security model

ZorinMacBridge is designed for a trusted private LAN. It is not intended to be exposed directly to the public Internet.

## Network boundaries

The client and server accept literal private/loopback/link-local addresses only. The project does not configure router forwarding, UPnP, cloud relays, or public rendezvous services.

Runtime traffic is split across authenticated TLS connections:

- control: mouse, keyboard, clipboard;
- video: H.264 screen stream;
- file operations: listing, upload, download, mkdir.

This separation prevents video backpressure from blocking input or file operations.

## TLS and trust

The macOS server generates a local TLS certificate and private key under:

```text
~/.zorin-mac-bridge/
```

The Linux client pins the SHA-256 certificate fingerprint. LAN discovery does not publish the fingerprint as a trusted value. On first pairing, compare the fingerprint shown by the Mac. The client can remember the verified fingerprint after a successful connection.

## Password handling

The server does not need the plaintext session password after configuration. When password persistence is enabled it stores a salted PBKDF2-HMAC-SHA256 verifier with a high iteration count in a mode-0600 settings file.

The Linux client can remember the plaintext password in the desktop system keyring. If no usable system keyring backend is available, ZorinMacBridge logs a warning and does not silently fall back to a plaintext password file.

Passwords are never written to application logs or mDNS records.

## File confinement

Remote file operations are constrained to the configured share root (default `~/ZorinMac-Share`). The server rejects absolute paths, `..`, NUL bytes, path escapes, and symlink traversal. Uploads are written to temporary files and atomically renamed on successful completion.

## macOS permissions

Screen Recording and Accessibility are macOS privacy/TCC permissions. ZorinMacBridge does not attempt to bypass or silently grant them.

Stable permissions across application updates depend on stable macOS code identity. Ad-hoc signed builds may be treated as different code after updates. Production releases should use a stable Developer ID Application identity and notarization.

Apple also provides the restricted `com.apple.developer.persistent-content-capture` entitlement for VNC applications that need persistent screen-capture access. The entitlement requires Apple approval before it may be used.

## Start at login

The optional launch-at-login feature creates a **per-user LaunchAgent** under `~/Library/LaunchAgents`. It is disabled by default and can be removed from the server UI. It does not install a root daemon.

The optional auto-start-server setting starts listening when the GUI app launches. These options are intended for a Mac mini or other dedicated development Mac where the user explicitly wants unattended access after login.

## Updates

There is no background update checker. A GitHub request occurs only after the user explicitly selects **Check for updates** or runs an installer command.

Release packages are verified against published SHA-256 files before installation. For stronger software-supply-chain assurance, production macOS releases should additionally be Developer-ID-signed and notarized.

## Reporting issues

Do not include real session passwords, TLS private keys, personal file contents, or other secrets in a public issue. Connection logs intentionally omit the session password.
