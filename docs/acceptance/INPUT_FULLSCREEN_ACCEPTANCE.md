# ZorinMacBridge 0.14.9 — Keyboard + fullscreen acceptance

Run on a real Zorin + Mac pair after installing the same 0.14.9 build on both sides.

1. Connect Desktop and confirm keyboard & mouse capture is ON without changing the switch.
2. Press `a`, `b`, `c`, digits and punctuation in a normal Mac text field and in iOS Simulator. The control channel must remain connected; no `Reconnecting…` is allowed from a normal keypress.
3. Double-click inside the Mac desktop. It must be delivered remotely and must not change full-screen state.
4. Enter fullscreen using the **Full Screen** button.
5. Trigger a reconnect. After reconnect, the window must remain in a coherent fullscreen state.
6. Exit using **Exit Full Screen**. Re-enter and exit using **Esc**. Re-enter and exit using **Alt+Esc**.
7. Verify remote Escape is still sent normally when the client is not fullscreen.
8. Verify copy/paste shortcuts still work.
9. Disable **Capture keyboard & mouse** and confirm view-only mode; enable it again and confirm input resumes.
