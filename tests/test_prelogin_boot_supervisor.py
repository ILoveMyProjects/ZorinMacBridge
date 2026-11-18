from pathlib import Path
import plistlib

ROOT = Path(__file__).resolve().parents[1]
admin = (ROOT / 'native/macos/admin/zmb-admin').read_text(encoding='utf-8')
daemon = (ROOT / 'native/macos/admin/zmb-system-daemon').read_text(encoding='utf-8')

with (ROOT / 'native/macos/admin/com.ilovemyprojects.zorinmacbridge.admin.plist').open('rb') as fh:
    p0 = plistlib.load(fh)
assert p0['RunAtLoad'] is True
assert p0['KeepAlive'] is True
assert p0['ProgramArguments'] == ['/usr/local/libexec/zmb-system-daemon']

# P0 must no longer abandon recovery merely because Aqua/gui/<uid> is absent.
assert 'autoheal_prelogin()' in admin
assert 'if ! user_session_available "$uid"; then' in admin
assert 'autoheal_prelogin' in admin.split('autoheal() {', 1)[1]
assert 'PRELOGIN_PORT_FAIL_FILE="$HEARTBEAT_DIR/prelogin-port-fail-count"' in admin
assert 'prelogin_job_loaded()' in admin
assert 'launchctl print "loginwindow/${PRELOGIN_LABEL}"' in admin
assert 'repair_prelogin_agent' in admin
assert 'load_prelogin_current_session' in admin
assert 'restart_prelogin_current_session' in admin
assert 'wait_prelogin_port "$port" 20' in admin
assert 'PRELOGIN_AUTOHEAL_READY' in admin
assert 'PRELOGIN_AUTOHEAL_RECOVERED' in admin

# Boot supervision must converge promptly while remaining bounded.
assert '/bin/sleep 5' in daemon
assert '/bin/sleep 15' not in daemon

# This stage must not redirect LoginWindow recovery through Aqua/gui/0.
assert 'gui/0/${PRELOGIN_LABEL}' not in admin

print('boot pre-login supervisor regression tests: OK')
