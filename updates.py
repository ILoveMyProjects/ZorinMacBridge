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
import time
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


@dataclass(frozen=True)
class PreparedMacUpdate:
    current: str
    latest: str
    available: bool
    staged_app: str = ''


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
    """Read the latest official GitHub release metadata.

    The desktop UI calls this after an explicit user action. P0 may also call it
    from its scheduled root update service when the configured mode is Notify or
    Install automatically.
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
        raise RuntimeError(f'ZorinMacBridge supports macOS on Apple Silicon (arm64) only; detected: {machine}')

    raise RuntimeError(f'Automatic updates are not supported on {platform.system()}.')


def _emit(progress: ProgressCallback | None, message: str) -> None:
    if progress is not None:
        progress(message)


_TRANSIENT_HTTP_CODES = {408, 429, 500, 502, 503, 504}
_DOWNLOAD_ATTEMPTS = 4


def _retry_after_seconds(exc: BaseException, attempt: int) -> float:
    # Retry-After may be either seconds or an HTTP date. For the updater we only
    # need a bounded delay; malformed/large values fall back to exponential backoff.
    delay = min(8.0, float(2 ** max(0, attempt - 1)))
    headers = getattr(exc, 'headers', None)
    value = headers.get('Retry-After') if headers is not None else None
    if value:
        try:
            delay = min(30.0, max(0.0, float(str(value).strip())))
        except (TypeError, ValueError):
            pass
    return delay


def _is_transient_download_error(exc: BaseException) -> bool:
    if isinstance(exc, urllib.error.HTTPError):
        return int(getattr(exc, 'code', 0) or 0) in _TRANSIENT_HTTP_CODES
    if isinstance(exc, (TimeoutError, ConnectionError, ConnectionResetError, BrokenPipeError)):
        return True
    if isinstance(exc, urllib.error.URLError):
        # TLS verification errors are converted to RuntimeError in _urlopen and
        # must never be retried as if they were a transient network condition.
        return True
    return False


def _download_once(asset: ReleaseAsset, target: Path, progress: ProgressCallback | None, timeout: float) -> None:
    # urllib's timeout is per socket operation, not a total transfer deadline.
    # Keep a generous hard deadline as well so a trickling/broken connection can
    # never leave an update worker alive forever.
    deadline = time.monotonic() + max(300.0, min(1800.0, float(timeout) * 30.0))
    with _urlopen(_request(asset.download_url), timeout=timeout) as response, target.open('wb') as out:
        total_header = response.headers.get('Content-Length')
        total = int(total_header) if total_header and total_header.isdigit() else asset.size
        received = 0
        last_reported = -1
        while True:
            if time.monotonic() > deadline:
                raise TimeoutError(f'Download of {asset.name} exceeded the hard transfer deadline.')
            chunk = response.read(1024 * 1024)
            if not chunk:
                break
            out.write(chunk)
            received += len(chunk)
            if asset.size is not None and received > asset.size:
                raise RuntimeError(f'Downloaded {asset.name} is larger than the size declared by GitHub.')
            if total:
                pct = min(100, int(received * 100 / total))
                if pct // 10 != last_reported // 10:
                    last_reported = pct
                    _emit(progress, f'Downloading {asset.name}: {pct}%')
        if asset.size is not None and received != asset.size:
            raise RuntimeError(
                f'Downloaded {asset.name} has unexpected size: got {received}, expected {asset.size} bytes.'
            )


def _download(asset: ReleaseAsset, target: Path, progress: ProgressCallback | None, timeout: float) -> None:
    _emit(progress, f'Downloading {asset.name}…')
    last_exc: BaseException | None = None
    for attempt in range(1, _DOWNLOAD_ATTEMPTS + 1):
        try:
            target.unlink(missing_ok=True)
            _download_once(asset, target, progress, timeout)
            _emit(progress, f'Downloaded {asset.name}.')
            return
        except Exception as exc:
            target.unlink(missing_ok=True)
            last_exc = exc
            if not _is_transient_download_error(exc) or attempt >= _DOWNLOAD_ATTEMPTS:
                raise
            delay = _retry_after_seconds(exc, attempt)
            if isinstance(exc, urllib.error.HTTPError):
                detail = f'HTTP {exc.code}'
            else:
                detail = type(exc).__name__
            _emit(progress, f'Download failed ({detail}); retry {attempt + 1}/{_DOWNLOAD_ATTEMPTS} in {delay:g}s…')
            time.sleep(delay)
    if last_exc is not None:
        raise last_exc


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
        timeout=600,
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
        text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, check=False, timeout=60,
    )
    if completed.returncode != 0:
        raise RuntimeError(f'Could not inspect code signature for {app}:\n{completed.stdout.strip()}')
    return completed.stdout or ''


def _designated_requirement(app: Path) -> str:
    completed = subprocess.run(
        ['/usr/bin/codesign', '-d', '-r-', str(app)],
        text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, check=False, timeout=60,
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
            text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, check=False, timeout=60,
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


def _verify_macos_transport_app(app: Path, progress: ProgressCallback | None, expected_version: str | None = None) -> None:
    """Verify a downloaded macOS app without requiring paid Apple credentials.

    Every release must have a valid code signature and the expected bundle/version.
    Developer ID + provisioning is accepted when present, but the default free
    release path uses an explicit ad-hoc transport signature from GitHub Actions.
    """
    import plistlib
    from mac_release_signing import app_has_release_identity, verify_release_app

    codesign = Path('/usr/bin/codesign')
    if not codesign.exists():
        raise RuntimeError('codesign was not found; refusing to install an unverified macOS update.')
    _emit(progress, 'Verifying macOS application signature and bundle identity…')
    completed = subprocess.run(
        [str(codesign), '--verify', '--strict', '--deep', '--verbose=2', str(app)],
        text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, check=False, timeout=120,
    )
    if completed.returncode != 0:
        raise RuntimeError('Downloaded macOS app has an invalid code signature. Update aborted.\n' + (completed.stdout or '').strip())
    info = app / 'Contents' / 'Info.plist'
    try:
        with info.open('rb') as fh:
            plist = plistlib.load(fh)
        bundle_id = plist.get('CFBundleIdentifier')
        bundle_version = str(plist.get('CFBundleShortVersionString') or plist.get('CFBundleVersion') or '').strip()
    except Exception as exc:
        raise RuntimeError(f'Could not read downloaded app Info.plist: {exc}') from exc
    if bundle_id != 'com.ilovemyprojects.zorinmacbridge.server':
        raise RuntimeError(f'Unexpected macOS bundle identifier: {bundle_id!r}. Update aborted.')
    if expected_version and _version_tuple(bundle_version) != _version_tuple(expected_version):
        raise RuntimeError(
            f'Downloaded macOS app version {bundle_version or "<missing>"!r} does not match release {expected_version!r}. Update aborted.'
        )
    if app_has_release_identity(app):
        release_info = verify_release_app(app)
        _emit(progress, f'Optional Developer ID release verified: team {release_info.team_identifier}; profile {release_info.name}.')
    else:
        _verify_requirement(app, 'identifier "com.ilovemyprojects.zorinmacbridge.server"', 'Downloaded application')
        _emit(progress, 'Free ad-hoc transport signature verified.')


def _verify_macos_update_identity(current_app: Path | None, new_app: Path, progress: ProgressCallback | None) -> str:
    """Return the update signing mode after validating optional publisher identity."""
    from mac_release_signing import app_has_release_identity, verify_release_app

    if not app_has_release_identity(new_app):
        _emit(progress, 'Free release selected; a stable per-Mac local identity will be applied before installation.')
        return 'local-adhoc'

    new_info = verify_release_app(new_app)
    if current_app is None or not current_app.is_dir():
        _emit(progress, f'Optional Developer ID identity verified for new installation (team {new_info.team_identifier}).')
        return 'developer-id'

    if app_has_release_identity(current_app):
        old_info = verify_release_app(current_app)
        if old_info.team_identifier != new_info.team_identifier:
            raise RuntimeError(
                f'Update Developer Team mismatch: installed={old_info.team_identifier}, downloaded={new_info.team_identifier}. '
                'The update was NOT installed.'
            )
        old_req = _designated_requirement(current_app)
        _verify_requirement(new_app, old_req, 'New application')
        _emit(progress, 'Optional Developer ID designated requirement and TeamIdentifier continuity verified.')
        return 'developer-id'

    _emit(progress, 'Optional migration: local/ad-hoc code identity → Developer ID release.')
    return 'developer-id'


def _stage_macos_update_app(source_app: Path, stage_root: Path, signing_mode: str, progress: ProgressCallback | None) -> Path:
    """Stage an update, applying free per-Mac signing unless Developer ID is present."""
    stage_root = Path(stage_root)
    stage_root.mkdir(parents=True, exist_ok=True)
    if signing_mode == 'developer-id':
        staged_app = stage_root / 'ZorinMacBridge Server.app'
        if staged_app.exists():
            shutil.rmtree(staged_app)
        _emit(progress, 'Preserving optional Developer ID signature while staging update…')
        completed = subprocess.run(
            ['/usr/bin/ditto', str(source_app), str(staged_app)],
            text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, check=False, timeout=180,
        )
        if completed.returncode != 0:
            raise RuntimeError('Could not stage verified macOS update:\n' + (completed.stdout or '').strip())
        return staged_app

    from mac_local_signing import stage_and_sign
    _emit(progress, 'Applying free stable per-Mac ad-hoc identity to staged update…')
    staged_app, _identity = stage_and_sign(source_app, stage_root)
    return staged_app


def prepare_macos_update_for_root(
    stage_root: Path,
    progress: ProgressCallback | None = None,
    timeout: float = 30.0,
) -> PreparedMacUpdate:
    """Prepare a verified macOS update; paid Apple signing is optional."""
    if platform.system().lower() != 'darwin' or platform.machine().lower() not in {'arm64', 'aarch64'}:
        raise RuntimeError('P0 macOS update staging is supported on Apple Silicon Macs only.')

    stage_root = Path(stage_root).resolve()
    stage_root.mkdir(parents=True, exist_ok=True)
    info = check_for_updates(timeout=min(timeout, 15.0))
    if not info.available:
        return PreparedMacUpdate(current=info.current, latest=info.latest, available=False)

    with tempfile.TemporaryDirectory(prefix='zorinmacbridge-p0-download-', dir=str(stage_root)) as tmp:
        temp_dir = Path(tmp)
        kind, package_path = _download_and_verify(info, temp_dir, progress, timeout)
        if kind != 'macos':
            raise RuntimeError(f'Unexpected updater platform while preparing macOS update: {kind}')

        hdiutil = Path('/usr/bin/hdiutil')
        if not hdiutil.exists():
            raise RuntimeError('Required macOS tool hdiutil was not found.')
        mount_dir = temp_dir / 'mounted-dmg'
        mount_dir.mkdir(exist_ok=True)
        mounted = False
        try:
            _emit(progress, 'Mounting verified macOS release…')
            completed = subprocess.run(
                [str(hdiutil), 'attach', str(package_path), '-nobrowse', '-readonly', '-mountpoint', str(mount_dir), '-quiet'],
                text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, check=False, timeout=120,
            )
            if completed.returncode != 0:
                raise RuntimeError('Could not mount the update DMG:\n' + (completed.stdout or '').strip())
            mounted = True

            source_app = mount_dir / 'ZorinMacBridge Server.app'
            if not source_app.is_dir():
                raise RuntimeError('The downloaded DMG does not contain ZorinMacBridge Server.app.')
            _verify_macos_transport_app(source_app, progress, expected_version=info.latest)
            signing_mode = _verify_macos_update_identity(_current_macos_app(), source_app, progress)

            final_stage = stage_root / 'prepared'
            staged_app = _stage_macos_update_app(source_app, final_stage, signing_mode, progress)
            _verify_macos_transport_app(staged_app, progress, expected_version=info.latest)
            return PreparedMacUpdate(current=info.current, latest=info.latest, available=True, staged_app=str(staged_app))
        finally:
            if mounted:
                subprocess.run(
                    [str(hdiutil), 'detach', str(mount_dir), '-quiet'],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False, timeout=30,
                )


def _install_macos(package_path: Path, progress: ProgressCallback | None, expected_version: str | None = None) -> None:
    hdiutil = '/usr/bin/hdiutil'
    osascript = '/usr/bin/osascript'
    app_name = 'ZorinMacBridge Server.app'
    destination = f'/Applications/{app_name}'

    if not Path(hdiutil).exists() or not Path(osascript).exists():
        raise RuntimeError('Required macOS system tools hdiutil/osascript were not found.')

    mount_dir = package_path.parent / 'mounted-dmg'
    stage_root = package_path.parent / 'install-stage'
    mount_dir.mkdir(exist_ok=True)
    stage_root.mkdir(exist_ok=True)
    mounted = False
    try:
        _emit(progress, 'Mounting the verified DMG…')
        completed = subprocess.run(
            [hdiutil, 'attach', str(package_path), '-nobrowse', '-readonly', '-mountpoint', str(mount_dir), '-quiet'],
            text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, check=False, timeout=120,
        )
        if completed.returncode != 0:
            raise RuntimeError('Could not mount the update DMG:\n' + (completed.stdout or '').strip())
        mounted = True

        source_app = mount_dir / app_name
        if not source_app.is_dir():
            raise RuntimeError(f'The downloaded DMG does not contain {app_name}.')
        _verify_macos_transport_app(source_app, progress, expected_version=expected_version)
        signing_mode = _verify_macos_update_identity(_current_macos_app(), source_app, progress)
        staged_app = _stage_macos_update_app(source_app, stage_root, signing_mode, progress)
        _verify_macos_transport_app(staged_app, progress, expected_version=expected_version)

        _emit(progress, 'Waiting for the macOS administrator-password prompt…')
        script = (
            'on run argv\n'
            'set src to item 1 of argv\n'
            'set dst to item 2 of argv\n'
            'do shell script "/bin/rm -rf " & quoted form of dst & " && /usr/bin/ditto " & quoted form of src & " " & quoted form of dst with administrator privileges\n'
            'end run'
        )
        completed = subprocess.run(
            [osascript, '-e', script, str(staged_app), destination],
            text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, check=False, timeout=600,
        )
        if completed.returncode != 0:
            output = (completed.stdout or '').strip()
            raise RuntimeError('macOS application installation failed:\n' + (output or f'osascript exited with code {completed.returncode}'))
        _verify_macos_transport_app(Path(destination), progress, expected_version=expected_version)
        if signing_mode == 'developer-id':
            _emit(progress, 'macOS application installed with optional Developer ID identity preserved.')
        else:
            _emit(progress, 'macOS application installed with free stable per-Mac ad-hoc identity.')
    finally:
        if mounted:
            subprocess.run(
                [hdiutil, 'detach', str(mount_dir), '-quiet'],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False, timeout=30,
            )

def install_update(
    info: UpdateInfo,
    progress: ProgressCallback | None = None,
    timeout: float = 30.0,
) -> InstallResult:
    """Download, verify, and install a newer release from the interactive app.

    This function is the manual desktop updater and may show the normal local
    authorization flow. P0 unattended updates use prepare_macos_update_for_root()
    plus the restricted root helper instead.
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
            _install_macos(package_path, progress, expected_version=info.latest)
        else:
            raise RuntimeError(f'Unsupported updater platform: {kind}')

        return InstallResult(
            current=info.current,
            installed=info.latest,
            package_name=package_path.name,
            restart_recommended=True,
        )
