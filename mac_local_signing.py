from __future__ import annotations

import hashlib
import os
import plistlib
import secrets
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

BUNDLE_ID = 'com.ilovemyprojects.zorinmacbridge.server'
APP_NAME = 'ZorinMacBridge Server.app'
IDENTITY_MARKER_KEY = 'ZMBLocalIdentity'
SIGNING_DIR = Path.home() / 'Library' / 'Application Support' / 'ZorinMacBridge' / 'CodeSigning'
TOKEN_PATH = SIGNING_DIR / 'local-identity-token'


class LocalSigningError(RuntimeError):
    pass


@dataclass(frozen=True)
class LocalIdentity:
    token: str

    @property
    def cert_sha256(self) -> str:
        """Compatibility name used by existing UI/logging; this is an identity hash, not a certificate hash."""
        return hashlib.sha256(self.token.encode('utf-8')).hexdigest().upper()

    @property
    def common_name(self) -> str:
        return 'ZorinMacBridge Stable Local Designated Requirement'

    @property
    def requirement(self) -> str:
        return (
            f'designated => identifier "{BUNDLE_ID}" '
            f'and info[{IDENTITY_MARKER_KEY}] = "{self.token}"'
        )


def _run(args: list[str], *, check: bool = True, timeout: float = 60.0) -> subprocess.CompletedProcess[str]:
    proc = subprocess.run(
        args,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
        timeout=timeout,
    )
    if check and proc.returncode != 0:
        raise LocalSigningError(f"Command failed ({proc.returncode}): {' '.join(args)}\n{(proc.stdout or '').strip()}")
    return proc


def _require_macos_tools() -> None:
    if os.uname().sysname != 'Darwin':
        raise LocalSigningError('Local macOS signing is only available on macOS.')
    if not Path('/usr/bin/codesign').exists():
        raise LocalSigningError('Required macOS tool is missing: /usr/bin/codesign')


def _chmod_private(path: Path) -> None:
    try:
        path.chmod(0o600)
    except OSError:
        pass


def ensure_local_identity() -> LocalIdentity:
    """Create or reuse one stable per-Mac designated-requirement token.

    No certificate, keychain, Developer ID, GitHub secret, or external account is
    involved.  The token is injected into the staged app's Info.plist and the app
    is ad-hoc signed with an explicit DR containing the bundle identifier plus
    this token.  Reusing the same token makes the explicit DR identical across
    updates on this Mac.
    """
    _require_macos_tools()
    SIGNING_DIR.mkdir(parents=True, exist_ok=True)
    try:
        SIGNING_DIR.chmod(0o700)
    except OSError:
        pass

    token = ''
    if TOKEN_PATH.exists():
        try:
            token = TOKEN_PATH.read_text(encoding='ascii').strip().lower()
        except Exception:
            token = ''
    if len(token) != 64 or any(ch not in '0123456789abcdef' for ch in token):
        token = secrets.token_hex(32)
        tmp = TOKEN_PATH.with_suffix('.tmp')
        tmp.write_text(token + '\n', encoding='ascii')
        _chmod_private(tmp)
        os.replace(tmp, TOKEN_PATH)
        _chmod_private(TOKEN_PATH)
    return LocalIdentity(token=token)


def bundle_identifier(app: Path) -> str:
    info = app / 'Contents' / 'Info.plist'
    try:
        with info.open('rb') as fh:
            data = plistlib.load(fh)
    except Exception as exc:
        raise LocalSigningError(f'Cannot read {info}: {exc}') from exc
    return str(data.get('CFBundleIdentifier', ''))


def _identity_marker(app: Path) -> str:
    info = app / 'Contents' / 'Info.plist'
    try:
        with info.open('rb') as fh:
            data = plistlib.load(fh)
    except Exception as exc:
        raise LocalSigningError(f'Cannot read {info}: {exc}') from exc
    return str(data.get(IDENTITY_MARKER_KEY, ''))


def _inject_identity_marker(app: Path, identity: LocalIdentity) -> None:
    info = app / 'Contents' / 'Info.plist'
    try:
        raw = info.read_bytes()
        data = plistlib.loads(raw)
    except Exception as exc:
        raise LocalSigningError(f'Cannot read {info}: {exc}') from exc
    if data.get('CFBundleIdentifier') != BUNDLE_ID:
        raise LocalSigningError(f'Refusing to modify unexpected bundle identifier: {data.get("CFBundleIdentifier")!r}')
    data[IDENTITY_MARKER_KEY] = identity.token
    fmt = plistlib.FMT_BINARY if raw.startswith(b'bplist00') else plistlib.FMT_XML
    tmp = info.with_name('Info.plist.zmbtmp')
    try:
        with tmp.open('wb') as fh:
            plistlib.dump(data, fh, fmt=fmt, sort_keys=False)
        os.replace(tmp, info)
    finally:
        try:
            tmp.unlink()
        except FileNotFoundError:
            pass


def _write_requirement(identity: LocalIdentity, directory: Path) -> Path:
    path = directory / 'zorinmacbridge-local.req'
    path.write_text(identity.requirement + '\n', encoding='utf-8')
    return path


def _sign_adhoc(target: Path) -> None:
    _run([
        '/usr/bin/codesign', '--force', '--timestamp=none', '--sign', '-', str(target),
    ])


def sign_app_locally(app: Path) -> LocalIdentity:
    app = Path(app).resolve()
    if not app.is_dir() or app.name != APP_NAME:
        raise LocalSigningError(f'Expected {APP_NAME}, got: {app}')
    if bundle_identifier(app) != BUNDLE_ID:
        raise LocalSigningError(f'Refusing to sign unexpected bundle identifier: {bundle_identifier(app)!r}')

    identity = ensure_local_identity()
    _inject_identity_marker(app, identity)

    # The release DMG is checksum-verified before this point. The locally signed
    # copy gets its own explicit DR, so it must not inherit a quarantine identity
    # from the downloaded transport copy.
    if Path('/usr/bin/xattr').exists():
        _run(['/usr/bin/xattr', '-dr', 'com.apple.quarantine', str(app)], check=False)

    nested: list[Path] = []
    contents = app / 'Contents'
    for path in contents.rglob('*'):
        if path.is_file() and path.suffix in ('.dylib', '.so'):
            nested.append(path)
    for path in sorted(nested, key=lambda p: len(str(p)), reverse=True):
        _sign_adhoc(path)

    with tempfile.TemporaryDirectory(prefix='zmb-local-sign-') as tmp:
        req = _write_requirement(identity, Path(tmp))
        _run([
            '/usr/bin/codesign', '--force', '--deep', '--options', 'runtime', '--timestamp=none',
            '--sign', '-', '--requirements', str(req), str(app),
        ], timeout=180.0)

    verify_app_local_identity(app, identity)
    return identity


def verify_app_local_identity(app: Path, identity: LocalIdentity | None = None) -> None:
    app = Path(app).resolve()
    identity = identity or ensure_local_identity()
    if bundle_identifier(app) != BUNDLE_ID:
        raise LocalSigningError(f'Unexpected bundle identifier: {bundle_identifier(app)!r}')
    if _identity_marker(app) != identity.token:
        raise LocalSigningError('The app does not contain this Mac\'s stable local identity marker.')
    _run(['/usr/bin/codesign', '--verify', '--deep', '--strict', '--verbose=2', str(app)])
    with tempfile.TemporaryDirectory(prefix='zmb-local-verify-') as tmp:
        req = _write_requirement(identity, Path(tmp))
        _run(['/usr/bin/codesign', '--verify', '--strict', '--deep', '-R', str(req), str(app)])

    # Make sure the embedded designated requirement is explicit and does not
    # fall back to the ad-hoc default cdhash requirement.
    shown = _run(['/usr/bin/codesign', '-d', '-r-', str(app)], check=False).stdout or ''
    if 'designated =>' not in shown or 'cdhash ' in shown:
        raise LocalSigningError('The app does not have the expected stable explicit designated requirement.')
    if IDENTITY_MARKER_KEY not in shown:
        raise LocalSigningError('The explicit designated requirement is missing the local identity marker.')


def app_has_local_identity(app: Path) -> bool:
    try:
        verify_app_local_identity(app)
        return True
    except Exception:
        return False


def identity_summary() -> str:
    identity = ensure_local_identity()
    return f'{identity.common_name} · {identity.cert_sha256[:16]}…'


def stage_and_sign(source_app: Path, stage_root: Path) -> tuple[Path, LocalIdentity]:
    source_app = Path(source_app).resolve()
    stage_root = Path(stage_root).resolve()
    stage_app = stage_root / APP_NAME
    if stage_app.exists():
        shutil.rmtree(stage_app)
    _run(['/usr/bin/ditto', str(source_app), str(stage_app)], timeout=180.0)
    identity = sign_app_locally(stage_app)
    return stage_app, identity


def running_app_bundle() -> Path | None:
    import sys
    try:
        exe = Path(sys.executable).resolve()
    except Exception:
        return None
    for parent in (exe, *exe.parents):
        if parent.name == APP_NAME and parent.is_dir():
            return parent
    return None


def bootstrap_installed_app_identity() -> None:
    """One-time migration from the old build-bound/ad-hoc identity.

    The running transport app stages a copy, applies this Mac's stable explicit
    designated requirement, then asks for administrator authorization only to
    replace the bundle in /Applications. There is no certificate trust step.
    """
    import shlex
    import sys

    app = running_app_bundle()
    if app is None:
        return
    expected = Path('/Applications') / APP_NAME
    try:
        same_location = app.resolve() == expected.resolve()
    except Exception:
        same_location = str(app) == str(expected)
    if not same_location or app_has_local_identity(app):
        return

    ensure_local_identity()
    with tempfile.TemporaryDirectory(prefix='zmb-identity-migration-') as tmp:
        stage_root = Path(tmp) / 'stage'
        stage_root.mkdir(parents=True, exist_ok=True)
        stage_app = stage_root / APP_NAME
        _run(['/usr/bin/ditto', str(app), str(stage_app)], timeout=180.0)
        sign_app_locally(stage_app)

        q = shlex.quote
        shell = ' && '.join([
            f'/bin/rm -rf {q(str(expected))}',
            f'/usr/bin/ditto {q(str(stage_app))} {q(str(expected))}',
        ])
        script = (
            'on run argv\n'
            'do shell script item 1 of argv with administrator privileges\n'
            'end run'
        )
        proc = _run(['/usr/bin/osascript', '-e', script, shell], check=False, timeout=300.0)
        if proc.returncode != 0:
            raise LocalSigningError(
                'Could not migrate the installed app to its stable local designated requirement.\n'
                + (proc.stdout or '').strip()
            )

    helper = (
        'sleep 1; '
        'while /bin/kill -0 "$1" 2>/dev/null; do sleep 0.2; done; '
        'exec /usr/bin/open -n "$2"'
    )
    subprocess.Popen(
        ['/bin/sh', '-c', helper, 'zmb-restart', str(os.getpid()), str(expected)],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
        close_fds=True,
    )
    os._exit(0)
