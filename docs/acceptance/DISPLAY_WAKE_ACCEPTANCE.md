# 0.14.12 DisplayWake acceptance

Historical checklist for the 0.14.12 display-wake work.

1. With the Mac awake, connect Desktop and confirm the Display selector reports the displays that are actually attached to the Mac. Do not infer Zorin-side monitors here.
2. Put only the Mac display to sleep while leaving the machine reachable. Desktop control must remain connected and the client should request `wake-display`.
3. Confirm the existing login screen/desktop becomes visible again when ScreenCaptureKit sees the display.
4. Put the whole Mac to sleep and use Connect on the saved Mac. Wake-on-Connect should wake the machine, then `wake-display` should wake the screen.
5. Verify no automatic login, FileVault, or lock-screen policy is changed.
6. Regression: Full Screen, Exit Full Screen, Esc/Alt+Esc, keyboard/mouse capture, clipboard, reconnect and recursive folder download still work.
