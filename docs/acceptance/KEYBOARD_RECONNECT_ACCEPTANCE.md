# ZorinMacBridge 0.14.8 — Keyboard reconnect acceptance

Run on the real Zorin + Mac pair before tagging the release.

1. Connect Desktop and enable `Capture keyboard & mouse`.
2. In a normal macOS text field, type: `abc xyz 123 !?`.
   - Expected: characters appear correctly.
   - Expected: no `Reconnecting`, no Desktop disconnect.
3. Open iOS Simulator and focus a text field.
4. Type: `abcxyz 123 !?`.
   - Expected: distinct characters, not repeated `A`.
   - Expected: no reconnect on any key press.
5. Hold a key to exercise repeat.
   - Expected: repeat works without dropping the control channel.
6. Test Left Ctrl+C / Left Ctrl+V in Linux-shortcuts mode.
   - Expected: Mac copy/paste works and clipboard contents are current.
7. Test Right Ctrl in Terminal.
   - Expected: remains real macOS Control.
8. Check Mac server log while typing.
   - Expected: no `[control] keyboard event rejected` / `[control] text event rejected` lines in normal use.
   - If one appears, the session must remain connected; capture the exact line for diagnosis.
9. Disconnect/reconnect Desktop once and repeat steps 2–4.

Do not tag `v0.14.8` until this real-device checklist passes.
