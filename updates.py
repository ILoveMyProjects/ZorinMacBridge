from __future__ import annotations

import hashlib
import json
import os
import platform
import shutil
import ssl
import sys
import subprocess
import tempfile
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import certifi

from resources import resource_path

REPOSITORY = 'ILoveMyProjects/ZorinMacBridge'
LATEST_API = f'https://api.github.com/repos/{REPOSITORY}/releases/latest'

ProgressCallback = Callable[[str], None]


@dataclass(frozen=True)
class ReleaseAsset:
    name: str
    download_url: str
    size: int | None = None
    digest: str | None = None


@dataclass(frozen=True)
class UpdateInfo:
    current: str
    latest: str
    available: bool
    page_url: str
    assets: dict[str, ReleaseAsset]


@dataclass(frozen=True)
class InstallResult:
    current: str
    installed: str
    package_name: str
    restart_recommended: bool = True


def current_version() -> str:
    try:
        return resource_path('VERSION').read_text(encoding='utf-8').strip()
    except Exception:
        return '0.0.0'


def _version_tuple(value: str) -> tuple[int, ...]:
    import re

    value = value.strip().lstrip('vV')
    match = re.match(r'^(\d+(?:\.\d+)*)', value)
    if not match:
        return (0,)
    return tuple(int(part) for part in match.group(1).split('.'))


def _request(url: str) -> urllib.request.Request:
    return urllib.request.Request(
        url,
        headers={
            'Accept': 'application/vnd.github+json',
            'User-Agent': f'ZorinMacBridge/{current_version()}',
        },
    )


def _ca_bundle() -> str:
    """Return the bundled Mozilla CA bundle used for HTTPS update traffic.

    The frozen macOS app must not depend on whichever CA paths happen to be
    visible to the embedded Python/OpenSSL runtime.  certifi is packaged with
    the app and gives us a deterministic trust store while keeping certificate
    verification fully enabled.
    """
    path = Path(certifi.where())
    if not path.is_file():
        raise RuntimeError(f'Bundled CA certificate file was not found: {path}')
    return str(path)


def _https_context() -> ssl.SSLContext:
    return ssl.create_default_context(cafile=_ca_bundle())


def _urlopen(request: urllib.request.Request, timeout: float):
    url = request.full_url
    try:
        if url.lower().startswith('https://'):
            return urllib.request.urlopen(request, timeout=timeout, context=_https_context())
        # Local file:// URLs are used by the updater unit tests and do not use TLS.
        return urllib.request.urlopen(request, timeout=timeout)
    except ssl.SSLCertVerificationError as exc:
        raise RuntimeError(
            'TLS certificate verification failed while contacting GitHub for updates. '
            f'Bundled CA file: {_ca_bundle()}. Error: {exc}'
        ) from exc
    except urllib.error.URLError as exc:
        reason = getattr(exc, 'reason', None)
        if isinstance(reason, ssl.SSLCertVerificationError):
            raise RuntimeError(
                'TLS certificate verification failed while contacting GitHub for updates. '
                f'Bundled CA file: {_ca_bundle()}. Error: {reason}'
            ) from exc
        raise


def updater_tls_self_test() -> str:
    """Verify that a frozen build contains a usable CA bundle and SSL context.

    This intentionally performs no network request.  The release workflow runs
    it inside the finished executable to catch missing certifi data before a
    release is published.
    """
    cafile = _ca_bundle()
    context = _https_context()
    if context.verify_mode != ssl.CERT_REQUIRED or not context.check_hostname:
        raise RuntimeError('Updater TLS context is not enforcing certificate verification.')
    return cafile


def check_for_updates(timeout: float = 8.0) -> UpdateInfo:
    """Perform an explicit, user-initiated GitHub release check.

    This function is intentionally never called in the background or at app start.
    """
    with _urlopen(_request(LATEST_API), timeout=timeout) as response:
        data = json.load(response)

    latest = str(data.get('tag_name') or '').strip().lstrip('vV')
    if not latest:
        raise RuntimeError('GitHub did not return a release version.')

    assets: dict[str, ReleaseAsset] = {}
    for item in data.get('assets') or []:
        name = str(item.get('name') or '').strip()
        url = str(item.get('browser_download_url') or '').strip()
        if not name or not url:
            continue
        size = item.get('size')
        digest = item.get('digest')
        assets[name] = ReleaseAsset(
            name=name,
            download_url=url,
            size=int(size) if isinstance(size, int) else None,
            digest=str(digest) if digest else None,
        )

    current = current_version()
    return UpdateInfo(
        current=current,
        latest=latest,
        available=_version_tuple(latest) > _version_tuple(current),
        page_url=str(data.get('html_url') or f'https://github.com/{REPOSITORY}/releases/latest'),
        assets=assets,
    )


def _asset_names() -> tuple[str, str, str]:
    system = platform.system().lower()
    machine = platform.machine().lower()

    if system == 'linux':
        if machine in {'x86_64', 'amd64'}:
            return ('linux', 'ZorinMacBridge-Client_linux-amd64.deb', 'SHA256SUMS-linux.txt')
        raise RuntimeError(f'Unsupported Linux architecture for automatic updates: {machine}')

    if system == 'darwin':
        if machine in {'arm64', 'aarch64'}:
            return ('macos', 'ZorinMacBridge-Server_macOS-arm64.dmg', 'SHA256SUMS-macOS-arm64.txt')
        if machine in {'x86_64', 'amd64'}:
            return ('macos', 'ZorinMacBridge-Server_macOS-x86_64.dmg', 'SHA256SUMS-macOS-x86_64.txt')
        raise RuntimeError(f'Unsupported macOS architecture for automatic updates: {machine}')

    raise RuntimeError(f'Automatic updates are not supported on {platform.system()}.')


def _emit(progress: ProgressCallback | None, message: str) -> None:
    if progress is not None:
        progress(message)


def _download(asset: ReleaseAsset, target: Path, progress: ProgressCallback | None, timeout: float) -> None:
    _emit(progress, f'Downloading {asset.name}…')
    with _urlopen(_request(asset.download_url), timeout=timeout) as response, target.open('wb') as out:
        total_header = response.headers.get('Content-Length')
        total = int(total_header) if total_header and total_header.isdigit() else asset.size
        received = 0
        last_reported = -1
        while True:
            chunk = response.read(1024 * 1024)
            if not chunk:
                break
            out.write(chunk)
            received += len(chunk)
            if total:
                pct = min(100, int(received * 100 / total))
                if pct // 10 != last_reported // 10:
                    last_reported = pct
                    _emit(progress, f'Downloading {asset.name}: {pct}%')
    _emit(progress, f'Downloaded {asset.name}.')


def _checksum_for(text: str, filename: str) -> str:
    for line in text.splitlines():
        parts = line.strip().split()
        if len(parts) < 2:
            continue
        listed = parts[-1].lstrip('*')
        if listed == filename:
            digest = parts[0].strip().lower()
            if len(digest) == 64 and all(ch in '0123456789abcdef' for ch in digest):
                return digest
    raise RuntimeError(f'Checksum file does not contain a valid SHA-256 entry for {filename}.')


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open('rb') as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def _download_and_verify(
    info: UpdateInfo,
    temp_dir: Path,
    progress: ProgressCallback | None,
    timeout: float,
) -> tuple[str, Path]:
    kind, package_name, checksum_name = _asset_names()

    package_asset = info.assets.get(package_name)
    checksum_asset = info.assets.get(checksum_name)
    if package_asset is None:
        raise RuntimeError(f'Release v{info.latest} does not contain {package_name}.')
    if checksum_asset is None:
        raise RuntimeError(f'Release v{info.latest} does not contain {checksum_name}.')

    package_path = temp_dir / package_name
    checksum_path = temp_dir / checksum_name
    _download(package_asset, package_path, progress, timeout)
    _download(checksum_asset, checksum_path, progress, timeout)

    _emit(progress, 'Verifying SHA-256 checksum…')
    expected = _checksum_for(checksum_path.read_text(encoding='utf-8', errors='replace'), package_name)
    actual = _sha256(package_path)
    if expected != actual:
        raise RuntimeError(
            f'SHA-256 verification failed for {package_name}. Expected {expected}, got {actual}. '
            'The update was NOT installed.'
        )

    # GitHub may expose its own asset digest. Treat it as an additional integrity check when present.
    if package_asset.digest:
        prefix, sep, digest = package_asset.digest.partition(':')
        if sep and prefix.lower() == 'sha256' and digest and digest.lower() != actual:
            raise RuntimeError(
                f'GitHub asset digest does not match {package_name}. The update was NOT installed.'
            )

    _emit(progress, 'SHA-256 verified.')
    return kind, package_path


def _install_linux(package_path: Path, progress: ProgressCallback | None) -> None:
    apt = shutil.which('apt') or '/usr/bin/apt'
    if not Path(apt).exists():
        raise RuntimeError('apt was not found. Automatic installation is only supported on Debian/Ubuntu/Zorin systems.')

    command = [apt, 'install', '-y', str(package_path)]
    if os.geteuid() != 0:
        pkexec = shutil.which('pkexec')
        if not pkexec:
            raise RuntimeError(
                'pkexec was not found. Install PolicyKit/pkexec or update using the one-command installer from README.'
            )
        command.insert(0, pkexec)
        _emit(progress, 'Waiting for the system administrator-password prompt…')

    _emit(progress, 'Installing the Linux package…')
    completed = subprocess.run(
        command,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
    )
    if completed.returncode != 0:
        output = (completed.stdout or '').strip()
        tail = output[-4000:] if output else f'installer exited with code {completed.returncode}'
        raise RuntimeError('Linux package installation failed:\n' + tail)
    _emit(progress, 'Linux package installed successfully.')




def _current_macos_app() -> Path | None:
    """Return the currently running .app bundle when executed from a frozen macOS app."""
    candidates = []
    try:
        candidates.append(Path(sys.executable).resolve())
    except Exception:
        pass
    candidates.append(Path('/Applications/ZorinMacBridge Server.app'))
    for candidate in candidates:
        for parent in (candidate, *candidate.parents):
            if parent.name == 'ZorinMacBridge Server.app' and parent.is_dir():
                return parent
    return None


def _codesign_output(app: Path) -> str:
    completed = subprocess.run(
        ['/usr/bin/codesign', '-dvvv', str(app)],
        text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, check=False,
    )
    if completed.returncode != 0:
        raise RuntimeError(f'Could not inspect code signature for {app}:\n{completed.stdout.strip()}')
    return completed.stdout or ''


def _designated_requirement(app: Path) -> str:
    completed = subprocess.run(
        ['/usr/bin/codesign', '-d', '-r-', str(app)],
        text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, check=False,
    )
    text = completed.stdout or ''
    marker = 'designated =>'
    pos = text.find(marker)
    if completed.returncode != 0 or pos < 0:
        raise RuntimeError(f'Could not read designated requirement for {app}:\n{text.strip()}')
    requirement = text[pos + len(marker):].strip()
    if not requirement:
        raise RuntimeError(f'Empty designated requirement for {app}.')
    return requirement


def _verify_requirement(app: Path, requirement: str, label: str) -> None:
    with tempfile.NamedTemporaryFile('w', prefix='zmb-requirement-', suffix='.txt', delete=False) as handle:
        handle.write(requirement + '\n')
        req_path = Path(handle.name)
    try:
        completed = subprocess.run(
            ['/usr/bin/codesign', '--verify', '--strict', '--deep', '-R', str(req_path), str(app)],
            text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, check=False,
        )
        if completed.returncode != 0:
            raise RuntimeError(
                f'{label} does not satisfy the required ZorinMacBridge code identity. '
                'The update was NOT installed.\n' + (completed.stdout or '').strip()
            )
    finally:
        try:
            req_path.unlink()
        except OSError:
            pass


def _verify_macos_transport_app(app: Path, progress: ProgressCallback | None) -> None:
    """Verify the release artifact before it is re-signed for this Mac."""
    import plistlib

    codesign = Path('/usr/bin/codesign')
    if not codesign.exists():
        raise RuntimeError('codesign was not found; refusing to install an unverified macOS update.')
    _emit(progress, 'Verifying downloaded macOS application…')
    completed = subprocess.run(
        [str(codesign), '--verify', '--strict', '--deep', '--verbose=2', str(app)],
        text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, check=False,
    )
    if completed.returncode != 0:
        raise RuntimeError('Downloaded macOS app has an invalid transport signature. Update aborted.\n' + (completed.stdout or '').strip())
    info = app / 'Contents' / 'Info.plist'
    try:
        with info.open('rb') as fh:
            bundle_id = plistlib.load(fh).get('CFBundleIdentifier')
    except Exception as exc:
        raise RuntimeError(f'Could not read downloaded app Info.plist: {exc}') from exc
    if bundle_id != 'com.ilovemyprojects.zorinmacbridge.server':
        raise RuntimeError(f'Unexpected macOS bundle identifier: {bundle_id!r}. Update aborted.')


def _verify_macos_update_identity(current_app: Path | None, new_app: Path, progress: ProgressCallback | None) -> None:
    """Verify that an already locally-signed install keeps the same per-Mac DR.

    v0.6.6 is the migration point for the certificate-free stable DR model. Older
    installs may have build-bound ad-hoc, transport, or experimental local-certificate
    signatures. Every installed update is re-signed with the same explicit per-Mac DR
    *before* replacing the app.
    """
    from mac_local_signing import app_has_local_identity, verify_app_local_identity

    verify_app_local_identity(new_app)
    if current_app is None or not current_app.is_dir():
        _emit(progress, 'Stable local designated requirement verified for the new installation.')
        return

    if not app_has_local_identity(current_app):
        _emit(progress, 'One-time migration: current app uses the old build-bound identity; new app uses this Mac\'s stable local designated requirement.')
        return

    old_req = _designated_requirement(current_app)
    new_req = _designated_requirement(new_app)
    _verify_requirement(new_app, old_req, 'New application')
    _verify_requirement(current_app, new_req, 'Current application')
    _emit(progress, 'Stable designated requirement verified: privacy permissions remain attached to the same app identity.')


def _install_macos(package_path: Path, progress: ProgressCallback | None) -> None:
    hdiutil = '/usr/bin/hdiutil'
    osascript = '/usr/bin/osascript'
    app_name = 'ZorinMacBridge Server.app'
    destination = f'/Applications/{app_name}'

    if not Path(hdiutil).exists() or not Path(osascript).exists():
        raise RuntimeError('Required macOS system tools hdiutil/osascript were not found.')

    from mac_local_signing import stage_and_sign

    mount_dir = package_path.parent / 'mounted-dmg'
    stage_root = package_path.parent / 'locally-signed-stage'
    mount_dir.mkdir(exist_ok=True)
    stage_root.mkdir(exist_ok=True)
    mounted = False
    try:
        _emit(progress, 'Mounting the verified DMG…')
        completed = subprocess.run(
            [hdiutil, 'attach', str(package_path), '-nobrowse', '-readonly', '-mountpoint', str(mount_dir), '-quiet'],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            check=False,
        )
        if completed.returncode != 0:
            raise RuntimeError('Could not mount the update DMG:\n' + (completed.stdout or '').strip())
        mounted = True

        source_app = mount_dir / app_name
        if not source_app.is_dir():
            raise RuntimeError(f'The downloaded DMG does not contain {app_name}.')

        _verify_macos_transport_app(source_app, progress)
        _emit(progress, 'Applying this Mac\'s persistent local code identity…')
        signed_app, identity = stage_and_sign(source_app, stage_root)
        _emit(progress, f'Stable local DR ready: {identity.cert_sha256[:16]}…')
        _verify_macos_update_identity(_current_macos_app(), signed_app, progress)

        _emit(progress, 'Waiting for the macOS administrator-password prompt…')
        script = (
            'on run argv\n'
            'set src to item 1 of argv\n'
            'set dst to item 2 of argv\n'
            'do shell script "/bin/rm -rf " & quoted form of dst & " && /usr/bin/ditto " & quoted form of src & " " & quoted form of dst with administrator privileges\n'
            'end run'
        )
        completed = subprocess.run(
            [osascript, '-e', script, str(signed_app), destination],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            check=False,
        )
        if completed.returncode != 0:
            output = (completed.stdout or '').strip()
            raise RuntimeError('macOS application installation failed:\n' + (output or f'osascript exited with code {completed.returncode}'))
        _emit(progress, 'macOS application installed with the stable local designated requirement.')
    finally:
        if mounted:
            subprocess.run(
                [hdiutil, 'detach', str(mount_dir), '-quiet'],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
            )

def install_update(
    info: UpdateInfo,
    progress: ProgressCallback | None = None,
    timeout: float = 30.0,
) -> InstallResult:
    """Download, verify, and install a newer release after explicit user approval.

    No update work is performed automatically. Callers must invoke this only after the
    user manually requests an update and confirms installation.
    """
    if not info.available:
        raise RuntimeError(f'No newer version is available (installed: {info.current}, latest: {info.latest}).')

    _emit(progress, f'Preparing update {info.current} → {info.latest}.')
    with tempfile.TemporaryDirectory(prefix='zorinmacbridge-update-') as tmp:
        temp_dir = Path(tmp)
        kind, package_path = _download_and_verify(info, temp_dir, progress, timeout)
        if kind == 'linux':
            _install_linux(package_path, progress)
        elif kind == 'macos':
            _install_macos(package_path, progress)
        else:
            raise RuntimeError(f'Unsupported updater platform: {kind}')

        return InstallResult(
            current=info.current,
            installed=info.latest,
            package_name=package_path.name,
            restart_recommended=True,
        )
