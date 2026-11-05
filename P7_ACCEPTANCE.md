# P7 / v0.14.0 Required Setup Acceptance — real Mac

Run this checklist on the actual Apple Silicon Mac before tagging `v0.14.0`.

## 1. Clean/incomplete setup must be impossible to miss

1. Start from a Mac where at least one prerequisite is missing (Remote Login OFF is sufficient).
2. Launch **ZorinMacBridge Server**.
3. Confirm the **Required setup for full Zorin operation** panel is visible without opening logs.
4. Confirm every missing prerequisite is displayed in red as `REQUIRED` with the affected feature named.
5. Confirm a one-time **Required setup incomplete** startup alert lists the same missing items.

## 2. Remote Login required flow

1. Turn **System Settings → General → Sharing → Remote Login** OFF.
2. Click **Refresh REQUIRED setup**.
3. Confirm the panel says Remote Login is required for pairing/System/Terminal/Projects.
4. Click **Fix next REQUIRED item** (after any earlier missing P0 item has been repaired).
5. Confirm Sharing settings opens and the UI explains that Remote Login must be ON.
6. Turn Remote Login ON and click **Refresh REQUIRED setup**.
7. Confirm TCP 22 is accepted and the Remote Login item clears.

## 3. Missing SSH host key repair

On a disposable/test Mac only, back up the existing host public keys first if you intentionally manipulate them.

1. With Remote Login ON, verify `/etc/ssh/ssh_host_*_key.pub` exists normally.
2. Exercise **Enable / Repair** and confirm `ssh-keygen -A` does not damage existing keys.
3. Confirm **Copy SSH fingerprint** returns at least one SHA-256 fingerprint.
4. If a test environment can safely reproduce missing host keys, confirm **Enable / Repair** regenerates them and the red `SSH host public key` requirement clears.

## 4. TCC requirements

1. Revoke/disable Screen Recording for this exact app build.
2. Refresh and confirm **Screen Recording permission** is red/REQUIRED and says video is affected.
3. Use **Fix next REQUIRED item** and confirm the expected prompt/settings path appears.
4. Repeat for Accessibility and confirm mouse/keyboard impact is shown.
5. Restart the app after granting permissions if macOS requires it, then confirm both items clear.

## 5. Desktop authentication/startup requirements

1. On a fresh profile without a saved ZMB password verifier, confirm the session-password requirement is red.
2. Enter a password of at least 12 characters, keep **Remember password verifier on this Mac** enabled, and start the server once.
3. Confirm the requirement clears.
4. Disable **Launch ZorinMacBridge Server when this macOS user logs in** and confirm it becomes red/REQUIRED.
5. Disable **Automatically start the server when the app launches** and confirm it becomes red/REQUIRED.
6. Use **Fix next REQUIRED item** and verify both startup settings are restored.

## 6. Terminal installer result

Run the normal macOS installer/setup with at least Remote Login or a TCC permission missing.

Expected output includes:

```text
SETUP_COMPLETE=0
REQUIRED_SETUP_BEGIN
REQUIRED=...
REQUIRED_SETUP_END
```

It must not print `SETUP_OK=1` while required items are missing. When every requirement is satisfied, rerun setup/status validation and confirm:

```text
SETUP_COMPLETE=1
SETUP_OK=1
```

## 7. Zorin end-to-end

After the Mac panel is green/READY:

1. Zorin **Find Macs** discovers the Mac.
2. **System → Pair / Configure → Read fingerprint** returns the same SSH SHA-256 fingerprint shown on the Mac.
3. Pairing succeeds with the macOS account password.
4. System status works.
5. Terminal connects.
6. Project actions work.
7. Desktop connection provides video plus keyboard/mouse input.

Do not tag `v0.14.0` until this real-Mac checklist passes.
