from __future__ import annotations

import threading
import time
import urllib.error
from pathlib import Path

import updates
from tray_icon import TrayController
from zmb_shutdown import arm_hard_exit_watchdog
from updates import ReleaseAsset

ROOT = Path(__file__).resolve().parents[1]


class _Response:
    def __init__(self, chunks, *, headers=None, fail_after=None):
        self._chunks = list(chunks)
        self.headers = headers or {}
        self._reads = 0
        self._fail_after = fail_after

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def read(self, _size=-1):
        if self._fail_after is not None and self._reads >= self._fail_after:
            raise TimeoutError('synthetic read timeout')
        self._reads += 1
        if not self._chunks:
            return b''
        return self._chunks.pop(0)


def test_retry_http_504_then_success() -> None:
    original_open = updates._urlopen
    original_sleep = updates.time.sleep
    attempts = {'count': 0}
    progress: list[str] = []
    payload = b'update-payload'

    def fake_open(_request, timeout):
        attempts['count'] += 1
        if attempts['count'] < 3:
            raise urllib.error.HTTPError(
                'https://example.invalid/pkg', 504, 'Gateway Time-out', {}, None
            )
        return _Response([payload, b''], headers={'Content-Length': str(len(payload))})

    try:
        updates._urlopen = fake_open
        updates.time.sleep = lambda _delay: None
        target = ROOT / '.test-update-retry.tmp'
        target.unlink(missing_ok=True)
        asset = ReleaseAsset('pkg.bin', 'https://example.invalid/pkg', len(payload), None)
        updates._download(asset, target, progress.append, 1.0)
        assert target.read_bytes() == payload
        assert attempts['count'] == 3
        assert any('HTTP 504' in item and 'retry 2/4' in item for item in progress)
        target.unlink(missing_ok=True)
    finally:
        updates._urlopen = original_open
        updates.time.sleep = original_sleep


def test_partial_transfer_is_deleted_before_retry() -> None:
    original_open = updates._urlopen
    original_sleep = updates.time.sleep
    attempts = {'count': 0}
    payload = b'abcdefghij'

    def fake_open(_request, timeout):
        attempts['count'] += 1
        if attempts['count'] == 1:
            return _Response([b'abc'], headers={'Content-Length': str(len(payload))}, fail_after=1)
        return _Response([payload, b''], headers={'Content-Length': str(len(payload))})

    try:
        updates._urlopen = fake_open
        updates.time.sleep = lambda _delay: None
        target = ROOT / '.test-update-partial.tmp'
        target.unlink(missing_ok=True)
        asset = ReleaseAsset('pkg.bin', 'https://example.invalid/pkg', len(payload), None)
        updates._download(asset, target, None, 1.0)
        assert target.read_bytes() == payload
        assert attempts['count'] == 2
        target.unlink(missing_ok=True)
    finally:
        updates._urlopen = original_open
        updates.time.sleep = original_sleep


def test_non_transient_http_error_does_not_retry() -> None:
    original_open = updates._urlopen
    original_sleep = updates.time.sleep
    attempts = {'count': 0}

    def fake_open(_request, timeout):
        attempts['count'] += 1
        raise urllib.error.HTTPError('https://example.invalid/pkg', 404, 'Not Found', {}, None)

    try:
        updates._urlopen = fake_open
        updates.time.sleep = lambda _delay: None
        target = ROOT / '.test-update-404.tmp'
        target.unlink(missing_ok=True)
        asset = ReleaseAsset('pkg.bin', 'https://example.invalid/pkg', None, None)
        try:
            updates._download(asset, target, None, 1.0)
        except urllib.error.HTTPError as exc:
            assert exc.code == 404
        else:
            raise AssertionError('HTTP 404 should fail immediately')
        assert attempts['count'] == 1
        assert not target.exists()
    finally:
        updates._urlopen = original_open
        updates.time.sleep = original_sleep


def test_tray_stop_async_never_blocks_caller() -> None:
    gate = threading.Event()
    started = threading.Event()

    class SlowIcon:
        def stop(self):
            started.set()
            gate.wait(2.0)

    controller = TrayController(None, 'test', ROOT / 'missing.png', [])
    controller.icon = SlowIcon()
    begin = time.monotonic()
    controller.stop_async()
    elapsed = time.monotonic() - begin
    assert elapsed < 0.2, elapsed
    assert started.wait(0.5)
    gate.set()


def test_update_paths_cannot_quit_application() -> None:
    text = (ROOT / 'linux_client_gtk.py').read_text(encoding='utf-8')
    for name, next_name in [
        ('_show_update_result', 'show_window'),
        ('check_updates', '_install_update'),
        ('_install_update', '_restart_after_update'),
    ]:
        if f'    def {name}' not in text:
            # check_updates/_install_update are inherited from CoreClient; verify that
            # the GTK update rendering path itself never owns application lifetime.
            continue
        block = text.split(f'    def {name}', 1)[1]
        marker = f'    def {next_name}'
        if marker in block:
            block = block.split(marker, 1)[0]
        assert '.quit()' not in block, f'{name} must never quit the application'
        assert 'on_close(' not in block, f'{name} must never close the application'

    core = (ROOT / 'linux_client.py').read_text(encoding='utf-8')
    check_block = core.split('    def check_updates', 1)[1].split('    def _install_update', 1)[0]
    install_block = core.split('    def _install_update', 1)[1].split('    def _restart_after_update', 1)[0]
    assert 'on_close(' not in check_block
    assert 'on_close(' not in install_block


def test_persistent_lifecycle_log_and_single_quit_owner() -> None:
    text = (ROOT / 'linux_client_gtk.py').read_text(encoding='utf-8')
    assert "'.local' / 'state'" in text
    assert "'zorinmacbridge'" in text and "'client.log'" in text
    assert 'faulthandler.enable' in text
    assert 'sys.excepthook = excepthook' in text
    assert "reason='window-close'" in text
    assert "reason: str = 'user-quit'" in text
    assert text.count('app.quit()') == 1, 'GTK application quit must be centralized in on_close()'
    assert "GApplication shutdown observed; reason=" in text
    assert "Python process exiting normally (atexit)." in text


def test_hard_exit_watchdog_has_bounded_fallback() -> None:
    fired = threading.Event()
    codes: list[int] = []

    def fake_exit(code: int) -> None:
        codes.append(code)
        fired.set()

    start = time.monotonic()
    arm_hard_exit_watchdog(0.05, exit_func=fake_exit)
    assert time.monotonic() - start < 0.05
    assert fired.wait(0.5)
    assert codes == [0]


def test_close_path_is_nonblocking_by_construction() -> None:
    text = (ROOT / 'linux_client_gtk.py').read_text(encoding='utf-8')
    lifecycle = text.split('    def _on_close_request', 1)[1]
    close_request = lifecycle.split('    def on_close', 1)[0]
    on_close = lifecycle.split('    def on_close', 1)[1].split('\n\nclass ClientApplication', 1)[0]
    assert 'return False' in close_request
    assert 'return True' not in close_request
    assert 'self.set_visible(False)' in on_close
    assert 'arm_hard_exit_watchdog(1.25)' in on_close
    assert 'self.disconnect_remote()' not in on_close
    assert 'self.terminal_controller.disconnect_for_shutdown()' in on_close
    assert 'self.projects_controller.cancel(wait=False, update_ui=False)' in on_close
    assert 'self.tray.stop_async()' in on_close
    assert 'self.tray.stop()' not in on_close
    assert 'self._update_check_serial += 1' in on_close
    assert 'self._discovery_serial += 1' in on_close


def main() -> None:
    test_retry_http_504_then_success()
    test_partial_transfer_is_deleted_before_retry()
    test_non_transient_http_error_does_not_retry()
    test_tray_stop_async_never_blocks_caller()
    test_update_paths_cannot_quit_application()
    test_persistent_lifecycle_log_and_single_quit_owner()
    test_hard_exit_watchdog_has_bounded_fallback()
    test_close_path_is_nonblocking_by_construction()
    assert (ROOT / 'VERSION').read_text(encoding='utf-8').strip() == '0.14.19'
    print('update/shutdown regression tests: OK')


if __name__ == '__main__':
    main()
