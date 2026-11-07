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

            # Debian/Ubuntu can pair an older pystray with Pillow >= 10. Older
            # pystray still references Image.ANTIALIAS, which Pillow removed.
            # Keep the compatibility shim local to ZMB instead of modifying the
            # system package under /usr/lib/python3/dist-packages.
            if not hasattr(Image, 'ANTIALIAS'):
                resampling = getattr(Image, 'Resampling', None)
                if resampling is not None:
                    Image.ANTIALIAS = resampling.LANCZOS

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

    def stop_async(self) -> None:
        """Stop a tray backend without ever blocking the GUI/main thread."""
        icon, self.icon = self.icon, None
        if icon is None:
            return

        def worker() -> None:
            try:
                icon.stop()
            except Exception:
                pass

        import threading
        threading.Thread(target=worker, name='zmb-tray-stop', daemon=True).start()
