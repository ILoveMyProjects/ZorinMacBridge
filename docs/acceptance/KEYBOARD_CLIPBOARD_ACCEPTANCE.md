# ZorinMacBridge 0.14.6 — Keyboard / Clipboard acceptance

Run this on a real Zorin client and the updated macOS server.

## Simulator typing

1. Connect Desktop and enable **Capture keyboard & mouse**.
2. Open Xcode's iOS Simulator and focus a normal text field inside an iOS app.
3. Type: `abcxyz 123 !? _+-=[]{};:'\",.<>/` from the Zorin keyboard.
4. Confirm the Simulator receives the actual characters instead of repeated `A`.
5. Hold Shift and type several letters; confirm uppercase text is correct.
6. Confirm Return, Backspace, arrows, Tab and Escape still behave as before where the target accepts them.

## Linux shortcuts and clipboard

With **Linux shortcuts** enabled:

1. Put unique text in the Zorin clipboard.
2. Focus TextEdit / Xcode editor / Simulator text field on the Mac and press **Left Ctrl+V** on Zorin.
3. Confirm the current Zorin clipboard is pasted, not an older Mac clipboard value.
4. Select text on the Mac and press **Left Ctrl+C** on Zorin.
5. Immediately paste into a local Zorin text editor and confirm the copied Mac text arrived in the Linux clipboard.
6. Repeat copy while releasing Left Ctrl before `C`; clipboard synchronization must still complete.
7. Press **Right Ctrl+C** in a Mac Terminal command; confirm it is delivered as real macOS Control+C rather than Command+C.

## Regression

- mouse input still works,
- full-screen toolbar remains outside the remote image,
- explicit Clipboard Linux → Mac / Mac → Linux buttons still work,
- no modifier remains stuck after leaving full-screen or disabling input capture.
