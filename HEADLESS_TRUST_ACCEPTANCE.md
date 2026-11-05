# ZorinMacBridge 0.14.3 acceptance

Run on a real Zorin + Mac pair before release.

1. Pair the Mac once from **System** and manually verify the SSH fingerprint only on first enrollment.
2. Close/reopen the Zorin client. Open **Pair / Configure** again. It must automatically show **Remembered SSH identity verified** without requiring a Mac-side comparison.
3. Reboot the Mac or change its DHCP address. The same remembered SSH identity must still verify because trust is tied to key material/server identity, not the IP address.
4. Confirm Terminal/System actions continue to use the saved profile without another host-key prompt.
5. On a Mac still running an older P0 helper, re-pair from Zorin using the macOS account password. Desktop TLS trust must be learned during that same SSH session; Desktop must not request a second manual SHA fingerprint.
6. Run **System → Update now** on macOS. A valid staged app under `/private/var/tmp/...` must not fail with `staged app path escaped the trusted update directory`.
7. Security regression: replace/regenerate the Mac SSH host keys in a controlled test. Zorin must refuse automatic trust and require explicit re-verification.

Do not tag v0.14.3 until these real-device checks pass.
