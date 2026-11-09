from __future__ import annotations

import datetime as _dt
import hashlib
import os
import plistlib
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

BUNDLE_ID = 'com.ilovemyprojects.zorinmacbridge.server'
PROFILE_NAME = 'embedded.provisionprofile'
REQUIRED_RESTRICTED_ENTITLEMENTS = (
    'com.apple.developer.persistent-content-capture',
    'com.apple.developer.hid.virtual.device',
)


class ReleaseSigningError(RuntimeError):
    pass


@dataclass(frozen=True)
class ReleaseProfileInfo:
    name: str
    team_identifier: str
    application_identifier: str
    expires_at: _dt.datetime


def _run(args: list[str], *, timeout: float = 60.0) -> subprocess.CompletedProcess[str]:
    proc = subprocess.run(
        args,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
        timeout=timeout,
    )
    if proc.returncode != 0:
        raise ReleaseSigningError(
            f"Command failed ({proc.returncode}): {' '.join(args)}\n{(proc.stdout or '').strip()}"
        )
    return proc


def _require_macos() -> None:
    if sys.platform != 'darwin':
        raise ReleaseSigningError('Release signing verification requires macOS.')


def _extract_plist(text: str, label: str) -> dict[str, Any]:
    starts = [pos for marker in ('<?xml', '<plist', 'bplist00') if (pos := text.find(marker)) >= 0]
    if not starts:
        raise ReleaseSigningError(f'{label} did not contain a property list.')
    payload = text[min(starts):].encode('utf-8', errors='replace')
    try:
        value = plistlib.loads(payload)
    except Exception as exc:
        raise ReleaseSigningError(f'Could not parse {label} property list: {exc}') from exc
    if not isinstance(value, dict):
        raise ReleaseSigningError(f'{label} property list is not a dictionary.')
    return value


def decode_provisioning_profile(profile_path: Path) -> dict[str, Any]:
    _require_macos()
    profile_path = Path(profile_path).resolve()
    if not profile_path.is_file():
        raise ReleaseSigningError(f'Provisioning profile not found: {profile_path}')
    proc = _run(['/usr/bin/security', 'cms', '-D', '-i', str(profile_path)])
    return _extract_plist(proc.stdout or '', 'provisioning profile')


def _normalise_expiration(value: Any) -> _dt.datetime:
    if not isinstance(value, _dt.datetime):
        raise ReleaseSigningError('Provisioning profile has no valid ExpirationDate.')
    if value.tzinfo is None:
        return value.replace(tzinfo=_dt.timezone.utc)
    return value.astimezone(_dt.timezone.utc)


def validate_profile_payload(
    payload: Mapping[str, Any],
    *,
    bundle_id: str = BUNDLE_ID,
    now: _dt.datetime | None = None,
    certificate_sha1: str | None = None,
) -> ReleaseProfileInfo:
    entitlements = payload.get('Entitlements')
    if not isinstance(entitlements, Mapping):
        raise ReleaseSigningError('Provisioning profile does not contain Entitlements.')

    team_value = entitlements.get('com.apple.developer.team-identifier')
    if not isinstance(team_value, str) or not team_value.strip():
        top_level_team = payload.get('TeamIdentifier')
        if isinstance(top_level_team, list) and top_level_team and isinstance(top_level_team[0], str):
            team_value = top_level_team[0]
    if not isinstance(team_value, str) or not team_value.strip():
        raise ReleaseSigningError('Provisioning profile does not identify an Apple Developer team.')
    team_id = team_value.strip()

    app_id = entitlements.get('com.apple.application-identifier')
    expected_app_id = f'{team_id}.{bundle_id}'
    if app_id != expected_app_id:
        raise ReleaseSigningError(
            f'Provisioning profile application identifier mismatch: expected {expected_app_id!r}, got {app_id!r}.'
        )

    for key in REQUIRED_RESTRICTED_ENTITLEMENTS:
        if entitlements.get(key) is not True:
            raise ReleaseSigningError(f'Provisioning profile does not authorize required entitlement: {key}')

    # A Developer ID distribution profile applies to all Macs. Reject development
    # profiles tied to individual devices so a release cannot accidentally ship one.
    if payload.get('ProvisionsAllDevices') is not True:
        raise ReleaseSigningError('Provisioning profile is not an all-devices Developer ID distribution profile.')
    if payload.get('ProvisionedDevices'):
        raise ReleaseSigningError('Provisioning profile is device-limited and must not be used for a public release.')

    expires = _normalise_expiration(payload.get('ExpirationDate'))
    current = now or _dt.datetime.now(_dt.timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=_dt.timezone.utc)
    else:
        current = current.astimezone(_dt.timezone.utc)
    if expires <= current:
        raise ReleaseSigningError(f'Provisioning profile expired at {expires.isoformat()}.')

    certs = payload.get('DeveloperCertificates')
    if not isinstance(certs, list) or not certs:
        raise ReleaseSigningError('Provisioning profile contains no authorized signing certificates.')
    if certificate_sha1:
        wanted = certificate_sha1.replace(' ', '').upper()
        allowed = {hashlib.sha1(bytes(cert)).hexdigest().upper() for cert in certs if isinstance(cert, (bytes, bytearray))}
        if wanted not in allowed:
            raise ReleaseSigningError('Selected Developer ID signing certificate is not authorized by the provisioning profile.')

    return ReleaseProfileInfo(
        name=str(payload.get('Name') or '<unnamed>'),
        team_identifier=team_id,
        application_identifier=expected_app_id,
        expires_at=expires,
    )


def verify_provisioning_profile(profile_path: Path, certificate_sha1: str | None = None) -> ReleaseProfileInfo:
    return validate_profile_payload(decode_provisioning_profile(profile_path), certificate_sha1=certificate_sha1)


def release_entitlements_from_profile(payload: Mapping[str, Any]) -> dict[str, Any]:
    info = validate_profile_payload(payload)
    return {
        'com.apple.application-identifier': info.application_identifier,
        'com.apple.developer.team-identifier': info.team_identifier,
        'com.apple.developer.persistent-content-capture': True,
        'com.apple.developer.hid.virtual.device': True,
    }


def write_release_entitlements(profile_path: Path, output_path: Path) -> ReleaseProfileInfo:
    payload = decode_provisioning_profile(profile_path)
    info = validate_profile_payload(payload)
    entitlements = release_entitlements_from_profile(payload)
    output_path = Path(output_path).resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open('wb') as handle:
        plistlib.dump(entitlements, handle, fmt=plistlib.FMT_XML, sort_keys=True)
    return info


def _codesign_entitlements(app: Path) -> dict[str, Any]:
    proc = _run(['/usr/bin/codesign', '--display', '--entitlements', '-', '--xml', str(app)], timeout=120.0)
    return _extract_plist(proc.stdout or '', 'code-signing entitlements')


def _codesign_details(app: Path) -> str:
    return _run(['/usr/bin/codesign', '-dvvv', str(app)], timeout=120.0).stdout or ''


def _bundle_id(app: Path) -> str:
    try:
        with (app / 'Contents' / 'Info.plist').open('rb') as handle:
            info = plistlib.load(handle)
    except Exception as exc:
        raise ReleaseSigningError(f'Could not read app Info.plist: {exc}') from exc
    return str(info.get('CFBundleIdentifier') or '')


def verify_release_app(app: Path) -> ReleaseProfileInfo:
    _require_macos()
    app = Path(app).resolve()
    if not app.is_dir():
        raise ReleaseSigningError(f'Application bundle not found: {app}')
    if _bundle_id(app) != BUNDLE_ID:
        raise ReleaseSigningError(f'Unexpected bundle identifier: {_bundle_id(app)!r}')

    _run(['/usr/bin/codesign', '--verify', '--deep', '--strict', '--verbose=2', str(app)], timeout=180.0)
    details = _codesign_details(app)
    if 'Signature=adhoc' in details:
        raise ReleaseSigningError('Release app is ad-hoc signed; Developer ID Application signing is required.')
    if 'Authority=Developer ID Application:' not in details:
        raise ReleaseSigningError('Release app is not signed by a Developer ID Application identity.')

    profile_path = app / 'Contents' / PROFILE_NAME
    profile = decode_provisioning_profile(profile_path)
    profile_info = validate_profile_payload(profile)

    signed = _codesign_entitlements(app)
    expected = release_entitlements_from_profile(profile)
    for key, value in expected.items():
        if signed.get(key) != value:
            raise ReleaseSigningError(
                f'Signed app entitlement mismatch for {key}: expected {value!r}, got {signed.get(key)!r}.'
            )

    team_line = next((line for line in details.splitlines() if line.startswith('TeamIdentifier=')), '')
    actual_team = team_line.split('=', 1)[1].strip() if '=' in team_line else ''
    if actual_team != profile_info.team_identifier:
        raise ReleaseSigningError(
            f'Code-signing TeamIdentifier {actual_team!r} does not match profile team {profile_info.team_identifier!r}.'
        )
    return profile_info


def app_has_release_identity(app: Path) -> bool:
    try:
        verify_release_app(app)
        return True
    except Exception:
        return False


def release_identity_summary(app: Path) -> str:
    info = verify_release_app(app)
    return f'Developer ID · team {info.team_identifier} · {info.name}'


def _main(argv: list[str]) -> int:
    if len(argv) in {3, 4} and argv[1] == '--verify-profile':
        info = verify_provisioning_profile(Path(argv[2]), argv[3] if len(argv) == 4 else None)
        print(f'PROFILE_OK=1')
        print(f'PROFILE_NAME={info.name}')
        print(f'TEAM_ID={info.team_identifier}')
        print(f'APPLICATION_IDENTIFIER={info.application_identifier}')
        print(f'EXPIRES_AT={info.expires_at.isoformat()}')
        return 0
    if len(argv) == 4 and argv[1] == '--write-entitlements':
        info = write_release_entitlements(Path(argv[2]), Path(argv[3]))
        print(f'ENTITLEMENTS_OK=1')
        print(f'TEAM_ID={info.team_identifier}')
        return 0
    if len(argv) == 3 and argv[1] == '--verify-app':
        info = verify_release_app(Path(argv[2]))
        print('RELEASE_SIGNING_OK=1')
        print(f'TEAM_ID={info.team_identifier}')
        print(f'PROFILE_NAME={info.name}')
        return 0
    raise SystemExit(
        'usage: mac_release_signing.py --verify-profile PROFILE [CERT_SHA1] | '
        '--write-entitlements PROFILE OUT | --verify-app APP'
    )


if __name__ == '__main__':
    raise SystemExit(_main(sys.argv))
