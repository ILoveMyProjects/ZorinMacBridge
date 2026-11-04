from __future__ import annotations

import hashlib
import tempfile
from pathlib import Path

import updates
from updates import ReleaseAsset, UpdateInfo, _asset_names, _download_and_verify, updater_tls_self_test


def file_asset(path: Path) -> ReleaseAsset:
    return ReleaseAsset(path.name, path.resolve().as_uri(), path.stat().st_size, None)


def main() -> None:
    cafile = updater_tls_self_test()
    assert Path(cafile).is_file()
    with tempfile.TemporaryDirectory() as source_tmp, tempfile.TemporaryDirectory() as dest_tmp:
        source = Path(source_tmp)
        package = source / 'ZorinMacBridge-Client_linux-amd64.deb'
        package.write_bytes(b'not-a-real-deb-but-valid-test-payload\n' * 100)
        digest = hashlib.sha256(package.read_bytes()).hexdigest()
        sums = source / 'SHA256SUMS-linux.txt'
        sums.write_text(f'{digest}  {package.name}\n', encoding='utf-8')

        info = UpdateInfo(
            current='0.3.1',
            latest='0.3.2',
            available=True,
            page_url='https://example.invalid/release',
            assets={package.name: file_asset(package), sums.name: file_asset(sums)},
        )
        kind, downloaded = _download_and_verify(info, Path(dest_tmp), None, 5.0)
        assert kind == 'linux'
        assert downloaded.read_bytes() == package.read_bytes()

    # The downloader must reject a transfer whose byte count differs from
    # GitHub release metadata instead of accepting an unbounded/partial stream.
    with tempfile.TemporaryDirectory() as source_tmp, tempfile.TemporaryDirectory() as dest_tmp:
        source = Path(source_tmp)
        package = source / 'ZorinMacBridge-Client_linux-amd64.deb'
        package.write_bytes(b'size-check-payload')
        digest = hashlib.sha256(package.read_bytes()).hexdigest()
        sums = source / 'SHA256SUMS-linux.txt'
        sums.write_text(f'{digest}  {package.name}\n', encoding='utf-8')
        bad_size_asset = ReleaseAsset(package.name, package.resolve().as_uri(), package.stat().st_size + 1, None)
        info = UpdateInfo(
            current='0.3.1', latest='0.3.2', available=True,
            page_url='https://example.invalid/release',
            assets={package.name: bad_size_asset, sums.name: file_asset(sums)},
        )
        try:
            _download_and_verify(info, Path(dest_tmp), None, 5.0)
        except RuntimeError as exc:
            assert 'unexpected size' in str(exc) or 'larger than the size declared' in str(exc)
        else:
            raise AssertionError('Mismatched GitHub asset size was not rejected')

    with tempfile.TemporaryDirectory() as source_tmp, tempfile.TemporaryDirectory() as dest_tmp:
        source = Path(source_tmp)
        package = source / 'ZorinMacBridge-Client_linux-amd64.deb'
        package.write_bytes(b'tampered')
        sums = source / 'SHA256SUMS-linux.txt'
        sums.write_text(f'{"0" * 64}  {package.name}\n', encoding='utf-8')
        info = UpdateInfo(
            current='0.3.1', latest='0.3.2', available=True,
            page_url='https://example.invalid/release',
            assets={package.name: file_asset(package), sums.name: file_asset(sums)},
        )
        try:
            _download_and_verify(info, Path(dest_tmp), None, 5.0)
        except RuntimeError as exc:
            assert 'SHA-256 verification failed' in str(exc)
        else:
            raise AssertionError('Tampered package was not rejected')


    # macOS is intentionally Apple Silicon only.
    orig_system = updates.platform.system
    orig_machine = updates.platform.machine
    try:
        updates.platform.system = lambda: 'Darwin'
        updates.platform.machine = lambda: 'arm64'
        assert _asset_names() == ('macos', 'ZorinMacBridge-Server_macOS-arm64.dmg', 'SHA256SUMS-macOS-arm64.txt')
        updates.platform.machine = lambda: 'x86_64'
        try:
            _asset_names()
        except RuntimeError as exc:
            assert 'Apple Silicon' in str(exc)
        else:
            raise AssertionError('Intel macOS must be rejected')
    finally:
        updates.platform.system = orig_system
        updates.platform.machine = orig_machine

    print('updater tests: OK')


if __name__ == '__main__':
    main()
