# ZorinMacBridge 0.15.53 — Aqua Scroll Event2 Fix

## Target
Restore Aqua/user scroll delivery without changing working mouse movement, clicks, keyboard, LoginWindow DriverKit input, video, or display-wake behavior.

## Confirmed from 0.15.52 runtime logs
- Linux/GTK scroll reaches the Mac control channel.
- The server executes the Aqua scroll dispatch path.
- 0.15.52 display wake retry succeeds before ScreenCaptureKit startup.
- macOS still does not visibly scroll after the Python `ctypes` variadic CoreGraphics call.

## Change
Aqua scroll now uses `CGEventCreateScrollWheelEvent2`, the fixed-signature CoreGraphics API, with pixel units, two axes and `kCGSessionEventTap`.

This removes the variadic ABI boundary from Python/ctypes on Apple Silicon. The old `CGEventCreateScrollWheelEvent` function is variadic; Python documents a distinct variadic calling convention on Apple ARM64.

## Runtime marker
`dispatch=SESSION_PIXEL_EVENT2_SUBMITTED_UNVERIFIED`

`SUBMITTED_UNVERIFIED` remains intentional: `CGEventPost` has no delivery acknowledgement.

## Not changed
- GTK scroll collection/protocol
- Aqua mouse move/buttons/keyboard
- DriverKit/LoginWindow input
- ScreenCaptureKit/VideoToolbox
- 0.15.52 wake retry
