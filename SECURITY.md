# Security policy

## Intended deployment

ZorinMacBridge is intended for remote access to your own Mac from your own Zorin/Linux workstation on a trusted local network. Do not expose the service port directly to the public Internet.

The core remote session uses no cloud relay, vendor account, telemetry, DNS lookup, UPnP, or router configuration. LAN discovery uses mDNS/Bonjour only while the server is running. GitHub is contacted only when the user explicitly installs or checks for an update.

## Network trust

- TLS protects control, video, and file channels.
- The Linux client pins the server certificate SHA-256 fingerprint.
- Authentication occurs after TLS verification.
- The server accepts only private/link-local/loopback address ranges.
- Video, control, and file transfer use separate connections.

## Authentication storage

The Mac stores only a salted PBKDF2 password verifier. The plaintext session password is not stored there. When the user chooses to remember the Mac on Linux, the client uses the desktop keyring when available.

## File-transfer boundary

Remote file operations are constrained to the configured share root. Path validation rejects absolute paths, `..` traversal, and symlink escapes outside that root.

## macOS privacy permissions and update identity

Screen Recording is required for video and Accessibility/Core Graphics event permission is required for remote keyboard/mouse control. ZorinMacBridge does not edit the TCC database or bypass macOS consent.

Starting with v0.6.0, installed Mac copies use a persistent **per-Mac local code-signing identity**. The private key is generated and kept on that Mac under the user's Application Support directory; it is not uploaded to GitHub and is not stored in the public repository. Before an update replaces the installed app, the verified release artifact is staged and re-signed with the same local identity. The resulting designated requirement is bound to the fixed bundle identifier and that local certificate.

This design is intended to stop normal app updates from changing the code identity associated with existing Screen Recording and input-control grants. The one-time migration from pre-v0.6 builds can still require a fresh grant because the identity genuinely changes at that migration boundary.

Deleting the local signing state causes a new identity to be generated and can therefore require privacy authorization again.

## Updates

There is no background update checker. An update request occurs only after an explicit user action.

The updater downloads the matching release package and checksum from GitHub, verifies SHA-256, verifies the macOS transport signature and bundle identifier, applies the Mac's persistent local signature to a staged copy, verifies the resulting signature/designated requirement, and only then asks for administrator authorization to replace the installed application.

Linux package installation continues to use the system package manager and its normal administrator authorization flow.

## Reporting issues

Do not include real session passwords, TLS private keys, personal file contents, or the files under `~/Library/Application Support/ZorinMacBridge/CodeSigning/` in public issues.
