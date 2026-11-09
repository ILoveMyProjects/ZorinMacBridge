from __future__ import annotations

import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from mac_requirements import evaluate_prelogin_requirements, prelogin_readiness_state
from updates import _verify_macos_update_identity

ROOT = Path(__file__).resolve().parents[1]


def _state(**changes):
    value = {
        'installed': True,
        'prelogin_release_identity_ready': True,
        'prelogin_agent_exists': True,
        'prelogin_agent_contract_ready': True,
        'prelogin_probe_ready': True,
    }
    value.update(changes)
    return value


# Reboot-to-LoginWindow readiness now has three independent gates: verified
# managed signing, the LoginWindow agent, and a current physical capability proof.
req = {item.key: item for item in evaluate_prelogin_requirements(_state())}
assert req['prelogin_release_identity'].ready is True
assert req['prelogin_agent'].ready is True
assert req['prelogin_capability_proof'].ready is True
assert prelogin_readiness_state(_state()) == 'VERIFIED'
assert prelogin_readiness_state(_state(prelogin_release_identity_ready=False)) == 'NOT_READY'
assert prelogin_readiness_state(_state(prelogin_probe_ready=False)) == 'UNVERIFIED'

# A Developer ID/pre-login-capable installation must never be silently replaced
# by a free/ad-hoc update, because that would remove the managed entitlements.
with tempfile.TemporaryDirectory() as tmp:
    root = Path(tmp)
    current = root / 'current.app'
    new = root / 'new.app'
    current.mkdir()
    new.mkdir()

    def has_release_identity(path):
        return Path(path) == current

    with mock.patch('mac_release_signing.app_has_release_identity', side_effect=has_release_identity), \
         mock.patch('mac_release_signing.verify_release_app'):
        try:
            _verify_macos_update_identity(current, new, None)
            raise AssertionError('Developer ID -> ad-hoc downgrade was accepted')
        except RuntimeError as exc:
            assert 'Refusing to replace a pre-login-capable Developer ID installation' in str(exc)

    # Migration in the other direction remains allowed: Aqua-only -> managed.
    def new_has_release_identity(path):
        return Path(path) == new

    with mock.patch('mac_release_signing.app_has_release_identity', side_effect=new_has_release_identity), \
         mock.patch('mac_release_signing.verify_release_app', return_value=SimpleNamespace(team_identifier='TEAM')):
        assert _verify_macos_update_identity(current, new, None) == 'developer-id'

installer = (ROOT / 'install-macos.sh').read_text(encoding='utf-8')
admin = (ROOT / 'native/macos/admin/zmb-admin').read_text(encoding='utf-8')
admin_install = (ROOT / 'native/macos/admin/install-admin.sh').read_text(encoding='utf-8')
source_gate = (ROOT / 'scripts/verify-release-source.sh').read_text(encoding='utf-8')

assert 'EXISTING_PRELOGIN_CAPABLE=0' in installer
assert 'Refusing to replace a pre-login-capable Developer ID installation with a free/ad-hoc build.' in installer
assert '--verify-release-app "$APP_PATH"' in admin
assert 'PRELOGIN_MANAGED_CAPABILITIES=ready' in admin_install
assert 'pre-login READY requires Apple-managed Developer ID/provisioning' in source_gate

print('managed pre-login capability gate regression checks passed')
