# ZorinMacBridge 0.15.52 — Scroll and wake/capture race

## Target

Fix two physically observed Aqua problems without changing working LoginWindow HID:

1. Scroll packets reach the Mac server but do not move the target application.
2. After display sleep, the server requests wake but ScreenCaptureKit can see zero displays on its first startup attempt.

## Changes

- Aqua `MacInput.scroll()` now constructs the wheel event as a two-axis pixel event and posts it at `kCGSessionEventTap`, matching Chromium Remote Desktop's macOS implementation. It does not override the event location or flags.
- Aqua ScreenCaptureKit startup retries an initially empty display list every 100 ms for at most 50 attempts before reporting failure.

## Intentionally unchanged

- LoginWindow DriverKit virtual HID and pre-login scroll path.
- Mouse motion/buttons and keyboard injection.
- TLS, pairing, authentication and wire packet format.
- VideoToolbox encoder lifecycle.

## Runtime verification

Static/unit tests cannot prove macOS accepts and delivers a posted CoreGraphics wheel event to a physical application. Physical Mac verification remains required.
