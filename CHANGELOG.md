## 0.15.2 — PreloginVisibleInputFix

- Fixed a black pre-login viewer caused by discarding ScreenCaptureKit's `.started` first frame; the initial macOS login-window frame is now encoded and sent.
- Pre-login keyboard/mouse traffic is held until the first H.264 frame is decoded, so input cannot tear down the session before the login screen becomes visible.
- LoginWindow pointer/button/scroll failures are isolated per event instead of terminating the entire Desktop control channel.
- Added a Quartz session-event fallback (`kCGSessionEventTap`) when deprecated `IOHIDPostEvent` is rejected with `kIOReturnNotPrivileged`.
- Added regression coverage for first-frame handling, pre-login input gating, per-event isolation, and the IOHID privilege fallback.

# Changelog

## 0.15.1 — SessionPasswordInputFix

- Fixed GTK keyboard capture stealing keystrokes from local text fields, including **ZMB session password**, while remote input capture is enabled by default.
- Remote keyboard forwarding now requires an active Desktop/control session; before connection, normal GTK fields always receive local typing.
- A Desktop `bad password` authentication failure no longer triggers repeated automatic reconnects with the same rejected password. The client returns focus to the session-password field and waits for the user to correct it.
- Empty ZMB session passwords are rejected locally with a clear prompt instead of opening a connection that is guaranteed to fail authentication.
- Added regression coverage for local password-field precedence and authentication-failure reconnect suppression.


## 0.15.0 — PreloginConnectFix

- Fixed Desktop Connect misclassifying `system online + desktop absent` as a sleeping/offline Mac. Wake-on-LAN is now used only after the live P0/System channel is unavailable.
- Added bounded P0 `recover-prelogin` for `GUI_SESSION=NO`, with remote LoginWindow helper recovery and readiness polling.
- Removed invalid `gui/0` launchd handling for the LoginWindow agent. In-place updates performed while LoginWindow already exists now attempt to load the new agent into that session immediately, with a legacy `-S LoginWindow` fallback.
- Added pre-login process/readiness status and pre-login stdout/stderr to P0 logs.
- Added regression coverage for the exact `system online -> recover prelogin -> connect` decision so a green test suite can no longer silently accept the previous WOL behavior.


## 0.14.18 — PreloginRecovery + VideoRestartStability

- Adds a root **LoginWindow LaunchAgent** in `/Library/LaunchAgents` so ZorinMacBridge can start a pre-login Desktop endpoint after a normal macOS reboot, before the target user's Aqua session exists.
- The pre-login endpoint reuses the target user's existing ZMB TLS certificate, password verifier, server id, port and LAN discovery identity; no second pairing ceremony is introduced.
- Pre-login Desktop exposes only the physical macOS login-window display plus keyboard/mouse. Files and virtual 2K/4K remain disabled until the normal user session starts.
- Adds a native IOHIDSystem input shim for loginwindow (`IOHIDPostEvent`) bundled into the existing streamer dylib. This path is experimental and requires real-Mac verification because Secure Event Input behavior varies by macOS release.
- After login, the LoginWindow launchd session ends and the existing Aqua LaunchAgent takes over; the Zorin client auto-reconnects and restores the saved 1080p/2K/4K selection.
- Coalesces rapid FPS/Quality/Resolution changes before restarting video, adds a short cleanup grace, and serializes native video ownership on the Mac so multiple replacement streamers cannot overlap.
- Native video pipe reads monitor the TLS socket as well as the encoder pipe, so closing an old video channel for reconfiguration starts cleanup immediately instead of waiting up to 15 seconds.
- The Zorin client reports `Connected — macOS login window` and keeps Files disabled until Aqua handoff.
- No automatic login, FileVault disablement, lock-policy change, VNC/Screen Sharing dependency, or port 5900 requirement is added.
- FileVault preboot/EFI unlock remains outside this transport because macOS, launchd and ZMB are not running there.
## 0.14.17 — DesktopStateVideoControls

- Splits the top Desktop video controls into three independent settings: **Resolution**, **FPS**, and **Quality/bitrate**.
- Quality presets no longer carry a hidden width or frame-rate choice. `Adaptive` adapts bitrate only; FPS remains exactly the user-selected 24/30/60 value.
- **1080p** is the safe/default physical Mac display mode. **2K** and **4K** are real virtual macOS desktops at 2560×1440 and 3840×2160.
- 2K/4K now detach the built-in MacBook panel from the active workspace for the current login session using runtime-resolved `SLS/CGSConfigureDisplayEnabled`; switching back to 1080p restores it.
- Connect prepares the selected 2K/4K virtual display before starting video, but a virtual-display preparation failure no longer makes the authenticated Desktop/control connection fail.
- ScreenCaptureKit no longer falls back to the physical MacBook panel when a requested virtual display is not yet visible; it waits for the exact display ID.
- 2K→4K replacement is transactional: the working virtual display remains alive until the replacement is online, so a failed 4K transition does not intentionally destroy a working 2K session.
- Video-only errors such as `native streamer pipe stalled for 15s` keep the Desktop/control session and **Full Screen** controls active while H.264 retries.
- Adds a Pillow 10+ compatibility shim for older `pystray` builds that still reference `Image.ANTIALIAS`.
- No automatic login, FileVault, password, permanent display configuration, or lock-screen policy changes.

## 0.14.16 — PackagingHotfix

- Fixes the Linux release package so `zmb_shutdown.py` is installed alongside `linux_client_gtk.py`.
- Adds a staged-package import smoke test to the Linux release job, catching missing local Python modules before the `.deb` is built.
- Verifies that the generated `.deb` contains `usr/lib/zorinmacbridge/zmb_shutdown.py`.
- No runtime behavior changes outside packaging/release validation.

## 0.14.15 — ShutdownHardening

- Fixes the remaining GTK close hang: `close-request` no longer vetoes normal window destruction.
- Hides the window before any optional cleanup so the UI disappears immediately on explicit Quit.
- Removes the interactive `disconnect_remote()` path from application shutdown.
- Adds a shutdown-only Terminal SIGTERM path that does not call VTE reset/widget updates.
- Keeps Project/SSH and tray cleanup non-blocking.
- Adds a 1.25 s hard-exit watchdog for explicit Quit so a wedged native backend cannot leave the client process alive indefinitely.
- Adds shutdown regression coverage and a real-machine acceptance checklist.

## 0.14.14 — VirtualDisplayWake

- **2K** and **4K** are no longer crop/upscale modes. They create a real macOS virtual desktop at exactly **2560×1440** or **3840×2160** using the runtime-resolved private `CGVirtualDisplay` API.
- the virtual desktop is made primary only for the current login session so the menu bar and newly opened windows appear on the remote desktop; switching back to **Auto** removes it and restores the physical display arrangement
- ScreenCaptureKit captures the virtual display directly by display ID; if the private API is missing or changed, the video channel fails with an explicit error instead of silently falling back to fake upscaling
- mouse coordinates follow the active captured display dynamically, including the virtual display, instead of using cached MacBook-panel bounds
- ZorinMacBridge Server holds a runtime `caffeinate -i -w <server-pid>` assertion so ordinary display-off does not automatically put the whole Mac into idle system sleep; display sleep, lock screen and password policy are unchanged
- explicit/manual Sleep is still allowed and Wake-on-Connect remains the recovery path for a genuinely sleeping Mac
- fixed a Swift string-interpolation syntax defect in the native streamer source and added Swift parse validation to this release audit
- no automatic login, FileVault, password or lock-screen policy changes

## 0.14.13 — ResolutionWake

- explicit **2K** now means a real 2560×1440 H.264 output raster; **4K** means a real 3840×2160 output raster
- explicit 1080p/2K/4K uses a centered 16:9 ScreenCaptureKit source crop instead of preserving the MacBook panel aspect ratio
- mouse coordinates are inverse-mapped through the crop so pointer/click positions stay aligned with the Mac desktop
- video-only ScreenCaptureKit/H.264 failures no longer terminate the Desktop control/input session
- display sleep enters a video-waking retry state: the client repeatedly requests the restricted P0 `wake-display` action and reconnects only H.264
- `wake-display` now runs its short `caffeinate -u` assertion in the active GUI user's launchd context when available, with a system fallback for loginwindow
- Auto capture follows the Mac's current/main display; Zorin-side 2K/4K monitors are output targets, not assumed Mac-side displays
- no automatic-login, FileVault, password, or lock-screen policy changes

## 0.14.12 — DisplayWake

- keep Desktop control/input alive when ScreenCaptureKit temporarily reports no display; only the H.264 channel retries
- add restricted P0 `wake-display`, using a one-shot `caffeinate -u` user-activity assertion to turn an off display back on without changing login policy
- enumerate real macOS displays and expose them to the Zorin client
- Auto capture now prefers the largest external display instead of blindly taking `content.displays.first`
- add explicit display selection plus Auto / 1080p / 1440p / 4K stream resolution controls
- raise High to 20 Mbit/s and Ultra to 40 Mbit/s for sharper 2K/4K desktop text
- no automatic-login, FileVault or lock-screen policy changes
- Files: single-click now selects folders, double-click opens them, so **Download selected** can download an entire remote folder recursively

## 0.14.11 — WakeFullscreen

- Fixed GTK full-screen controls by restoring initialization of the full-screen state and synchronizing UI state with GTK4 `notify::fullscreened`.
- `Full Screen`, `Exit Full Screen`, `Esc`, and `Alt+Esc` no longer depend on a stale/uninitialized reconnect flag.
- Added Wake-on-Connect for saved paired Macs: Connect sends LAN wake packets and probes SSH/Desktop until the sleeping Mac wakes, then continues the normal Desktop connection.
- P0 status now reports the active network interface MAC address so the Zorin client can remember the wake target.
- Does not change macOS login, lock-screen, FileVault, or automatic-login policy.

## 0.14.10

- Repository layout cleanup only; no runtime feature changes.
- Moved regression tests to `tests/` and added `scripts/run-tests.sh` as the single CI/release test entry point.
- Moved acceptance checklists to `docs/acceptance/` and P0 developer notes to `docs/development/`.
- Updated README, contributor docs, CI, release workflow, and source-invariant tests for the new paths.

## 0.14.9 - Default input + explicit full-screen controls

- enabled keyboard and mouse forwarding by default for newly started clients and newly connected Desktop sessions, while keeping the capture switch as an explicit view-only override;
- removed double-click as a local full-screen gesture from both GTK and legacy clients, so remote double-clicks are always delivered to macOS;
- added an explicit **Full Screen** button in normal Desktop UI;
- kept **Exit Full Screen**, **Esc**, and **Alt+Esc** as the only local full-screen exit paths;
- focused the remote desktop automatically after connection when input forwarding is enabled.

## 0.14.8 - Keyboard reconnect + fullscreen escape regression fix

- route printable ASCII through the established physical `KEY` packet path instead of synthesizing physical CoreGraphics events inside `MacInput.text()`;
- restore the pre-0.14.6 Unicode-only `TEXT` injection path as fallback for non-ASCII text;
- isolate individual `KEY`/`TEXT` injection failures so one CoreGraphics error is logged and does not tear down the control channel;
- keep the 0.14.6 clipboard ordering and Linux-shortcut fixes;
- add regression coverage for `keypress -> reconnect` and the ASCII/Unicode split.
- keep full-screen state independent from connection/reconnect state so reconnects cannot disable Exit Full Screen;
- reserve both `Esc` and `Alt+Esc` locally in full-screen using GTK capture phase, and make the Exit Full Screen action idempotent even if internal state was previously desynchronised;
- extend regression coverage to reconnect-while-fullscreen and all three exit paths (button, Esc, Alt+Esc).

## 0.14.7 - Resilient updater and non-blocking shutdown

- Retried transient update download failures (`408`, `429`, `500`, `502`, `503`, `504`, socket timeouts/resets) up to four attempts with bounded backoff instead of failing the install after one gateway response.
- Partial update files are deleted before every retry; SHA-256 verification remains mandatory before installation.
- Closing the GTK window no longer waits for Project/SSH child processes or a pystray backend on the main thread.
- Explicit close invalidates late discovery/update callbacks and stops the tray asynchronously before quitting the GTK application.
- Update check/download/render paths are forbidden from owning application shutdown; the only GTK `app.quit()` call is centralized in the explicit close path.
- Added persistent lifecycle/crash logging at `~/.local/state/zorinmacbridge/client.log`, including process start, explicit shutdown reason, normal `atexit`, unhandled Python/thread exceptions, and Python faulthandler output for native crashes.
- Added regression coverage for `504 → retry → success`, partial-transfer retry, non-retriable `404`, asynchronous tray shutdown, and the GTK close path.

## 0.14.6 - Keyboard and clipboard shortcut reliability

- ordinary printable ASCII text now reaches macOS with the corresponding physical Mac virtual keycode instead of using keycode `0` for every Unicode event; this prevents iOS Simulator from interpreting unrelated keys as repeated `A`,
- the Unicode payload is retained alongside the physical keycode so normal macOS text controls continue to receive the intended character,
- `Ctrl+V` / `Command+V` clipboard bridging now waits for Linux → Mac clipboard synchronization before synthesizing the paste shortcut, eliminating the old race that could paste stale data or nothing,
- copy/cut clipboard pullback is scheduled from the key-down chord instead of depending on key-release order, so releasing Ctrl before `C`/`X` no longer loses synchronization,
- preserved the documented shortcut model: left Ctrl maps to macOS Command when **Linux shortcuts** is enabled, while right Ctrl remains real macOS Control,
- added keyboard/clipboard regression coverage for Simulator keycodes and shortcut ordering.

## 0.14.5 - Full-screen toolbar outside the remote image

- moved the full-screen status/quality/input/exit controls out of the remote-desktop overlay and into the application top layout,
- the Mac image now starts below the full-screen toolbar, so controls never cover the remote desktop,
- the normal Desktop status/hint row is hidden while full-screen is active to avoid two competing top bars,
- kept `Alt+Esc` and double-click-on-toolbar exit behavior while making **Exit Full Screen** permanently visible at the top,
- added regression coverage preventing the full-screen toolbar from being reintroduced as a `Gtk.Overlay` child.

## 0.14.4 - Desktop trust follows System pairing

- removed the editable/manual Desktop TLS SHA input from the GTK trust path; the field is now read-only diagnostics,
- Desktop Connect now resolves TLS identity only from persistent trust keyed by stable `server_id`,
- when upgrading from an older Mac helper, missing Desktop TLS identity is recovered automatically through the already-pinned SSH/System profile,
- an established Desktop TLS fingerprint is never silently replaced by a later P0/status refresh; identity changes are blocked as a security event,
- added regression coverage for first trust, remembered trust, old-helper recovery, and changed-certificate rejection.

## 0.14.3 - Remembered SSH identity and migration hotfix

- Reuse the SSH host key pinned during the first System pairing; reconfiguration automatically compares the live key with the remembered `known_hosts` entry and does not require another walk to the Mac when it matches.
- Fail closed if the Mac SSH host key actually changes and require explicit manual re-verification before replacing trust.
- During password-authenticated pairing, learn the Desktop TLS certificate fingerprint over that already-pinned SSH channel so migration from older P0 helpers does not require a second manual Desktop SHA.
- Canonicalize the macOS root update staging directory before the containment check, fixing the `/var/tmp` vs `/private/var/tmp` false `staged app path escaped the trusted update directory` failure.
- Clarify on the Mac that printed SSH fingerprints are for first enrollment/key-change recovery, not every reconnect.

## v0.14.2

- Unified pairing state across System, Desktop, Terminal, Projects, Xcode, and Files.
- P0 status now exposes the desktop TLS SHA-256 fingerprint only through the already-pinned SSH administration channel; successful System pairing stores that TLS identity automatically, so Desktop no longer requires a second manual fingerprint trust step.
- Successful pairing now propagates immediately to all feature controllers instead of refreshing only the System tab.
- Files now reports whether the Mac is paired versus whether the Desktop session is connected, and disables file actions until the authenticated Desktop channel is active.
- Added pairing-state regression coverage and release packaging checks for the new trust bridge.
- Version advanced to 0.14.2.

## v0.14.1

- Hotfix: repaired the native GTK update-result path on Zorin. `linux_client_gtk.py` now implements `_show_update_result()` instead of calling a missing method after GitHub Releases returns.
- Manual update checks now reject duplicate concurrent checks and carry a generation token so late/stale network results cannot overwrite a newer UI state.
- Added a 15-second main-loop watchdog around the GitHub release check; the client returns to a usable state even if DNS/TLS/socket behavior outlives urllib's per-operation timeout.
- The GTK periodic tick now catches and logs individual queue-handler failures instead of allowing one callback exception to remove the periodic GLib source and make the whole client appear hung.
- Added regression coverage for update-result handling and wired it into CI/release tests.
- Version advanced to 0.14.1.

## v0.14.0

- P7 setup hardening: the macOS app now has a prominent **Required setup for full Zorin operation** panel. Missing prerequisites are shown in red as `REQUIRED` instead of being buried in logs or optional-looking status text.
- The Mac UI evaluates one shared checklist for Screen Recording, Accessibility, the saved ZMB session password, P0 helper/daemons, Remote Login, SSH transport/host key, launch-at-login, and automatic desktop-server start.
- A startup alert lists every missing required item, and **Fix next REQUIRED item** routes the user to the appropriate repair/action instead of leaving the failure to appear later on Zorin.
- Added a dedicated **Open Remote Login Settings** action and stronger Remote Login/fingerprint errors. Remote Login OFF is now explicitly described as blocking pairing, System, Terminal, and Project actions.
- P0 installation repairs missing macOS SSH host keys with `ssh-keygen -A` when Remote Login is already enabled, then reports a hard `REQUIRED` state if fingerprints are still unavailable.
- Command-line setup now reports `SETUP_COMPLETE=0` plus a machine-readable `REQUIRED_SETUP_BEGIN/END` block whenever required setup is incomplete; it no longer prints a misleading all-good setup marker in that state.
- Added P7 regression tests and a real-Mac P7 acceptance checklist; version advanced to 0.14.0.

## v0.13.0

- P6: added layered read-only recovery diagnostics with explicit issue codes, severity, and auto/manual repair classification instead of a generic connection failure.
- Added **Recover all safe layers** to repair P0/update LaunchDaemon registration, regenerate/re-register the user LaunchAgent, clear stale updater locks, restore only a code-signature-verified update backup, and restart the desktop service with bounded verification.
- P0 autoheal now records bounded recovery events and only restarts a desktop process whose port remains unavailable for three consecutive recovery ticks, preventing restart loops on transient stalls.
- The system Bonjour advertiser now publishes its supervised PID so diagnostics can detect/recover a dead `_zorinmacbridge-admin._tcp` child.
- Zorin now classifies recovery-transport failures (host-key change, SSH refused/auth failure, network unreachable, timeout) even when Mac-side diagnostics cannot run. Host-key changes are never auto-accepted.
- Added update-daemon/plist, LaunchAgent-plist, partial-update/backup, stale-heartbeat, application-signature, desktop-process/port and Remote Login diagnostic guards.
- P6 keeps the P0 command surface closed: no arbitrary root shell or generic command execution was added.
- Added P6 parser/source regression tests and a real-Mac recovery acceptance checklist; version advanced to 0.13.0.

## v0.12.0

- P5: added an **Adaptive** remote-desktop profile that selects an initial tier from the visible viewport, downgrades quickly on client frame drops, and upgrades only after sustained stability.
- Video quality changes now reconnect only the dedicated H.264 channel; the authenticated control/file session remains connected.
- Client video buffering is latest-frame-only (`maxsize=1`) and GTK reports live stream telemetry (effective profile, FPS, receive rate, local drop percentage, and render-queue delay).
- GTK4 uses `Gdk.MemoryTexture` when available to avoid the Pixbuf conversion path, while retaining a compatibility fallback. Minimized/unmapped windows stop constructing textures.
- Added TCP_NODELAY to client/server sockets for lower interactive latency and lightweight server video telemetry packets.
- ScreenCaptureKit queue depth is reduced and VideoToolbox is configured for one-frame maximum delay plus explicit data-rate limits and two-second keyframe duration.
- The native Swift writer now permits at most one pending encoded access unit; additional frames are dropped instead of building an unbounded pipe backlog and increasing end-to-end latency.
- Added pure adaptive-policy tests and P5 source guards; version advanced to 0.12.0.

## v0.11.0

- P4: added safe one-way **Project Sync** from a configured Zorin project directory to the paired Mac project directory.
- Projects now store both local Zorin and remote macOS paths plus optional project-specific rsync exclude patterns.
- Added **Preview sync** with outgoing, protected remote, and remote-only classifications before any write occurs.
- Actual sync never uses `--delete`, uses `--update`, and additionally exact-excludes every path classified as a protected Mac-side conflict so equal-mtime divergent files are not overwritten.
- Project Sync reuses the pinned P3 user SSH identity and host key; it does not use the P0 root helper or add any sudo privilege.
- Default excludes protect `.git`, DerivedData/build directories, Xcode user state, and `.DS_Store`; sync runs outside GTK and supports cancellation.
- Linux packaging/source setup now includes `rsync`, `zmb_sync_backend.py`, and P4 regression tests.
- Version advanced to 0.11.0 for the P4 feature release.

## v0.10.0

- P3: added an integrated **Terminal** tab backed by GTK4 VTE and a pinned, dedicated Ed25519 key for the normal macOS user. This key is separate from the restricted P0 admin key and has no ZorinMacBridge sudoers privileges.
- P3: added a **Projects** tab with saved per-Mac project profiles and streamed `git status`, `git pull --ff-only`, Xcode inspect/open, build, test, clean, and cancellable long-running actions.
- System pairing can now optionally install/refresh the P3 user-shell key during the existing one-time macOS password login; revocation removes both P0 and P3 keys.
- Project actions use shell-safe argument quoting, disable interactive Git credential prompts, follow the persistent `server_id` across DHCP changes, and run outside the GTK main thread.
- Linux packaging now includes the P3 modules and GTK4 VTE runtime (`gir1.2-vte-3.91`, `libvte-2.91-gtk4-0`).
- Version advanced to 0.10.0 for the P3 feature release.

## v0.9.0

- P1 dashboard: added explicit machine-state classification, power/battery status, network-wake configuration, and a one-click **Recover desktop** action.
- P2 developer tools: added an integrated **Xcode** tab in the Zorin client with Xcode/toolchain status, Simulator discovery, boot/shutdown controls, and remote **Open Xcode / Open Simulator** actions.
- Xcode/Simulator operations run in the logged-in macOS user session through the existing restricted P0 command gateway; no arbitrary shell command was added.
- Linux release packaging now ships `zmb_xcode_page.py`; macOS P0 sudoers/ForcedCommand rules explicitly allow only the new closed command set.
- Version advanced to 0.9.0 for the next feature release after the 0.8.x P0 hardening series.

## v0.8.2

- Fixed P0 setup on current macOS when `systemsetup -setremotelogin on` is denied because the installer process lacks Full Disk Access. P0 now installs successfully and reports Remote Login as a one-time manual action instead of failing the whole setup.
- Privileged AppleScript commands now execute from `/` so an installer launched from a privacy-protected project directory cannot fail with `getcwd: Operation not permitted`.
- Terminal and GUI setup open Sharing settings when Remote Login still needs to be enabled and explicitly state that P0 does not need to be reinstalled afterwards.

## v0.8.1

- Fixed P0 installation on macOS when `launchctl bootstrap` races a preceding `bootout` and returns `Bootstrap failed: 5: Input/output error`.
- LaunchDaemon installation now validates plist files, waits boundedly for old jobs to leave the launchd namespace, retries bootstrap, and prints actionable diagnostics on failure.
- Added static regression coverage for the launchd bootstrap/retry path.

## v0.8.0

- Audit hardening pass: removed additional GUI-thread blocking operations, bounded discovery/UI/log workloads, added stale-operation invalidation, and added hard native-streamer pipe deadlines.
- Fixed races around Zeroconf discovery, reconnect queues, System-page worker callbacks, and regenerated pairing keys.
- Hardened P0 privileged file handling: user-owned LaunchAgent/SSH files are now written as the target user, temporary files are unpredictable, config secrets are root-only, and unattended update staging remains root-owned end-to-end.
- macOS update verification now checks the app bundle version against the selected release in addition to SHA-256, bundle id and code-signature verification.
- Completed a source-level reliability/security audit before the first v0.8.0 release: eliminated remaining GTK/Tk worker-state races, bounded client/server queues and pre-auth concurrency, and added hard deadlines to native video and update helper operations.
- Connection workers now use immutable UI-thread snapshots; high-rate mouse movement is coalesced so KEY-UP/button-up transitions cannot be crowded out by motion events.
- Hardened P0 root/user boundaries: LaunchAgent and `authorized_keys` changes are performed atomically as the desktop user, unattended update staging remains root-owned, and predictable/root-followed user-writable paths were removed.
- macOS update staging now verifies the release version embedded in the application bundle in addition to SHA-256, bundle identity and code-signature validity, with rollback retained on install/helper failures.
- Added bounded server pre-auth concurrency and validation caps for locally loaded password-verifier parameters to avoid resource/CPU stalls from malformed state.
- Hardened the Zorin GTK client against UI stalls: bounded GTK queue work per tick, asynchronous keyring reads, bounded Bonjour scans with a GUI watchdog, bounded log memory/marks, batched large-directory rendering, and video RGB preparation moved off the GTK thread.
- Added a P0 system Bonjour advertisement (`_zorinmacbridge-admin._tcp`) so a paired Mac can be rediscovered by its persistent `server_id` and current DHCP address even when the user-session desktop service is down.
- LAN discovery now shows the Bonjour hostname plus current IP and distinguishes desktop/system availability. The persistent `server_id` remains the machine identity; IP is transport only.
- Added fully remote Mac updates to the Zorin **System** tab: **Check update**, **Update now**, and **Automatic updates: Off / Notify only / Install automatically**.
- Added a separate six-hour macOS update LaunchDaemon so an unattended update can safely refresh the recovery daemon/helper without terminating its own transaction.
- Unattended updates reuse the normal GitHub release metadata, SHA-256 files, transport code-signature/bundle-id verification and persistent per-Mac designated requirement; the root helper makes a backup and rolls the app bundle back on installation/helper-refresh failure.
- After the one-time local P0 setup, remote/automatic Mac updates require no local monitor or repeated administrator-password prompt.
- macOS command-line installation now configures P0 and Remote Login (SSH) by default, verifies SSH enablement, and prompts for the separate ZorinMacBridge session password.
- Clarified throughout the UI that the first System pairing uses the macOS account/login password, while normal Desktop authentication uses the separate ZorinMacBridge session password.
- System/SSH actions are now fully fail-fast from the Zorin GUI: helper processes run in killable process groups, SSH has bounded connection/keepalive settings, and the GTK System page has its own watchdog so a missing reply cannot leave the application stuck in a permanent busy state.
- Linux clipboard helper processes now run off the GTK main thread, preventing a stalled `wl-copy`, `wl-paste`, or `xclip` process from freezing the UI.

- macOS support is now Apple Silicon (arm64) only; the Intel/x86_64 build and update path were removed to keep the project focused and release workflow simpler.
- Added P0 unattended administration directly to the main source tree; no runtime patcher, dpkg-divert, second launcher, or separate admin application is used.
- Added a native **System** page to the GTK4/libadwaita client for pairing, health/status, recovery, logs, sleep, restart, and shutdown.
- Added restricted Ed25519 device pairing over macOS Remote Login. The dedicated admin key is forced through `/usr/local/libexec/zmb-admin-ssh` and cannot open a general shell or SSH tunnel.
- Added an optional root LaunchDaemon that exposes no network listener and keeps a heartbeat plus recovery for the user-session ZorinMacBridge server.
- Added local **Enable / Repair** and **Disable P0** controls to the macOS server. The helper payload is bundled into the normal `.dmg`.
- Paired Macs remain visible in the normal Mac selector as `paired/offline` when the desktop mDNS service is unavailable, allowing recovery after a desktop-service failure.
- Linux release packaging now includes the P0 modules and `openssh-client`; macOS release packaging now embeds the P0 helper payload.
- Added P0 source/security tests and shell/plist validation to CI/release workflows.
- Existing manual GitHub update behavior is unchanged; P0 ships inside the normal `.deb`/`.dmg` release artifacts.

## v0.6.8

- Added a release-source gate that prints the checked-out commit, VERSION, tag, and SHA-256 of the macOS signing script before any package build starts.
- Tag-triggered releases now fail immediately when `v$VERSION` does not match the pushed tag.
- The release-source gate rejects the obsolete v0.6.6 transport signer (`signing nested native libraries ad-hoc`) and requires the v0.6.7+ outer-bundle-only signing markers.
- Added the same invariant check for runtime per-Mac signing so nested `.so`/`.dylib` files cannot silently be re-signed again.
- No change to the v0.6.7 signing algorithm itself; this release prevents an old checkout/tag from masquerading as a newer release.

## v0.6.7

- Fixed macOS release signing for PyInstaller bundles containing nested frameworks.
- Transport signing no longer re-signs individual `.so`/`.dylib` files after PyInstaller has signed enclosing bundles such as `Python.framework`.
- Per-Mac stable-DR signing now changes only the outer `.app` signature after updating `Info.plist`; nested code signatures are preserved.
- macOS signing preflight now includes a real nested-framework regression probe and deep verification.

## 0.6.6

- Fixed the macOS signing preflight failure `Requirement syntax error(s): line 1:1: unexpected token: designated`.
- `codesign --requirements` still receives an internal requirement set such as `designated => identifier ...`, which is correct when embedding a designated requirement into a signature.
- `codesign -R` now receives only the single requirement expression (`identifier ...` / `identifier ... and info[...] = ...`) because Apple documents that `-R` does **not** accept requirement-set tags such as `designated =>`.
- Applied the same split to transport verification, the stable per-Mac DR self-test, and runtime local-signing verification so the bug cannot reappear after CI during an actual Mac update.
- Added regression assertions that fail if a `designated =>` requirement-set file is passed to `codesign -R`.
- Kept the v0.6.5 certificate-free/keychain-free explicit-DR architecture unchanged; this release fixes verification syntax, not the identity model.

## 0.6.5

- Removed the certificate/keychain signing architecture that repeatedly failed on macOS runners with `Unknown critical cert extension` and keychain lookup errors.
- GitHub release transport signing is now purely ad-hoc and uses an explicit transport designated requirement; no ephemeral certificate, private key, keychain, trust mutation, or GitHub secret is involved.
- Installed macOS copies now use a stable explicit per-Mac designated requirement: fixed bundle identifier plus a random local `ZMBLocalIdentity` marker stored under Application Support.
- Every update injects the same marker into the staged app and ad-hoc signs it with the same explicit DR before replacing `/Applications`, eliminating certificate/keychain churn entirely.
- Added a fast macOS CI preflight that signs and verifies both a Mach-O probe and a minimal `.app` using the exact explicit-DR shape, and fails if the signature falls back to a build-bound `cdhash` DR.
- Removed all `security create-keychain`, PKCS#12 import, trust-root, private-key ACL, and local certificate code from the update/install path.

# Changelog

## 0.6.4

- Fixed the macOS signing certificate profile after v0.6.3 exposed `Unknown critical cert extension` and `codesign` failed on the first nested `.so`.
- Transport and persistent per-Mac signing certificates are now end-entity code-signing certificates: `CA:FALSE`, critical `digitalSignature` key usage, and critical `codeSigning` EKU; `keyCertSign`/`crlSign` were removed.
- `codesign` now selects the imported identity by SHA-1 and explicitly scopes lookup to the intended file keychain.
- Added a real Mach-O signing probe using a copy of `/usr/bin/true` before any release bundle is signed.
- Added a dedicated `macos-signing-preflight` GitHub Actions job so certificate/keychain failures happen before the macOS package build starts.
- Persistent local identities created with the obsolete v0.6.0-v0.6.3 CA-style certificate profile are automatically regenerated once before local signing.

## 0.6.3

- Fixed the macOS transport-signing validation bug in v0.6.2: `security find-key -a "$KEYCHAIN"` was malformed because `-a` means **application-label**, so the keychain path was being consumed as a label instead of searched as a keychain.
- Private-key validation now uses `security find-key -t private "$KEYCHAIN"`, which correctly searches the isolated keychain for a private key.
- PKCS#12 imports now explicitly use aggregate import type (`-t agg -f pkcs12`) so both the certificate and its private key are imported together.
- Applied the same fixes to the persistent per-Mac local-signing identity, preventing the identical validation/import bug from appearing later during an in-app update.
- Added regression assertions that fail if either the malformed `find-key -a` check or certificate-only PKCS#12 import returns.

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
