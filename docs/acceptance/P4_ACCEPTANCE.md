# ZorinMacBridge 0.11.0 — P4 Project Sync acceptance checklist

Run this on the real Zorin + Apple Silicon Mac pair after CI builds 0.11.0.

## Prerequisites

1. P0/P3 pairing works and **Terminal / Project Actions** is enabled for the Mac.
2. The project already exists on the Mac at the configured remote directory.
3. `rsync` is installed on Zorin and `/usr/bin/rsync` exists on the Mac.
4. Use a disposable/test project for the first validation pass.

## Configure a sync profile

1. Open **Projects → Add project** or edit an existing project.
2. Set **Local Zorin project directory** to an absolute Linux path.
3. Set **Remote macOS project directory** to the matching absolute Mac path.
4. Add any project-specific extra excludes, for example `node_modules/` or `Secrets.local`.
5. Save the profile and confirm both paths are shown in the project details.

## Preview

1. Modify or add a harmless file on Zorin.
2. Press **Preview sync**.
3. Confirm the file appears under **OUTGOING**.
4. Confirm `.git`, `DerivedData`, `.build`, `build`, `xcuserdata`, `*.xcuserstate`, and `.DS_Store` are excluded by default.
5. Create a file only on the Mac. Preview again and confirm it appears as **REMOTE-ONLY / UNTOUCHED**.
6. Modify the same file independently on both sides, making the Mac copy newer or otherwise divergent. Preview again and confirm it appears as **PROTECTED ON MAC**.

## Safe push

1. Press **Sync Zorin → Mac**.
2. If protected Mac changes exist, confirm the warning and choose **Sync safe changes**.
3. Confirm outgoing files arrive on the Mac.
4. Confirm remote-only files remain present.
5. Confirm protected Mac files are unchanged.
6. Confirm no `.git` directory or excluded build artefacts are copied from Zorin.
7. Confirm the GTK UI stays responsive while sync output streams.
8. Start a larger sync and press **Cancel running action**; confirm rsync stops and the UI recovers.

## DHCP / identity regression

1. Change the Mac DHCP address or reconnect it to the LAN.
2. Run **Find Macs**.
3. Reopen the same project profile.
4. Confirm Project Sync follows the persistent `server_id` and works without recreating the profile.

## Regression

- Desktop streaming still works.
- Files still work.
- System/P0 recovery still works.
- Xcode/Simulator still works.
- Terminal still works.
- Existing Git/Xcode Project Actions still work.
- No Project Sync operation uses the P0 root helper or adds sudo privileges.
