#!/usr/bin/env python3
from pathlib import Path

from zmb_recovery import classify_transport_error, parse_recovery_report, recovery_summary, render_recovery_report

ROOT = Path(__file__).resolve().parents[1]


def test_parser():
    text = """protocol=1
checked_at=1700000000
overall=recovery-needed
issue_count=3
issue=critical|APP_MISSING|1|Verified backup can be restored.
issue=warning|DESKTOP_PORT_DOWN|1|Desktop port is not listening.
issue=info|GUI_SESSION_ABSENT|0|No GUI session.
"""
    meta, issues = parse_recovery_report(text)
    assert meta["protocol"] == "1"
    assert meta["overall"] == "recovery-needed"
    assert [x.code for x in issues] == ["APP_MISSING", "DESKTOP_PORT_DOWN", "GUI_SESSION_ABSENT"]
    assert issues[0].repairable is True
    assert issues[-1].repairable is False
    summary = recovery_summary(meta, issues)
    assert "1 critical" in summary and "1 warning" in summary and "2 auto-repairable" in summary
    rendered = render_recovery_report(meta, issues)
    assert "[CRITICAL] APP_MISSING [AUTO]" in rendered
    assert "[INFO] GUI_SESSION_ABSENT [MANUAL]" in rendered


def test_transport_classification():
    assert classify_transport_error("Host key verification failed").code == "SSH_HOST_KEY_CHANGED"
    assert classify_transport_error("ssh: connect to host 1.2.3.4 port 22: Connection refused").code == "SSH_CONNECTION_REFUSED"
    assert classify_transport_error("No route to host").code == "NETWORK_UNREACHABLE"
    assert classify_transport_error("Timed out after 12s while running ssh").code == "SSH_TIMEOUT"
    assert classify_transport_error("Permission denied (publickey)").code == "SSH_AUTH_FAILED"


def test_source_guards():
    admin = (ROOT / "native/macos/admin/zmb-admin").read_text(encoding="utf-8")
    gate = (ROOT / "native/macos/admin/zmb-admin-ssh").read_text(encoding="utf-8")
    installer = (ROOT / "native/macos/admin/install-admin.sh").read_text(encoding="utf-8")
    daemon = (ROOT / "native/macos/admin/zmb-system-daemon").read_text(encoding="utf-8")
    backend = (ROOT / "zmb_admin_backend.py").read_text(encoding="utf-8")
    page = (ROOT / "zmb_system_page.py").read_text(encoding="utf-8")
    workflow = (ROOT / ".github/workflows/release.yml").read_text(encoding="utf-8")
    ci = (ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")

    assert '"diagnose", "recover-all"' in backend
    assert '"zmb-admin diagnose")' in gate
    assert '"zmb-admin recover-all")' in gate
    assert '/usr/local/sbin/zmb-admin diagnose' in installer
    assert '/usr/local/sbin/zmb-admin recover-all' in installer

    assert 'diagnose_cmd()' in admin
    assert 'recover_all_cmd()' in admin
    assert 'recover_prelogin_cmd()' in admin
    assert 'PRELOGIN_NOT_RUNNING' in admin
    assert 'PRELOGIN_PORT_DOWN' in admin
    assert 'APP_SIGNATURE_INVALID' in admin
    assert 'UPDATE_LOCK_STALE' in admin
    assert 'SYSTEM_MDNS_DOWN' in admin
    assert 'DESKTOP_PORT_DOWN' in admin
    assert 'LAST_UPDATE_FAILED' in admin
    assert 'UPDATE_DAEMON_DOWN' in admin and 'UPDATE_PLIST_INVALID' in admin
    assert 'LAUNCH_AGENT_INVALID' in admin
    assert 'ssh_transport_ready' in admin and '/usr/bin/nc -G 1 -z 127.0.0.1 22' in admin
    assert 'if app_signature_ok; then' in admin
    assert 'APP_UNTRUSTED' in admin
    assert '[ "$count" -ge 3 ]' in admin
    assert 'AUTOHEAL_PORT_STUCK' in admin
    assert 'zmb recovery events' in admin
    assert 'RECOVERY_LOG="/var/log/zorinmacbridge-recovery.log"' in admin

    assert 'ADVERTISER_PID_FILE="$HEARTBEAT_DIR/advertiser.pid"' in daemon
    assert 'printf \'%s\\n\' "$ADVERTISER_PID" > "$ADVERTISER_PID_FILE"' in daemon
    assert '/bin/rm -f "$ADVERTISER_PID_FILE"' in daemon

    assert 'P6 diagnostics and recovery' in page
    assert 'Run diagnostics' in page
    assert 'Recover all safe layers' in page
    assert 'parse_recovery_report' in page
    assert 'classify_transport_error' in page
    assert '_confirm_recover_all' in page

    assert 'zmb_recovery.py' in workflow
    assert 'scripts/run-tests.sh' in workflow
    assert 'scripts/run-tests.sh' in ci

    # P6 must stay closed: no generic shell action is added to the P0 gateway.
    assert 'zmb-admin exec' not in gate
    assert 'zmb-admin shell' not in gate
    assert 'bash -c' not in gate


def main():
    test_parser()
    test_transport_classification()
    test_source_guards()
    print("P6 recovery tests: OK")


if __name__ == "__main__":
    main()
