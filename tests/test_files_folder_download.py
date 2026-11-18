from pathlib import Path


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    gtk = (root / "linux_client_gtk.py").read_text(encoding="utf-8")
    core = (root / "linux_client.py").read_text(encoding="utf-8")

    # GTK: a single click selects; activation (double-click) navigates.
    assert "self.file_list.set_activate_on_single_click(False)" in gtk
    assert "Gtk.Button(label='Download selected')" in gtk
    assert "self.file_list.connect('row-activated', self._file_row_activated)" in gtk
    assert "if entry and entry.get('kind') == 'dir':" in gtk

    # Download must continue to support directories recursively.
    assert "dialog.select_folder(self, None, lambda d, r: self._download_folder_dialog_done" in gtk
    assert "target=self._download_folder_worker" in gtk
    assert "def _download_tree(" in core
    assert "count += self._download_tree(sock, reader, remote, local)" in core

    # Legacy Tk already uses double-click for directory navigation.
    assert "self.file_list.bind('<Double-1>', self._file_double_click)" in core


if __name__ == "__main__":
    main()
