# ZorinMacBridge 0.15.51 — Aqua scroll Session Event Tap fix

## Target
Restore wheel scrolling in the authenticated Aqua desktop without changing working mouse movement, clicks, keyboard, DriverKit LoginWindow input, video, wake, TLS or pairing.

## Change
`MacInput.scroll()` now posts the CoreGraphics wheel event at `kCGSessionEventTap` (`1`). Other `MacInput` events continue to use the existing `kCGHIDEventTap` default.

The GTK controller, scroll accumulator and SCROLL packet format are unchanged from 0.15.50.

## Verification
Automated tests check that scroll events select Session Event Tap and that ordinary mouse movement retains the default HID tap. Physical macOS delivery cannot be proven in this Linux test environment and must be verified on the target Mac.
