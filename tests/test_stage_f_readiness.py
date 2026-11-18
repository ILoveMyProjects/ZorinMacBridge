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
from mac_requirements import evaluate_prelogin_requirements, evaluate_setup_requirements, missing_setup_requirements, prelogin_readiness_state

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
        'virtual_hid_input_ready': True,
        'prelogin_capture_proof_ready': True,
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

    # Stage F proof is a separate reboot-login verification mode. It must not
    # block normal Aqua READY.
    for key, field in (
        ('prelogin_virtual_hid', 'virtual_hid_input_ready'),
        ('prelogin_agent', 'prelogin_agent_contract_ready'),
        ('prelogin_capture_proof', 'prelogin_capture_proof_ready'),
    ):
        state = _base_state(**{field: False})
        assert not missing_setup_requirements(_requirements(state))
        missing = {item.key for item in missing_setup_requirements(evaluate_prelogin_requirements(state))}
        assert key in missing

    assert prelogin_readiness_state(_base_state()) == 'VERIFIED'
    assert prelogin_readiness_state(_base_state(prelogin_capture_proof_ready=False)) == 'UNVERIFIED'
    assert prelogin_readiness_state(_base_state(prelogin_agent_contract_ready=False)) == 'NOT_READY'
    assert prelogin_readiness_state(_base_state(virtual_hid_input_ready=False)) == 'NOT_READY'

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
            assert state['prelogin_capture_proof_ready'] is True

        proof.write_text(
            f'app_version={CURRENT_VERSION}\n'
            'app_cdhash=abc123\n'
            'overall=PASS\n'
            'capture_result=PASS\n'
            'input_result=PASS\n'
            'input_selected_backend=userdevice\n'
            'input_corehid_result=FAIL_OPEN\n'
            'input_userdevice_result=PASS\n'
            'input_iohidsystem_result=FAIL\n'
            'input_quartz_result=FAIL\n'
            'input_legacy_result=PASS\n',
            encoding='utf-8',
        )
        with mock.patch.object(mac_admin_setup, 'CAPABILITY_STATUS', proof), \
             mock.patch.object(mac_admin_setup, '_installed_app_identity', return_value=(CURRENT_VERSION, 'abc123')):
            state = mac_admin_setup._prelogin_probe_state()
            assert state['prelogin_probe_current'] is True
            assert state['prelogin_probe_ready'] is True
            assert state['prelogin_probe_input_selected'] == 'userdevice'
            assert state['prelogin_probe_input_userdevice'] == 'PASS'
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
    assert 'virtual_hid_input_ready' in gui
    assert 'virtual-hid={vhid}' in gui
    assert 'virtual HID' in gui
    assert 'LoginWindow capture has not been physically verified' in gui
    assert 'PRELOGIN_AFTER_REBOOT=' in terminal
    assert 'Virtual HID keyboard/mouse is not active yet.' in terminal
    print('Stage F pre-login readiness gate tests: OK')


if __name__ == '__main__':
    main()
