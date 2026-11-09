from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / '.github' / 'workflows' / 'release.yml'


def test_deb_is_built_and_published() -> None:
    text = WORKFLOW.read_text(encoding='utf-8')
    assert 'linux-amd64:' in text
    assert 'dpkg-deb --root-owner-group --build' in text
    assert 'release/ZorinMacBridge-Client_linux-amd64.deb' in text
    assert 'name: linux-amd64' in text
    assert 'needs: [linux-amd64, macos-arm64]' in text


def test_runtime_modules_are_packaged_and_verified() -> None:
    text = WORKFLOW.read_text(encoding='utf-8')
    required = (
        'linux_client_gtk.py', 'linux_client.py', 'zmb_shutdown.py', 'zmb_recovery.py',
        'zmb_admin_backend.py', 'zmb_session_lifecycle.py', 'zmb_client_runtime.py',
    )
    for name in required:
        assert name in text, name
        assert (ROOT / name).is_file(), name
    assert "usr/lib/zorinmacbridge/zmb_session_lifecycle.py" in text
    assert "usr/lib/zorinmacbridge/zmb_client_runtime.py" in text


def test_release_smoke_imports_from_staged_package_tree() -> None:
    text = WORKFLOW.read_text(encoding='utf-8')
    assert 'PYTHONPATH="$APP" /usr/bin/python3 -c "import linux_client_gtk;' in text


def test_dmg_is_built_and_published() -> None:
    text = WORKFLOW.read_text(encoding='utf-8')
    assert "hdiutil create -volname 'ZorinMacBridge Server'" in text
    assert 'release/ZorinMacBridge-Server_macOS-arm64.dmg' in text
    assert 'name: macos-arm64' in text


def main() -> None:
    test_deb_is_built_and_published()
    test_runtime_modules_are_packaged_and_verified()
    test_release_smoke_imports_from_staged_package_tree()
    test_dmg_is_built_and_published()
    print('GitHub DEB/DMG release artifact regression tests: OK')


if __name__ == '__main__':
    main()
