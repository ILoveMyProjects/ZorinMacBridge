# ZorinMacBridge 0.14.9 — Default input / explicit fullscreen acceptance

Run on a real Zorin + Mac pair with the same 0.14.9 build on both sides.

1. Start the Zorin client and connect Desktop without touching the Capture keyboard & mouse switch. Confirm the switch is ON by default.
2. Click once inside the Mac desktop and type `abc 123`. Confirm input is delivered immediately.
3. Move the mouse, click, drag, and scroll. Confirm all input is delivered immediately.
4. Double-click a Finder item or macOS UI element. Confirm the double-click is delivered to the Mac and does not enter or exit full screen.
5. Click **Full Screen**. Confirm the window enters full screen.
6. Double-click inside the remote image while full screen. Confirm it is delivered to the Mac and does not exit.
7. Exit with **Esc**. Re-enter and exit with **Alt+Esc**. Re-enter and exit with **Exit Full Screen**.
8. Turn **Capture keyboard & mouse** OFF. Confirm the desktop becomes view-only. Turn it ON again and confirm forwarding resumes.
9. Disconnect/reconnect. Confirm input remains ON unless the user explicitly turned the switch OFF in the current client session.

Do not tag v0.14.9 until this real-device checklist passes.
