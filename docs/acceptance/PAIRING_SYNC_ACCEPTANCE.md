# v0.14.2 Pairing Sync Acceptance — real Mac + Zorin

Run this after installing the same v0.14.2 source/release on both machines.

1. On the Mac confirm P0/Remote Login are ready and the desktop server is running.
2. On Zorin select the Mac, open **System**, choose **Pair / Configure**, read and verify the SSH fingerprint once, and pair with **Enable Terminal / Project Actions** enabled.
3. Pairing must finish without asking for a second Desktop/TLS fingerprint.
4. Return to **Desktop**. The TLS fingerprint field should already contain the Mac certificate pin learned through P0. Enter only the ZMB session password if it is not already saved, then Connect.
5. Open **Terminal** without reselecting the Mac. It should report `User terminal ready: <user>@<host>` and Connect should be enabled when VTE is available.
6. Open **Projects**. It should no longer say to pair/re-pair the Mac; project actions should use the installed user SSH key.
7. Open **Xcode**. It should report the Mac as paired and allow Refresh/Open actions.
8. Open **Files** before Desktop is connected: it should say `Mac paired ✓ · Desktop is not connected`. After Desktop connects, file actions should become enabled.
9. Restart the Zorin client. Re-select the same persistent `server_id`: Desktop TLS trust and paired feature state must reload without another fingerprint prompt.
10. Change/renew the Mac DHCP address, rediscover it, and verify the same `server_id` keeps trust; P0 SSH host-key pinning must still reject an impostor using a different SSH host key.

Do not tag/release v0.14.2 until this real-device flow passes.
