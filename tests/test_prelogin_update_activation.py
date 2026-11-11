from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ADMIN = (ROOT / 'native/macos/admin/zmb-admin').read_text(encoding='utf-8')

assert 'prelogin_process_pids()' in ADMIN
assert 'restart_prelogin_current_session()' in ADMIN
assert '/bin/kill -TERM "$pid"' in ADMIN
assert '/bin/kill -KILL "$pid"' in ADMIN
assert 'if ! restart_prelogin_current_session; then' in ADMIN
assert 'new app installed but LoginWindow pre-login restart failed' in ADMIN
assert 'new LoginWindow process did not reopen Desktop port $port' in ADMIN
assert 'write_update_status "$latest" "$latest" 0 activation-failed' in ADMIN

# update-now must activate the replacement before it can publish installed/success.
update_start = ADMIN.index('update_now_cmd() {')
update_end = ADMIN.index('\nset_auto_update_cmd() {', update_start)
update_body = ADMIN[update_start:update_end]
activate_at = update_body.index('restart_prelogin_current_session')
installed_status_at = update_body.index('write_update_status "$latest" "$latest" 0 installed ""')
success_at = update_body.index("printf 'ok=updated")
assert activate_at < installed_status_at < success_at

# Merely seeing a process must not be the update activation path; old PIDs are captured first.
restart_start = ADMIN.index('restart_prelogin_current_session() {')
restart_end = ADMIN.index('\nload_prelogin_current_session() {', restart_start)
restart_body = ADMIN[restart_start:restart_end]
assert 'old_pids="$(prelogin_process_pids)"' in restart_body
assert 'for pid in $old_pids' in restart_body
assert 'while [ "$attempt" -le 40 ]' in restart_body

print('pre-login update activation regression tests: OK')
