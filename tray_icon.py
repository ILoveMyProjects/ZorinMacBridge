from __future__ import annotations

from collections.abc import Callable
from pathlib import Path


class TrayController:
    """Best-effort system tray / macOS menu-bar integration.

    Failure to create a tray icon must never prevent the main GUI from running.
    """

    def __init__(self, root, title: str, icon_path: Path, items: list[tuple[str, Callable[[], None]]]) -> None:
        self.root = root
        self.title = title
        self.icon_path = icon_path
        self.items = items
        self.icon = None

    def start(self) -> bool:
        try:
            import pystray
            from PIL import Image

            image = Image.open(self.icon_path).convert('RGBA')

            def invoke(callback):
                def wrapped(_icon=None, _item=None):
                    try:
                        self.root.after(0, callback)
                    except Exception:
                        pass
                return wrapped

            menu_items = [pystray.MenuItem(label, invoke(callback)) for label, callback in self.items]
            self.icon = pystray.Icon(self.title, image, self.title, pystray.Menu(*menu_items))
            self.icon.run_detached()
            return True
        except Exception:
            self.icon = None
            return False

    def stop(self) -> None:
        icon, self.icon = self.icon, None
        if icon is not None:
            try:
                icon.stop()
            except Exception:
                pass
