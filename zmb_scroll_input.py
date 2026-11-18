"""Remote scrolling: accumulate fractional GTK4 wheel/touchpad deltas.

GTK's scroll callback reports floating-point coordinates. Per-event rounding
silently discards small smooth-scroll motions. Keep the fractional remainder
until enough motion exists to send a whole wheel unit over the ZMB protocol.
"""
from __future__ import annotations

import math


class ScrollAccumulator:
    def __init__(self, scale: float = 3.0) -> None:
        self.scale = float(scale)
        self._x = 0.0
        self._y = 0.0

    def reset(self) -> None:
        self._x = 0.0
        self._y = 0.0

    def feed(self, dx: float, dy: float) -> tuple[int, int]:
        x, y = float(dx), float(dy)
        if not math.isfinite(x) or not math.isfinite(y):
            return 0, 0
        # Mac scrolling direction is the reverse of GTK wheel delta.
        self._x -= x * self.scale
        self._y -= y * self.scale
        sx = max(-127, min(127, math.trunc(self._x)))
        sy = max(-127, min(127, math.trunc(self._y)))
        self._x -= sx
        self._y -= sy
        return sx, sy
