# Security policy

## Intended deployment

ZorinMacBridge is intended for remote access to your own Mac from your own Zorin/Linux workstation on a trusted local network. Do not expose the service port directly to the public Internet.

The core remote session uses no cloud relay, vendor account, telemetry, DNS lookup, UPnP, or router configuration. LAN discovery uses mDNS/Bonjour only while the server is running. GitHub is contacted for installs/update checks; if P0 automatic updates are enabled, the dedicated update LaunchDaemon checks the official release every 6 hours.

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

Starting with v0.6.7, installed Mac copies use a persistent **per-Mac explicit designated requirement (DR)**. No private signing key or certificate exists. A random local marker is stored under the user's Application Support directory, injected into the staged app's `Info.plist`, and included in the explicit DR together with the fixed bundle identifier. Before an update replaces the installed app, the verified release artifact is staged and re-signed ad-hoc with that same DR.

This design is intended to stop normal app updates from changing the code identity associated with existing Screen Recording and input-control grants. The one-time migration from pre-v0.6 builds can still require a fresh grant because the identity genuinely changes at that migration boundary.

Deleting the local signing state causes a new identity to be generated and can therefore require privacy authorization again.

## Optional P0 unattended administration

Machine matching uses a persistent `server_id` UUID as a discovery handle, while trust is enforced by the pinned desktop TLS certificate fingerprint and the pinned SSH host key. The current DHCP IP address is not treated as identity. Network-interface MAC addresses are deliberately not used as trust anchors.

P0 is disabled until the Mac user runs Setup Assistant (or explicitly chooses **Enable / Repair**) and authorizes the local installation with an administrator password. The root LaunchDaemon does not listen on a TCP/UDP socket. Network administration uses the operating system's Remote Login (SSH) service.

The Zorin client generates a dedicated Ed25519 key per paired Mac. The Mac stores it in `authorized_keys` with `restrict` and a forced command pointing to `/usr/local/libexec/zmb-admin-ssh`. That gateway accepts only the documented ZorinMacBridge administration commands and rejects arbitrary shell commands. The root helper is exposed through a narrow sudoers allow-list.

First pairing still requires verifying the Mac SSH host fingerprint. The macOS account password supplied during pairing is passed to the one-time SSH login through a temporary askpass file with user-only permissions and is deleted immediately afterward; it is not written to the pairing profile.

P0 does not modify the TCC database, grant Screen Recording/Accessibility, bypass the login window, or defeat FileVault. The desktop streamer remains a user-session process. Removing P0 deletes ZorinMacBridge-Admin pairing keys but intentionally leaves macOS Remote Login unchanged because the user may rely on ordinary SSH separately.

## P3 Terminal and Project Actions

P3 deliberately does **not** turn the P0 restricted administration key into a shell key. During pairing the user may opt in to a second Ed25519 identity for normal-user SSH. Its `authorized_keys` entry disables agent, port and X11 forwarding and user rc processing, but allows a PTY because the Terminal tab is interactive. The key receives no ZorinMacBridge sudoers entry.

Project profiles contain paths, scheme/configuration and destination metadata only; they contain no passwords or tokens. Project commands are built from validated fields with shell quoting. `git pull` uses `--ff-only` and `GIT_TERMINAL_PROMPT=0` so unattended actions fail rather than block waiting for credentials. Revoking a Zorin device removes the user-shell key before the restricted admin key and deletes local pairing private keys.

## P5 desktop-streaming resource bounds

P5 does not add a new privileged or network service. Video remains a separate authenticated TLS role on the existing LAN-only server. Adaptive quality changes replace only that video connection. The client keeps a single newest decoded frame, and the native Mac writer keeps at most one pending encoded access unit, so a slow consumer cannot create an unbounded video backlog in either application. Server/client sockets use TCP_NODELAY as a latency optimization; TLS pinning and password authentication are unchanged.

## Updates

The interactive updater remains available, but P0 also supports `Off`, `Notify only`, and `Install automatically`. The automatic checker is a separate root LaunchDaemon with a six-hour interval and no network listener of its own. For unattended installation, the root helper launches the installed ZorinMacBridge application in a tightly scoped update mode with a root-owned staging directory. Stage C production updates select the official GitHub release, download assets over verified TLS, verify SHA-256, verify the Developer ID signature, embedded provisioning profile, exact App ID/version, required restricted entitlements and Developer Team continuity, and stage the verified bundle without changing its code signature before replacement. No verified/staged app is handed through a user-writable directory.

The root helper accepts only fixed update commands through the forced-command SSH gateway. Before replacement it verifies the staged app again, moves the existing app to a backup, installs the staged app, refreshes the P0 helper payload embedded in the new application, and rolls the app bundle back if installation/helper refresh fails. Automatic updates do not grant arbitrary root shell access and do not bypass TCC or FileVault. The transport signature is intentionally ad-hoc; therefore release authenticity ultimately depends on control of the GitHub repository/release channel in addition to HTTPS and the published checksum. A future independent release-signing key would provide a stronger supply-chain trust boundary.

The first local P0 setup still requires administrator authorization. After that, a paired Zorin client can run **Check update**, **Update now**, or change the automatic-update mode without a local monitor or another administrator-password prompt.

Linux package installation continues to use the system package manager and its normal administrator authorization flow.

## Reporting issues

Do not include real session passwords, TLS private keys, personal file contents, or the files under `~/Library/Application Support/ZorinMacBridge/CodeSigning/` in public issues.
