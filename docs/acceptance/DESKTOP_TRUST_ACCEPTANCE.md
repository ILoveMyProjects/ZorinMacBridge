# ZorinMacBridge 0.14.4 Desktop Trust acceptance

Do not tag v0.14.4 until these checks pass on the real Zorin + Mac pair.

1. Pair the Mac once in **System** and confirm pairing succeeds.
2. Open **Desktop**. The Desktop trust field is read-only and already populated; there is no editable SHA field.
3. Connect Desktop without copying or typing any TLS SHA.
4. Quit and restart the Zorin client. Re-select the same Mac and connect Desktop without any SHA prompt.
5. Reboot/change DHCP address on the Mac. Re-discover it by the same `server_id`; Desktop still connects using remembered trust.
6. Test migration with a Mac helper that predates `desktop_tls_fingerprint`: Desktop trust is recovered automatically over the existing pinned SSH profile.
7. Negative test: present a different Desktop TLS certificate while retaining the same SSH host key. The client must block Desktop with an identity-change warning and must not offer a manual SHA override.
8. Terminal/System remain usable independently of a logged-in GUI session where macOS permits SSH/P0.
