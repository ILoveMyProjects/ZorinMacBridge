from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / '.github' / 'workflows' / 'release.yml'


def test_shutdown_module_is_packaged_and_verified() -> None:
    text = WORKFLOW.read_text(encoding='utf-8')
    assert 'zmb_recovery.py zmb_shutdown.py "$APP/"' in text
    assert "usr/lib/zorinmacbridge/zmb_shutdown.py" in text


def test_release_smoke_imports_from_staged_package_tree() -> None:
    text = WORKFLOW.read_text(encoding='utf-8')
    assert 'PYTHONPATH="$APP" /usr/bin/python3 -c "import linux_client_gtk;' in text


def test_runtime_import_has_packaged_provider() -> None:
    client = (ROOT / 'linux_client_gtk.py').read_text(encoding='utf-8')
    assert 'from zmb_shutdown import arm_hard_exit_watchdog' in client
    assert (ROOT / 'zmb_shutdown.py').is_file()


def main() -> None:
    test_shutdown_module_is_packaged_and_verified()
    test_release_smoke_imports_from_staged_package_tree()
    test_runtime_import_has_packaged_provider()
    assert (ROOT / 'VERSION').read_text(encoding='utf-8').strip() == '0.15.4'
    print('linux package manifest regression tests: OK')


if __name__ == '__main__':
    main()
