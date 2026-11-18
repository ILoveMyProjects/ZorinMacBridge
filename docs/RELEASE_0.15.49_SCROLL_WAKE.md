# 0.15.49 — Scroll & display wake (source release)

## TARGET and scope

Fix two reported regressions on Mac Desktop: scroll-wheel and touchpad scrolling
from the GTK4 client, and a black video view when a previously connected Mac
has powered off its display. Preserve working reboot → LoginWindow input,
DriverKit keyboard/mouse, TLS, and the 0.15.48 VideoToolbox crash fix.

## Implementation

- GTK4 listens to smooth as well as discrete scroll. Fractional deltas are
  accumulated to signed integer protocol wheel steps; horizontal scrolling
  is also retained. `SCROLL` wire format is unchanged.
- Aqua CoreGraphics positions the scroll event at the actual macOS cursor and
  retains session modifier flags. The server logs rate-limited scroll RX.
- After Aqua Desktop authentication, the Linux client makes a one-shot,
  rate-limited, nonblocking P0 `wake-display` request. It never equates a dark
  wallpaper with an asleep display and does not restart capture on each frame.
- The Mac independently checks `CGDisplayIsAsleep` before video capture and
  requests user activity asynchronously via `caffeinate -u` only when asleep.
- The earlier `display_sleeping` fallback continues to work when capture
  reports that no display is available.

## Physical acceptance (NIEZWERYFIKOWANE)

1. From Zorin, scroll a browser/document vertically and horizontally if supported.
   Inspect client `Mouse scroll TX` and Mac `scroll RX` diagnostics.
2. Turn off the MacBook display without rebooting. Connect from Zorin and
   verify that the image appears without a manual restart.
3. Repeat with the display already awake and with a dark wallpaper: one-shot
   wake should not become a repeated loop or disconnect keyboard/mouse.
4. Reboot, connect at LoginWindow, log in and confirm previous keyboard/mouse
   and VideoToolbox transitions remain working.

This is a source-and-static test release. The actual macOS CoreGraphics and
Display Power Management behavior cannot be verified in a Linux container.
A `CGEventPost` submission is not a receipt from the target application.
