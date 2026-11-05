# ZorinMacBridge 0.13.0 — P6 Recovery acceptance checklist

Run these checks on the real Zorin + Apple Silicon Mac pair after CI builds 0.13.0. Static/container tests cannot validate launchd state transitions, real SSH outages, Bonjour recovery, or a real interrupted update.

## 1. Healthy baseline

1. Pair System administration and confirm Remote Login is enabled.
2. Open **System → Run diagnostics**.
3. Expected: `HEALTHY` (an informational `GUI_SESSION_ABSENT` is acceptable only when no Aqua user session is logged in).
4. Open **Logs** and confirm a `zmb recovery events` section exists.

## 2. Desktop process recovery

1. On the Mac, quit ZorinMacBridge Server while leaving the GUI user logged in.
2. Within about one P0 recovery tick, the daemon should start it again.
3. Run diagnostics. `DESKTOP_PROCESS_DOWN` should not persist.
4. Verify normal Desktop reconnect works without re-pairing.

## 3. LaunchAgent recovery

On the Mac:

```bash
launchctl bootout gui/$(id -u)/com.ilovemyprojects.zorinmacbridge.server || true
```

Run **System → Run diagnostics**, then **Recover all safe layers**.
Expected: the LaunchAgent is regenerated/re-registered with bounded launchd retry and desktop service returns.

## 4. System Bonjour recovery

On the Mac:

```bash
sudo cat /var/run/zorinmacbridge-admin/advertiser.pid
sudo kill "$(sudo cat /var/run/zorinmacbridge-admin/advertiser.pid)"
```

The system daemon should recreate the `_zorinmacbridge-admin._tcp` advertiser on its next loop. Run diagnostics after ~20 seconds; `SYSTEM_MDNS_DOWN` should be gone.

## 5. P0 LaunchDaemon recovery

On the Mac:

```bash
sudo launchctl bootout system/com.ilovemyprojects.zorinmacbridge.admin
```

The restricted SSH helper remains installed independently of that daemon. From Zorin run **Run diagnostics**; expect `ADMIN_DAEMON_DOWN` / stale heartbeat. Then run **Recover all safe layers** and verify the system service and Bonjour advertisement return.

## 6. Stale updater lock

On the Mac:

```bash
sudo mkdir -p /var/run/zorinmacbridge-admin/update.lock
printf '999999\n' | sudo tee /var/run/zorinmacbridge-admin/update.lock/pid >/dev/null
```

Run diagnostics; expect `UPDATE_LOCK_STALE`. Run **Recover all safe layers**; the stale lock should be removed.

## 7. Verified backup restoration

This intentionally manipulates the installed app; do it only while you have local access available for the acceptance test.

```bash
sudo rm -rf "/Applications/ZorinMacBridge Server.app.zmb-update-backup"
sudo ditto "/Applications/ZorinMacBridge Server.app" "/Applications/ZorinMacBridge Server.app.zmb-update-backup"
sudo mv "/Applications/ZorinMacBridge Server.app" "/Applications/ZorinMacBridge Server.app.p6-test-missing"
```

From Zorin run **Run diagnostics**; expect repairable `APP_MISSING`. Run **Recover all safe layers**. The verified backup should be restored to `/Applications/ZorinMacBridge Server.app` before desktop recovery is attempted.

After the test:

```bash
sudo rm -rf "/Applications/ZorinMacBridge Server.app.p6-test-missing"
```

## 8. No trusted application available

Do not destructively simulate this on the only usable installation. Review the static test instead: P6 must skip desktop start/LaunchAgent recovery when the installed app does not verify and no verified backup exists. The report must contain `APP_UNTRUSTED` / `APP_MISSING` or `APP_SIGNATURE_INVALID`, never execute an arbitrary replacement.

## 9. Transport-side classification

Optional, while local access is still available:

- Temporarily turn **Remote Login** off: Zorin System status should classify the failure as SSH unavailable/refused rather than pretending P0 diagnostics ran.
- Turn it back on and confirm the existing pinned key still works.
- Do **not** auto-accept a changed host key. A host-key mismatch must be shown as `SSH_HOST_KEY_CHANGED` and require local verification/re-pairing.

## 10. Update scheduler

Run diagnostics with the update LaunchDaemon loaded. Then inspect:

```bash
sudo launchctl print system/com.ilovemyprojects.zorinmacbridge.update
```

If registration is deliberately removed, diagnostics should report `UPDATE_DAEMON_DOWN` and **Recover all safe layers** should reload the existing valid plist without forcing an update immediately.

## 11. Regression workload

With Xcode + Simulator running, verify all earlier functionality still works:

- Desktop + Adaptive video
- Files / clipboard / input
- System status and recovery
- Xcode / Simulator controls
- Terminal
- Projects actions
- P4 preview/sync

Do not tag `v0.13.0` until the real-Mac checks above pass.
