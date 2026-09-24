from __future__ import annotations

import datetime as _dt
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
SIGNING_DIR = Path.home() / 'Library' / 'Application Support' / 'ZorinMacBridge' / 'CodeSigning'
KEYCHAIN_PATH = SIGNING_DIR / 'local-signing.keychain-db'
PASSWORD_PATH = SIGNING_DIR / 'local-signing.keychain.password'
P12_PATH = SIGNING_DIR / 'local-signing-identity.p12'
P12_PASSWORD_PATH = SIGNING_DIR / 'local-signing-identity.p12.password'
CERT_PATH = SIGNING_DIR / 'local-signing-cert.pem'
IDENTITY_LABEL = 'ZorinMacBridge Local Stable Code Signing'


class LocalSigningError(RuntimeError):
    pass


@dataclass(frozen=True)
class LocalIdentity:
    keychain: Path
    keychain_password: str
    cert_sha1: str
    cert_sha256: str
    common_name: str

    @property
    def requirement(self) -> str:
        return (
            f'designated => identifier "{BUNDLE_ID}" '
            f'and certificate leaf = H"{self.cert_sha1}"'
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
    for tool in ('/usr/bin/security', '/usr/bin/codesign'):
        if not Path(tool).exists():
            raise LocalSigningError(f'Required macOS tool is missing: {tool}')


def _chmod_private(path: Path) -> None:
    try:
        path.chmod(0o600)
    except OSError:
        pass


def _load_cert_hashes(cert_pem: bytes) -> tuple[str, str, str]:
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes
    from cryptography.x509.oid import NameOID

    cert = x509.load_pem_x509_certificate(cert_pem)
    sha1 = cert.fingerprint(hashes.SHA1()).hex().upper()
    sha256 = cert.fingerprint(hashes.SHA256()).hex().upper()
    attrs = cert.subject.get_attributes_for_oid(NameOID.COMMON_NAME)
    cn = attrs[0].value if attrs else IDENTITY_LABEL
    return sha1, sha256, cn



def _user_keychain_search_list() -> list[str]:
    proc = _run(['/usr/bin/security', 'list-keychains', '-d', 'user'], check=False)
    if proc.returncode != 0:
        return []
    result: list[str] = []
    for raw in (proc.stdout or '').splitlines():
        value = raw.strip().strip('"')
        if value:
            result.append(value)
    return result


def _ensure_keychain_searchable(keychain: Path) -> None:
    """Put the app-owned keychain in the user's search list exactly once.

    codesign may report errSecItemNotFound for a perfectly valid identity in a
    custom keychain when that keychain is not in the user's search list.  Keep
    the normal login/system entries and prepend only our private keychain.
    """
    current = _user_keychain_search_list()
    wanted = str(keychain)
    if wanted in current:
        return
    _run(['/usr/bin/security', 'list-keychains', '-d', 'user', '-s', wanted, *current])


def _create_identity_files() -> None:
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.hazmat.primitives.serialization import pkcs12
    from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

    SIGNING_DIR.mkdir(parents=True, exist_ok=True)
    SIGNING_DIR.chmod(0o700)

    keychain_password = secrets.token_urlsafe(36)
    p12_password = secrets.token_urlsafe(36)
    key = rsa.generate_private_key(public_exponent=65537, key_size=3072)
    now = _dt.datetime.now(_dt.timezone.utc)
    subject = issuer = x509.Name([
        x509.NameAttribute(NameOID.COMMON_NAME, IDENTITY_LABEL),
        x509.NameAttribute(NameOID.ORGANIZATION_NAME, 'ILoveMyProjects'),
        x509.NameAttribute(NameOID.ORGANIZATIONAL_UNIT_NAME, 'ZorinMacBridge'),
    ])
    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(issuer)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - _dt.timedelta(days=1))
        .not_valid_after(now + _dt.timedelta(days=3650))
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .add_extension(
            x509.KeyUsage(
                digital_signature=True,
                content_commitment=False,
                key_encipherment=False,
                data_encipherment=False,
                key_agreement=False,
                key_cert_sign=True,
                crl_sign=True,
                encipher_only=None,
                decipher_only=None,
            ),
            critical=True,
        )
        .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.CODE_SIGNING]), critical=True)
        .add_extension(x509.SubjectKeyIdentifier.from_public_key(key.public_key()), critical=False)
        .sign(key, hashes.SHA256())
    )

    cert_pem = cert.public_bytes(serialization.Encoding.PEM)
    p12 = pkcs12.serialize_key_and_certificates(
        IDENTITY_LABEL.encode('utf-8'),
        key,
        cert,
        None,
        serialization.BestAvailableEncryption(p12_password.encode('utf-8')),
    )

    PASSWORD_PATH.write_text(keychain_password, encoding='utf-8')
    P12_PASSWORD_PATH.write_text(p12_password, encoding='utf-8')
    CERT_PATH.write_bytes(cert_pem)
    P12_PATH.write_bytes(p12)
    for path in (PASSWORD_PATH, P12_PASSWORD_PATH, CERT_PATH, P12_PATH):
        _chmod_private(path)

    if KEYCHAIN_PATH.exists():
        try:
            KEYCHAIN_PATH.unlink()
        except OSError:
            pass
    _run(['/usr/bin/security', 'create-keychain', '-p', keychain_password, str(KEYCHAIN_PATH)])
    _run(['/usr/bin/security', 'set-keychain-settings', '-lut', '600', str(KEYCHAIN_PATH)])
    _run(['/usr/bin/security', 'unlock-keychain', '-p', keychain_password, str(KEYCHAIN_PATH)])
    # Use an isolated app-owned keychain. On macOS 15,
    # `security set-key-partition-list` can fail with errSecItemNotFound even
    # immediately after a successful PKCS#12 import. Importing with `-A` avoids
    # that brittle mutation. The keychain is private (0700 directory / 0600
    # files), unlocked only when signing is needed, and never leaves this Mac.
    _run([
        '/usr/bin/security', 'import', str(P12_PATH), '-k', str(KEYCHAIN_PATH),
        '-P', p12_password, '-A', '-T', '/usr/bin/codesign', '-T', '/usr/bin/security',
        '-t', 'agg', '-f', 'pkcs12',
    ])
    _ensure_keychain_searchable(KEYCHAIN_PATH)
    private_key = _run(
        ['/usr/bin/security', 'find-key', '-t', 'private', str(KEYCHAIN_PATH)],
        check=False,
    )
    if private_key.returncode != 0:
        raise LocalSigningError('The persistent signing private key was not imported into its keychain.')


def ensure_local_identity() -> LocalIdentity:
    """Create or reuse one per-user code-signing identity on this Mac.

    The private key never leaves this Mac. No GitHub credentials or developer
    account are involved. Future app updates are re-signed locally with this same
    identity before they replace the installed app.
    """
    _require_macos_tools()
    SIGNING_DIR.mkdir(parents=True, exist_ok=True)
    SIGNING_DIR.chmod(0o700)

    required = (KEYCHAIN_PATH, PASSWORD_PATH, P12_PASSWORD_PATH, P12_PATH, CERT_PATH)
    if not all(p.exists() for p in required):
        _create_identity_files()

    keychain_password = PASSWORD_PATH.read_text(encoding='utf-8').strip()
    if not keychain_password:
        raise LocalSigningError('Local signing keychain password file is empty.')
    cert_pem = CERT_PATH.read_bytes()
    cert_sha1, cert_sha256, common_name = _load_cert_hashes(cert_pem)

    unlock = _run(
        ['/usr/bin/security', 'unlock-keychain', '-p', keychain_password, str(KEYCHAIN_PATH)],
        check=False,
    )
    if unlock.returncode != 0:
        # The files may have been partially restored. Recreate exactly once only
        # when the keychain cannot be unlocked with its matching saved password.
        for p in required:
            try:
                p.unlink()
            except OSError:
                pass
        _create_identity_files()
        keychain_password = PASSWORD_PATH.read_text(encoding='utf-8').strip()
        cert_pem = CERT_PATH.read_bytes()
        cert_sha1, cert_sha256, common_name = _load_cert_hashes(cert_pem)
        _run(['/usr/bin/security', 'unlock-keychain', '-p', keychain_password, str(KEYCHAIN_PATH)])

    _ensure_keychain_searchable(KEYCHAIN_PATH)

    cert_listing = _run(
        ['/usr/bin/security', 'find-certificate', '-a', '-Z', '-c', common_name, str(KEYCHAIN_PATH)]
    ).stdout or ''
    normalized = cert_listing.replace(' ', '').upper()
    if cert_sha1 not in normalized:
        raise LocalSigningError(
            'The persistent ZorinMacBridge signing certificate is not available in its private keychain.'
        )

    return LocalIdentity(
        keychain=KEYCHAIN_PATH,
        keychain_password=keychain_password,
        cert_sha1=cert_sha1,
        cert_sha256=cert_sha256,
        common_name=common_name,
    )



def identity_is_trusted(identity: LocalIdentity | None = None) -> bool:
    identity = identity or ensure_local_identity()
    proc = _run(
        ['/usr/bin/security', 'verify-cert', '-c', str(CERT_PATH), '-p', 'codeSign', '-k', str(identity.keychain)],
        check=False,
    )
    return proc.returncode == 0


def local_certificate_path() -> Path:
    ensure_local_identity()
    return CERT_PATH

def bundle_identifier(app: Path) -> str:
    info = app / 'Contents' / 'Info.plist'
    try:
        with info.open('rb') as fh:
            data = plistlib.load(fh)
    except Exception as exc:
        raise LocalSigningError(f'Cannot read {info}: {exc}') from exc
    return str(data.get('CFBundleIdentifier', ''))


def _write_requirement(identity: LocalIdentity, directory: Path) -> Path:
    path = directory / 'zorinmacbridge-local.req'
    path.write_text(identity.requirement + '\n', encoding='utf-8')
    return path


def sign_app_locally(app: Path) -> LocalIdentity:
    app = Path(app).resolve()
    if not app.is_dir() or app.name != APP_NAME:
        raise LocalSigningError(f'Expected {APP_NAME}, got: {app}')
    if bundle_identifier(app) != BUNDLE_ID:
        raise LocalSigningError(f'Refusing to sign unexpected bundle identifier: {bundle_identifier(app)!r}')

    identity = ensure_local_identity()
    if not identity_is_trusted(identity):
        raise LocalSigningError(
            'The persistent local signing certificate is not yet trusted for code signing. '
            'The installer/migration must authorize that one-time trust step before signing.'
        )
    _run(['/usr/bin/security', 'unlock-keychain', '-p', identity.keychain_password, str(identity.keychain)])
    try:
        # Remove a stale quarantine marker from the staged copy. The transport DMG is
        # still integrity-checked before this point; this prevents the locally signed
        # copy from inheriting a release-download quarantine identity that no longer
        # matches its new local signature.
        if Path('/usr/bin/xattr').exists():
            _run(['/usr/bin/xattr', '-dr', 'com.apple.quarantine', str(app)], check=False)

        nested: list[Path] = []
        contents = app / 'Contents'
        for path in contents.rglob('*'):
            if path.is_file() and path.suffix in ('.dylib', '.so'):
                nested.append(path)
        for path in sorted(nested, key=lambda p: len(str(p)), reverse=True):
            _run([
                '/usr/bin/codesign', '--force', '--timestamp=none',
                '--sign', identity.common_name, str(path),
            ])

        with tempfile.TemporaryDirectory(prefix='zmb-local-sign-') as tmp:
            req = _write_requirement(identity, Path(tmp))
            _run([
                '/usr/bin/codesign', '--force', '--deep', '--options', 'runtime', '--timestamp=none',
                '--sign', identity.common_name, '--requirements', str(req), str(app),
            ], timeout=180.0)

        verify_app_local_identity(app, identity)
        return identity
    finally:
        _run(['/usr/bin/security', 'lock-keychain', str(identity.keychain)], check=False)


def verify_app_local_identity(app: Path, identity: LocalIdentity | None = None) -> None:
    app = Path(app).resolve()
    identity = identity or ensure_local_identity()
    _run(['/usr/bin/codesign', '--verify', '--deep', '--strict', '--verbose=2', str(app)])
    with tempfile.TemporaryDirectory(prefix='zmb-local-verify-') as tmp:
        req = _write_requirement(identity, Path(tmp))
        _run(['/usr/bin/codesign', '--verify', '--strict', '-R', str(req), str(app)])


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
    """One-time migration for installs upgraded from pre-v0.6.0 releases.

    The old updater can install the v0.6 transport-signed app. Before the normal
    GUI starts, migrate /Applications to this Mac's persistent local identity.
    Trusting the local certificate, signing the staged app, and replacing the
    installed bundle are performed under one macOS administrator authorization.
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
    if not same_location:
        return
    if app_has_local_identity(app):
        return

    identity = ensure_local_identity()
    user_home = str(Path.home())
    executable = str(Path(sys.executable).resolve())

    with tempfile.TemporaryDirectory(prefix='zmb-identity-migration-') as tmp:
        stage_root = Path(tmp) / 'stage'
        stage_root.mkdir(parents=True, exist_ok=True)
        stage_app = stage_root / APP_NAME
        _run(['/usr/bin/ditto', str(app), str(stage_app)], timeout=180.0)

        # One administrator authorization covers the one-time certificate trust,
        # local signing of the staged app, and replacement in /Applications.
        q = shlex.quote
        shell = ' && '.join([
            f'/usr/bin/security add-trusted-cert -d -r trustRoot -p codeSign -k /Library/Keychains/System.keychain {q(str(CERT_PATH))}',
            f'/usr/bin/env HOME={q(user_home)} {q(executable)} --local-sign-app {q(str(stage_app))}',
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
                'Could not migrate the installed app to its persistent local code identity.\n'
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

