from pathlib import Path

from mac_requirements import evaluate_prelogin_requirements, evaluate_setup_requirements, missing_setup_requirements

ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    ready_state = {
        'installed': True,
        'daemon_loaded': True,
        'updater_loaded': True,
        'remote_login': 'on',
        'ssh_port_ready': True,
        'ssh_host_key_ready': True,
        'prelogin_agent_exists': True,
        'prelogin_agent_contract_ready': True,
        'prelogin_release_identity_ready': True,
        'prelogin_probe_ready': True,
    }
    requirements = evaluate_setup_requirements(
        screen_recording=True,
        accessibility=True,
        session_password_configured=True,
        admin_state=ready_state,
        launch_at_login=True,
        auto_start_server=True,
    )
    assert not missing_setup_requirements(requirements)

    broken = dict(ready_state)
    broken.update(remote_login='off', ssh_port_ready=False, ssh_host_key_ready=False)
    requirements = evaluate_setup_requirements(
        screen_recording=False,
        accessibility=False,
        session_password_configured=False,
        admin_state=broken,
        launch_at_login=False,
        auto_start_server=False,
    )
    missing = {item.key for item in missing_setup_requirements(requirements)}
    assert 'screen_recording' in missing
    assert 'accessibility' in missing
    assert 'session_password' in missing
    assert 'remote_login' in missing
    assert 'ssh_host_key' in missing
    assert 'launch_at_login' in missing
    assert 'auto_start_server' in missing
    assert 'ssh_transport' not in missing  # Remote Login OFF is the primary failure.

    prelogin_broken = dict(ready_state)
    prelogin_broken.update(
        prelogin_agent_contract_ready=False,
        prelogin_probe_ready=False,
    )
    requirements = evaluate_setup_requirements(
        screen_recording=True,
        accessibility=True,
        session_password_configured=True,
        admin_state=prelogin_broken,
        launch_at_login=True,
        auto_start_server=True,
    )
    # Pre-login verification is intentionally separate from normal Aqua READY.
    assert not missing_setup_requirements(requirements)
    prelogin_missing = {item.key for item in missing_setup_requirements(evaluate_prelogin_requirements(prelogin_broken))}
    assert {'prelogin_agent', 'prelogin_capability_proof'} <= prelogin_missing
    assert 'prelogin_release_identity' not in prelogin_missing

    signing_broken = dict(ready_state)
    signing_broken['prelogin_release_identity_ready'] = False
    signing_missing = {item.key for item in missing_setup_requirements(evaluate_prelogin_requirements(signing_broken))}
    assert 'prelogin_release_identity' in signing_missing

    gui = (ROOT / 'mac_server_gui.py').read_text(encoding='utf-8')
    terminal = (ROOT / 'mac_terminal_setup.py').read_text(encoding='utf-8')
    admin = (ROOT / 'mac_admin_setup.py').read_text(encoding='utf-8')
    installer = (ROOT / 'native/macos/admin/install-admin.sh').read_text(encoding='utf-8')
    install_macos = (ROOT / 'install-macos.sh').read_text(encoding='utf-8')
    ci = (ROOT / '.github/workflows/ci.yml').read_text(encoding='utf-8')
    release = (ROOT / '.github/workflows/release.yml').read_text(encoding='utf-8')

    assert 'Required setup for normal Aqua operation' in gui
    assert 'Fix next REQUIRED item' in gui
    assert 'Open Remote Login Settings' in gui
    assert '#c62828' in gui
    assert 'REQUIRED: setup incomplete' in gui
    assert 'READY — normal Aqua/Desktop/Files/System/Terminal operation is available.' in gui
    assert 'required physical LoginWindow capture + CoreHID proof' in gui
    assert 'Required setup incomplete' in gui
    assert "'ssh_port_ready': ssh_port_ready" in admin
    assert "'ssh_host_key_ready': bool(ssh_host_fingerprints())" in admin
    assert "'prelogin_agent_contract_ready': _prelogin_agent_contract_ready()" in admin
    assert "'release_signing_mode': signing_mode" in admin
    assert "'prelogin_release_identity_ready': signing_mode == 'developer-id'" in admin
    assert "'prelogin_probe_ready': ready" in admin
    assert "if 'remote login: on' in remote_text" in admin
    assert "elif 'remote login: off' in remote_text" in admin
    assert "if 'on' in remote_text" not in admin
    assert '/usr/bin/ssh-keygen -A' in installer
    assert "_required('macOS Remote Login (SSH) is OFF.')" in terminal
    assert "prefix = '\\033[1;31mREQUIRED\\033[0m'" in terminal
    assert 'SETUP_COMPLETE=' in terminal
    assert 'REQUIRED_SETUP_BEGIN' in terminal and 'REQUIRED_SETUP_END' in terminal
    assert 'v0.14.0 or newer' in install_macos
    assert 'requires v0.14.0 or newer' in install_macos
    assert 'scripts/run-tests.sh' in ci
    assert 'scripts/run-tests.sh' in release
    print('P7 required-setup tests: OK')


if __name__ == '__main__':
    main()
