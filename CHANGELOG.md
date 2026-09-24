# Changelog

## 0.6.2

- Fixed the remaining macOS CI transport-signing failure where `codesign` could report `The specified item could not be found in the keychain` even though `security find-identity` saw the imported identity.
- The ephemeral signing keychain is now explicitly added to the user keychain search list and temporarily made the default keychain before any `codesign` call.
- Transport signing now selects the identity by its certificate common name, verifies the matching private key is present, and prints keychain diagnostics if signing still fails.
- The persistent per-Mac local signing keychain is also added to the user's keychain search list so the same errSecItemNotFound failure cannot reappear during an actual in-app update.
- Removed the Swift 6 async-context `NSLock.lock()/unlock()` warnings in the ScreenCaptureKit startup path by moving the lock operation into a synchronous helper.

## 0.6.1

- Fixed the macOS GitHub Actions signing failure `The specified item could not be found in the keychain` on macOS 15 runners.
- Removed the brittle `security set-key-partition-list` dependency from both ephemeral transport signing and persistent per-Mac local signing.
- Imported signing identities into dedicated keychains with explicit non-interactive ACLs, and added an identity-presence check before `codesign` runs.
- Added phase markers around transport/local signing in the release workflow so future keychain failures identify the exact failing stage.
- Kept the v0.6 per-Mac persistent identity design: release artifacts are transport-signed only; installed updates are re-signed on the Mac with its persistent local identity.

## 0.6.0

- Replaced the Linux/GitHub-secret macOS signing setup with an automatic persistent **per-Mac local code identity**.
- Removed `scripts/setup-stable-signing-linux.sh` and all `MACOS_CERTIFICATE_*` / `MACOS_SIGNING_IDENTITY` release-secret requirements.
- GitHub Actions now applies only an ephemeral self-signed **transport signature** to macOS release artifacts; no developer account or repository signing secret is required.
- The macOS one-command installer verifies the release, stages the app, creates/reuses the Mac's local identity, re-signs the staged app, verifies it, and only then installs it.
- The in-app macOS updater performs the same local re-signing before replacing `/Applications/ZorinMacBridge Server.app`.
- Added an automatic one-time migration for installs upgraded from v0.5.x through the old updater: v0.6.0 re-signs the installed app with the persistent local identity and restarts before the normal GUI starts.
- The persistent designated requirement is bound to `com.ilovemyprojects.zorinmacbridge.server` plus that Mac's local signing certificate.
- Added release/CI tests that fail if the obsolete GitHub signing-secret workflow returns.
- Kept the Linux post-update `execv()` restart path, video idle-timeout fix, auto-reconnect, quality presets, and fullscreen status bar from the v0.5.x series.

## 0.5.7

- Made `scripts/setup-stable-signing-linux.sh` non-invasive by default.
- The signing helper no longer installs packages, runs `gh auth login`, or touches SSH keys.
- Default mode only prepares/reuses the local stable signing identity after explicit confirmation and writes the four GitHub Actions secret values to private local files.
- Added optional `--upload-github` mode that works only when `gh` is already authenticated and requires typing `UPLOAD` before any repository secret is changed.
- Missing local tools now cause a clean error instead of automatic `apt`/`sudo` installation.
- Linux post-update restart now calls `execv()` immediately, without potentially blocking GTK/tray/network cleanup before process replacement.

## 0.5.6

- Added `scripts/setup-stable-signing-linux.sh`: one-time stable macOS signing setup performed entirely from Zorin/Linux.
- No Developer ID is required for private/internal deployments; the script creates a persistent self-signed code-signing identity with OpenSSL.
- The Linux script automatically stores the encrypted signing identity and password in GitHub Actions secrets using `gh`.
- Added a pinned SHA-256 certificate fingerprint secret so release builds fail if GitHub ever receives a different signing certificate by mistake.
- macOS signing now validates the imported certificate fingerprint before signing and continues to refuse ad-hoc releases.
- Documented the Linux-only stable-signing workflow in `SIGNING.md` and README.


## v0.5.5

- macOS releases now **require a persistent signing identity**; ad-hoc fallback was removed.
- The macOS updater refuses ad-hoc or code-identity-incompatible future updates.
- Once migrated to the stable identity, future correctly signed updates preserve the app identity used by macOS privacy permissions.
- Removed the misleading permission-repair button from the server UI.
- Added one-time ad-hoc → stable-signing migration messaging.

## 0.5.4

- Fixed Linux post-update restart by replacing the running process directly with `/usr/bin/zorinmacbridge` instead of relying on GTK/tray teardown or a helper process.
- Added macOS detection of ad-hoc vs stable code signing in the server permission panel.
- Added a local **Repair stale permissions after update** flow for the macOS case where System Settings still shows Screen Recording/Accessibility enabled but the updated build is not actually authorized.
- The repair flow resets only this app's `ScreenCapture` and `Accessibility` TCC records, requests fresh grants locally, and requires a full app restart.
- Added an explicit warning before installing a macOS update when the current build is ad-hoc signed and already has privacy grants.
- Changed macOS restart-after-update to replace the running process with LaunchServices' `open`, avoiding stale Tk/tray processes.

## 0.5.3

- Fixed H.264 sessions disconnecting when the Mac desktop was static: a video socket read timeout is now treated as an idle interval instead of a fatal error.
- Enabled TCP keepalive so genuinely dead LAN peers can still be detected without confusing a quiet ScreenCaptureKit stream with a disconnect.
- Added client-selectable video quality presets: **Low**, **Balanced**, **High**, and **Ultra**. The selected profile is sent in the authenticated video-channel request and is clamped to safe limits by the Mac server.
- Added **Auto reconnect** (enabled by default) with bounded exponential backoff after unexpected control-session disconnects. Manual Disconnect never auto-reconnects.
- Reworked full-screen behavior: double-clicking the remote image enters full screen, but remote-image double-clicks remain available while full screen. Exit is now on a persistent top status bar via **double-click the bar**, **Exit Full Screen**, or **Alt+Esc**.
- Added full-screen status information for connection state, selected quality, input-capture state, and the exit hint.

## 0.5.2

- Added explicit **Request Screen Recording Access** and **Request Mouse/Keyboard Access** controls to the macOS server GUI.
- Permission prompts can now be initiated only by a local user action on the Mac; remote `Connect` still never triggers a macOS privacy prompt.
- Added live Screen Recording and mouse/keyboard permission status plus a manual refresh button in the server window.
- Use Core Graphics `CGRequestScreenCaptureAccess()` for Screen Recording and `CGRequestPostEventAccess()` for synthetic mouse/keyboard event permission.
- Fixed Linux post-update restart so **Restart now** launches a detached replacement process and terminates the old GTK process deterministically instead of leaving GNOME with a misleading “not responding” dialog.
- Clarified that ad-hoc-signed updates can lose TCC grants because macOS treats each changed build as a different code identity; Developer ID signing is still required for reliable permission continuity across releases.

## 0.5.1

- Prevent a remote client connection from triggering the macOS Screen Recording permission prompt.
- Refuse H.264 capture before touching ScreenCaptureKit when the exact running build is not already authorized.
- Propagate the native ScreenCaptureKit/VideoToolbox fatal error text to the Linux client instead of only `code=2`.
- Add optional Developer ID signing and notarization support to the GitHub release workflow.
- Add `SIGNING.md` with the required GitHub secrets and the reason ad-hoc builds cannot preserve TCC permissions across code changes.

## 0.5.0

- Replaced the Linux client presentation layer with a native **GTK4 + libadwaita** interface for Zorin OS / GNOME.
- Reworked the remote file browser with system folder/file icons, native list rows, path display, and GTK file/folder pickers.
- Kept the existing TLS/control/H.264/file-transfer backend while changing the Linux UI layer.
- Changed the Linux `.deb` to install the native Python/GTK client and declare Zorin/Ubuntu system dependencies through APT instead of freezing the GUI with PyInstaller.
- Preserved one-command installation and the in-app `.deb` updater.
- Added **Open Shared Folder** to the macOS server window and Server menu.
- Added **Command+Shift+O** on macOS to open the configured share directory in Finder.
- The default share remains `~/ZorinMac-Share`.

## 0.4.4

- Added an explicit **Capture keyboard & mouse** switch to the Linux client; input is view-only until enabled.
- Added double-click on the remote desktop image to enter full-screen mode.
- Added **Alt+Esc** as a local-only full-screen escape shortcut.
- Input state is released when capture is disabled, full-screen changes, or the client disconnects to reduce stuck modifier/button states on macOS.

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
