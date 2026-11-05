from __future__ import annotations

"""Pure helpers for P5 adaptive desktop streaming.

The GTK/Tk clients use these helpers from their video worker threads. Keeping
policy separate from UI/network code makes adaptation deterministic and easy to
test without a display or a Mac.
"""

ADAPTIVE_TIERS = (
    ('Eco', {'fps': 15, 'max_width': 1280, 'bitrate': 3_000_000}),
    ('Efficient', {'fps': 24, 'max_width': 1600, 'bitrate': 5_000_000}),
    ('Balanced', {'fps': 30, 'max_width': 1920, 'bitrate': 8_000_000}),
    ('Sharp', {'fps': 30, 'max_width': 2560, 'bitrate': 12_000_000}),
)


def initial_tier_for_viewport(width: int | float | None) -> int:
    """Choose a conservative starting tier for the visible desktop area."""
    try:
        value = int(width or 0)
    except (TypeError, ValueError):
        value = 0
    if value <= 0:
        return 2
    if value <= 1100:
        return 0
    if value <= 1450:
        return 1
    if value <= 2100:
        return 2
    return 3


def profile_for_tier(index: int) -> tuple[str, dict]:
    idx = max(0, min(len(ADAPTIVE_TIERS) - 1, int(index)))
    name, profile = ADAPTIVE_TIERS[idx]
    return name, dict(profile)


def choose_adaptive_step(*, decoded: int, dropped: int, stable_windows: int, current_tier: int,
                         viewport_width: int | float | None) -> int:
    """Return -1/0/+1 for the next adaptive tier.

    Downgrades are intentionally fast when the client's latest-frame queue is
    dropping work. Upgrades require several stable windows to avoid oscillation.
    The visible viewport acts as a ceiling so a small window does not make the
    Mac encode pixels that GTK will immediately scale down.
    """
    current = max(0, min(len(ADAPTIVE_TIERS) - 1, int(current_tier)))
    ceiling = initial_tier_for_viewport(viewport_width)
    if current > ceiling:
        return -1
    if decoded < 20:
        return 0
    ratio = max(0.0, float(dropped) / max(1, decoded))
    if ratio >= 0.12 and current > 0:
        return -1
    if ratio <= 0.01 and stable_windows >= 4 and current < ceiling:
        return 1
    return 0
