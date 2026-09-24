from __future__ import annotations

import sys
from pathlib import Path


def resource_path(relative: str) -> Path:
    """Return a resource path both from source and from a PyInstaller bundle."""
    base = Path(getattr(sys, '_MEIPASS', Path(__file__).resolve().parent))
    return base / relative


def set_tk_icon(root, relative: str) -> None:
    """Best-effort Tk window icon. Bundle-level icons are configured separately."""
    try:
        import tkinter as tk
        image = tk.PhotoImage(file=str(resource_path(relative)))
        root.iconphoto(True, image)
        root._zorinmacbridge_icon = image
    except Exception:
        pass
