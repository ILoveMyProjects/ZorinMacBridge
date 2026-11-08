from __future__ import annotations

import plistlib
import tempfile
import sys
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import mac_admin_setup
CURRENT_VERSION = (ROOT / 'VERSION').read_text(encoding='utf-8').strip()
from mac_requirements import evaluate_setup_requirements, missing_setup_requirements

def _base_state(**changes):
    state = {
        'installed': True,
        'daemon_loaded': True,
        'updater_loaded': True,
        'remote_login': 'on',
        'ssh_port_ready': True,
        'ssh_host_key_ready': True,
        'prelogin_agent_exists': True,
        'prelogin_agent_contract_ready': True,
        'prelogin_probe_ready': True,
    }
    state.update(changes)
    return state


def _requirements(state):
    return evaluate_setup_requirements(
        screen_recording=True,
        accessibility=True,
        session_password_configured=True,
        admin_state=state,
        launch_at_login=True,
        auto_start_server=True,
    )


def main() -> None:
    assert not missing_setup_requirements(_requirements(_base_state()))

    for key, field in (
        ('prelogin_agent', 'prelogin_agent_contract_ready'),
        ('prelogin_capability_proof', 'prelogin_probe_ready'),
    ):
        missing = {item.key for item in missing_setup_requirements(_requirements(_base_state(**{field: False})))}
        assert key in missing

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        proof = root / 'proof'
        proof.write_text(
            f'app_version={CURRENT_VERSION}\n'
            'app_cdhash=abc123\n'
            'overall=PASS\n'
            'capture_result=PASS\n'
            'input_corehid_result=PASS\n'
            'input_legacy_result=FAIL\n',
            encoding='utf-8',
        )
        with mock.patch.object(mac_admin_setup, 'CAPABILITY_STATUS', proof), \
             mock.patch.object(mac_admin_setup, '_installed_app_identity', return_value=(CURRENT_VERSION, 'abc123')):
            state = mac_admin_setup._prelogin_probe_state()
            assert state['prelogin_probe_current'] is True
            assert state['prelogin_probe_ready'] is True
        with mock.patch.object(mac_admin_setup, 'CAPABILITY_STATUS', proof), \
             mock.patch.object(mac_admin_setup, '_installed_app_identity', return_value=(CURRENT_VERSION, 'different')):
            state = mac_admin_setup._prelogin_probe_state()
            assert state['prelogin_probe_current'] is False
            assert state['prelogin_probe_ready'] is False

        app_bin = root / 'ZorinMacBridge Server.app/Contents/MacOS/ZorinMacBridge Server'
        app_bin.parent.mkdir(parents=True)
        app_bin.write_text('', encoding='utf-8')
        plist = root / 'prelogin.plist'
        plist.write_bytes(plistlib.dumps({
            'Label': mac_admin_setup.PRELOGIN_LABEL,
            'ProgramArguments': [str(app_bin), '--prelogin-server'],
            'LimitLoadToSessionType': 'LoginWindow',
            'RunAtLoad': True,
            'KeepAlive': True,
            'ProcessType': 'Interactive',
        }))
        with mock.patch.object(mac_admin_setup, 'PRELOGIN_PLIST', plist), \
             mock.patch.object(mac_admin_setup, 'APP_BIN', app_bin):
            assert mac_admin_setup._prelogin_agent_contract_ready() is True
        broken = plistlib.loads(plist.read_bytes())
        broken['LimitLoadToSessionType'] = 'Aqua'
        plist.write_bytes(plistlib.dumps(broken))
        with mock.patch.object(mac_admin_setup, 'PRELOGIN_PLIST', plist), \
             mock.patch.object(mac_admin_setup, 'APP_BIN', app_bin):
            assert mac_admin_setup._prelogin_agent_contract_ready() is False

    admin = (ROOT / 'native/macos/admin/zmb-admin').read_text(encoding='utf-8')
    gui = (ROOT / 'mac_server_gui.py').read_text(encoding='utf-8')
    terminal = (ROOT / 'mac_terminal_setup.py').read_text(encoding='utf-8')
    assert 'CAPABILITY_STATUS_FILE="$ADMIN_DIR/prelogin-capability-status"' in admin
    assert '/bin/rm -f "$CAPABILITY_STATUS_FILE"' in admin
    assert "printf 'app_version=%s\\n'" in admin
    assert "printf 'app_cdhash=%s\\n'" in admin
    assert "printf 'prelogin_probe_current=%s\\n'" in admin
    assert "printf 'prelogin_probe_ready=%s\\n'" in admin
    assert 'REQUIRED: production pre-login signing' not in gui
    assert 'Signing: {signing} (informational)' in gui
    assert 'REQUIRED: verify LoginWindow capture + input' in gui
    assert 'LoginWindow capture/input has not been proven for this exact app build' in terminal
    print('Stage F pre-login readiness gate tests: OK')


if __name__ == '__main__':
    main()
