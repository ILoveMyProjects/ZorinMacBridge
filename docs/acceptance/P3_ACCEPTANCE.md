# ZorinMacBridge 0.10.0 — P3 acceptance checklist

Run this on the real Zorin + Apple Silicon Mac pair after CI builds 0.10.0.

## Pairing upgrade

1. Open **System → Pair / Configure** for the already paired Mac.
2. Leave **Enable Terminal / Project Actions** checked.
3. Verify the pinned SSH host fingerprint and enter the normal macOS account password once.
4. Confirm System status still works after pairing.
5. Confirm the pairing line says **terminal enabled**.

## Terminal

1. Open **Terminal**.
2. Press **Connect**.
3. Confirm an interactive prompt for the paired macOS user appears.
4. Run `whoami`, `pwd`, `uname -m`, and `xcode-select -p`.
5. Confirm `sudo -n true` does not gain any new permission merely because the Terminal key exists.
6. Disconnect and reconnect the terminal.
7. Change the Mac DHCP lease/IP, run **Find Macs**, and reconnect without re-pairing.

## Projects

1. Open **Projects → Add project**.
2. Save an absolute macOS project directory, optional `.xcworkspace`/`.xcodeproj`, scheme, configuration, and destination.
3. Run **Git status** and verify streamed output.
4. Run **Git pull --ff-only** on a test repository; confirm it fails instead of prompting if Git credentials are unavailable.
5. Run **Inspect Xcode**.
6. Run **Build** and confirm output streams while the GTK UI remains responsive.
7. Press **Cancel running action** during a build and confirm the SSH process stops and the UI recovers.
8. Run **Test** and **Clean** on a disposable/test project.
9. Run **Open in Xcode** and confirm Xcode opens in the logged-in Mac GUI session.

## Revocation

1. Return to **System** and choose **Revoke Zorin device**.
2. Confirm both the restricted P0 admin key and the P3 user-shell key stop authenticating.
3. Confirm local private-key files for the revoked pairing are removed from the Zorin profile directory.

## Regression

- Desktop streaming still works.
- Files still work.
- System/P0 recovery still works.
- Xcode/Simulator tab still works.
- Auto-update/recovery still work.
- No action above blocks the GTK main thread.
