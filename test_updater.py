from __future__ import annotations

import hashlib
import tempfile
from pathlib import Path

from updates import ReleaseAsset, UpdateInfo, _download_and_verify, updater_tls_self_test


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

    print('updater tests: OK')


if __name__ == '__main__':
    main()
