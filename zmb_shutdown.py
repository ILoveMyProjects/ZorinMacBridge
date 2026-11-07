from __future__ import annotations

import os
import threading
import time
from collections.abc import Callable


def arm_hard_exit_watchdog(delay: float = 1.25, *, exit_func: Callable[[int], None] = os._exit) -> threading.Thread:
    """Force an explicit GUI quit to finish even if a native backend wedges.

    The watchdog thread is daemonized, so a normal application exit cancels it
    automatically when the interpreter terminates.  It is armed only for an
    explicit user/application shutdown path, never for ordinary disconnects.
    """
    delay = max(0.05, float(delay))

    def worker() -> None:
        time.sleep(delay)
        exit_func(0)

    thread = threading.Thread(target=worker, name='zmb-hard-exit-watchdog', daemon=True)
    thread.start()
    return thread
