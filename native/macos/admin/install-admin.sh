#!/bin/bash
set -euo pipefail

if [ "$(uname -s)" != "Darwin" ]; then
  echo "macOS only" >&2
  exit 1
fi
if [ "$(uname -m)" != "arm64" ]; then
  echo "ZorinMacBridge P0 supports Apple Silicon (arm64) Macs only." >&2
  exit 1
fi
if [ "$(/usr/bin/id -u)" -ne 0 ]; then
  echo "This installer must run with administrator privileges." >&2
  exit 77
fi

HERE="$(cd "$(dirname "$0")" && pwd)"
TARGET_USER="${1:-}"
SERVER_ID="${2:-}"
SERVER_NAME="${3:-Mac}"
LOCAL_IDENTITY_TOKEN="${4:-}"
case "$TARGET_USER" in
  ""|root|loginwindow|_mbsetupuser) echo "Invalid target user" >&2; exit 64 ;;
esac
if ! printf '%s' "$TARGET_USER" | /usr/bin/grep -Eq '^[A-Za-z0-9._-]+$'; then
  echo "Unsupported macOS short username: $TARGET_USER" >&2
  exit 64
fi
if ! printf '%s' "$SERVER_ID" | /usr/bin/grep -Eq '^[A-Za-z0-9._:-]{8,128}$'; then
  echo "Invalid ZorinMacBridge server id" >&2
  exit 64
fi
if ! printf '%s' "$LOCAL_IDENTITY_TOKEN" | /usr/bin/grep -Eq '^[0-9a-fA-F]{64}$'; then
  echo "Invalid stable local identity token" >&2
  exit 64
fi
LOCAL_IDENTITY_TOKEN="$(printf '%s' "$LOCAL_IDENTITY_TOKEN" | /usr/bin/tr 'A-F' 'a-f')"
SERVER_NAME="$(printf '%s' "$SERVER_NAME" | /usr/bin/tr -cd 'A-Za-z0-9._-' | /usr/bin/cut -c1-48)"
[ -n "$SERVER_NAME" ] || SERVER_NAME="Mac"
TARGET_UID="$(/usr/bin/id -u "$TARGET_USER")"
TARGET_HOME="$(/usr/bin/dscl . -read "/Users/$TARGET_USER" NFSHomeDirectory | /usr/bin/awk '{print $2}')"
[ -n "$TARGET_HOME" ] || { echo "Could not resolve home directory" >&2; exit 69; }

ADMIN_DIR="/Library/Application Support/ZorinMacBridge/Admin"
LABEL="com.ilovemyprojects.zorinmacbridge.server"
ADMIN_LABEL="com.ilovemyprojects.zorinmacbridge.admin"
UPDATE_LABEL="com.ilovemyprojects.zorinmacbridge.update"
PRELOGIN_LABEL="com.ilovemyprojects.zorinmacbridge.prelogin"
PRELOGIN_PLIST="/Library/LaunchAgents/${PRELOGIN_LABEL}.plist"

# launchd processes bootout asynchronously on current macOS releases.  A
# bootstrap issued immediately afterwards can fail with EIO (5) even when the
# plist is valid.  Keep all launchd registration in one bounded/retrying helper.
wait_for_launchd_job_gone() {
  local label="$1" attempt=1
  while [ "$attempt" -le 20 ]; do
    if ! /bin/launchctl print "system/${label}" >/dev/null 2>&1; then
      return 0
    fi
    /bin/sleep 0.5
    attempt=$((attempt + 1))
  done
  return 1
}

bootstrap_system_job() {
  local label="$1" source_plist="$2" dest_plist="$3"
  local attempt rc last_err="" err_file

  /usr/bin/plutil -lint "$source_plist" >/dev/null || {
    echo "Invalid bundled LaunchDaemon plist: $source_plist" >&2
    return 1
  }

  /bin/launchctl bootout "system/${label}" >/dev/null 2>&1 || true
  # bootout can return before launchd has fully released the service label.
  wait_for_launchd_job_gone "$label" || true

  /usr/bin/install -o root -g wheel -m 0644 "$source_plist" "$dest_plist"
  /usr/bin/plutil -lint "$dest_plist" >/dev/null || {
    echo "Installed LaunchDaemon plist failed validation: $dest_plist" >&2
    return 1
  }
  /bin/launchctl enable "system/${label}" >/dev/null 2>&1 || true

  err_file="$(/usr/bin/mktemp /var/tmp/zmb-launchctl.XXXXXX)"
  attempt=1
  while [ "$attempt" -le 16 ]; do
    : > "$err_file"
    if /bin/launchctl bootstrap system "$dest_plist" 2>"$err_file"; then
      /bin/rm -f "$err_file"
      return 0
    fi
    rc=$?
    last_err="$(/bin/cat "$err_file" 2>/dev/null || true)"
    # If a concurrent/bootstrap retry actually registered it, treat that as
    # success rather than issuing another registration request.
    if /bin/launchctl print "system/${label}" >/dev/null 2>&1; then
      /bin/rm -f "$err_file"
      return 0
    fi
    # EIO/"in progress" after bootout is transient; bounded retries also make
    # other launchd transition states recover without hanging the installer.
    /bin/sleep 0.5
    attempt=$((attempt + 1))
  done

  echo "ERROR: launchd could not bootstrap ${label}." >&2
  [ -n "$last_err" ] && printf '%s\n' "$last_err" >&2
  echo "LaunchDaemon diagnostics:" >&2
  /usr/bin/plutil -lint "$dest_plist" >&2 || true
  /bin/ls -ld /Library/LaunchDaemons "$dest_plist" /usr/local/libexec /usr/local/sbin >&2 || true
  /bin/launchctl print-disabled system >&2 || true
  echo "Recent launchd messages for ZorinMacBridge:" >&2
  /usr/bin/log show --style compact --last 2m --predicate 'process == "launchd" AND (eventMessage CONTAINS[c] "zorinmacbridge" OR eventMessage CONTAINS[c] "bootstrap")' 2>/dev/null | /usr/bin/tail -n 80 >&2 || true
  /bin/rm -f "$err_file"
  return "${rc:-1}"
}

/bin/mkdir -p /usr/local/sbin /usr/local/libexec "$ADMIN_DIR"
/usr/bin/install -o root -g wheel -m 0755 "$HERE/zmb-admin" /usr/local/sbin/zmb-admin
/usr/bin/install -o root -g wheel -m 0755 "$HERE/zmb-admin-ssh" /usr/local/libexec/zmb-admin-ssh
/usr/bin/install -o root -g wheel -m 0755 "$HERE/zmb-system-daemon" /usr/local/libexec/zmb-system-daemon

OLD_AUTO_UPDATE="notify"
if [ -f "$ADMIN_DIR/config" ]; then
  VALUE="$(/usr/bin/awk -F= '$1=="AUTO_UPDATE" {print $2; exit}' "$ADMIN_DIR/config" 2>/dev/null || true)"
  case "$VALUE" in off|notify|install) OLD_AUTO_UPDATE="$VALUE" ;; esac
fi
CONFIG_TMP="$(/usr/bin/mktemp)"
cat > "$CONFIG_TMP" <<CFG
TARGET_USER=$TARGET_USER
SERVER_ID=$SERVER_ID
SERVER_NAME=$SERVER_NAME
AUTOHEAL=1
AUTO_UPDATE=$OLD_AUTO_UPDATE
LOCAL_IDENTITY_TOKEN=$LOCAL_IDENTITY_TOKEN
CFG
/usr/bin/install -o root -g wheel -m 0600 "$CONFIG_TMP" "$ADMIN_DIR/config"
/bin/rm -f "$CONFIG_TMP"

SUDOERS_TMP="$(/usr/bin/mktemp)"
cat > "$SUDOERS_TMP" <<SUDOERS
# ZorinMacBridge P0 — restricted helper only
$TARGET_USER ALL=(root) NOPASSWD: /usr/local/sbin/zmb-admin status
$TARGET_USER ALL=(root) NOPASSWD: /usr/local/sbin/zmb-admin start-zmb
$TARGET_USER ALL=(root) NOPASSWD: /usr/local/sbin/zmb-admin stop-zmb
$TARGET_USER ALL=(root) NOPASSWD: /usr/local/sbin/zmb-admin restart-zmb
$TARGET_USER ALL=(root) NOPASSWD: /usr/local/sbin/zmb-admin recover-desktop
$TARGET_USER ALL=(root) NOPASSWD: /usr/local/sbin/zmb-admin repair-launch-agent
$TARGET_USER ALL=(root) NOPASSWD: /usr/local/sbin/zmb-admin pairing-list
$TARGET_USER ALL=(root) NOPASSWD: /usr/local/sbin/zmb-admin unpair *
$TARGET_USER ALL=(root) NOPASSWD: /usr/local/sbin/zmb-admin logs
$TARGET_USER ALL=(root) NOPASSWD: /usr/local/sbin/zmb-admin diagnose
$TARGET_USER ALL=(root) NOPASSWD: /usr/local/sbin/zmb-admin recover-all
$TARGET_USER ALL=(root) NOPASSWD: /usr/local/sbin/zmb-admin check-update
$TARGET_USER ALL=(root) NOPASSWD: /usr/local/sbin/zmb-admin update-now
$TARGET_USER ALL=(root) NOPASSWD: /usr/local/sbin/zmb-admin set-auto-update off
$TARGET_USER ALL=(root) NOPASSWD: /usr/local/sbin/zmb-admin set-auto-update notify
$TARGET_USER ALL=(root) NOPASSWD: /usr/local/sbin/zmb-admin set-auto-update install
$TARGET_USER ALL=(root) NOPASSWD: /usr/local/sbin/zmb-admin set-wake-network on
$TARGET_USER ALL=(root) NOPASSWD: /usr/local/sbin/zmb-admin set-wake-network off
$TARGET_USER ALL=(root) NOPASSWD: /usr/local/sbin/zmb-admin wake-display
$TARGET_USER ALL=(root) NOPASSWD: /usr/local/sbin/zmb-admin xcode-overview
$TARGET_USER ALL=(root) NOPASSWD: /usr/local/sbin/zmb-admin xcode-open
$TARGET_USER ALL=(root) NOPASSWD: /usr/local/sbin/zmb-admin sim-open
$TARGET_USER ALL=(root) NOPASSWD: /usr/local/sbin/zmb-admin sim-boot *
$TARGET_USER ALL=(root) NOPASSWD: /usr/local/sbin/zmb-admin sim-shutdown *
$TARGET_USER ALL=(root) NOPASSWD: /usr/local/sbin/zmb-admin sleep
$TARGET_USER ALL=(root) NOPASSWD: /usr/local/sbin/zmb-admin reboot
$TARGET_USER ALL=(root) NOPASSWD: /usr/local/sbin/zmb-admin shutdown
SUDOERS
/usr/sbin/visudo -cf "$SUDOERS_TMP" >/dev/null
/usr/bin/install -o root -g wheel -m 0440 "$SUDOERS_TMP" /etc/sudoers.d/zorinmacbridge-admin
/bin/rm -f "$SUDOERS_TMP"

# User-session component stays a LaunchAgent because ScreenCaptureKit/input need Aqua.
# The target path is user-writable, so never create/overwrite it with root file
# semantics. The installed helper performs the atomic write as TARGET_USER.
/usr/local/sbin/zmb-admin repair-launch-agent >/dev/null

# Pre-login Desktop is a root LoginWindow LaunchAgent.  /Library/LaunchAgents
# jobs with LimitLoadToSessionType=LoginWindow are loaded by launchd in the
# pre-login GUI session, before the target user has an Aqua session.
/usr/bin/plutil -lint "$HERE/com.ilovemyprojects.zorinmacbridge.prelogin.plist" >/dev/null
/usr/bin/install -o root -g wheel -m 0644 "$HERE/com.ilovemyprojects.zorinmacbridge.prelogin.plist" "$PRELOGIN_PLIST"
/usr/bin/plutil -lint "$PRELOGIN_PLIST" >/dev/null
# The job is session-scoped, not a system LaunchDaemon.  If loginwindow is
# currently present, best-effort kick it now; otherwise launchd will load it
# automatically on the next boot/logout when LoginWindow appears.
/bin/launchctl enable "gui/0/${PRELOGIN_LABEL}" >/dev/null 2>&1 || true
if /bin/launchctl print gui/0 >/dev/null 2>&1; then
  /bin/launchctl bootout "gui/0/${PRELOGIN_LABEL}" >/dev/null 2>&1 || true
  /bin/launchctl bootstrap gui/0 "$PRELOGIN_PLIST" >/dev/null 2>&1 || true
  /bin/launchctl kickstart -k "gui/0/${PRELOGIN_LABEL}" >/dev/null 2>&1 || true
fi

bootstrap_system_job   "$ADMIN_LABEL"   "$HERE/com.ilovemyprojects.zorinmacbridge.admin.plist"   "/Library/LaunchDaemons/${ADMIN_LABEL}.plist"
/bin/launchctl kickstart -k "system/${ADMIN_LABEL}" >/dev/null 2>&1 || true

# Do not unload the updater job from inside an update it is currently performing.
# The new plist is installed on disk immediately; normal setup/reboot reloads it.
if [ "${ZMB_UPDATE_IN_PROGRESS:-0}" = "1" ]; then
  /usr/bin/plutil -lint "$HERE/com.ilovemyprojects.zorinmacbridge.update.plist" >/dev/null
  /usr/bin/install -o root -g wheel -m 0644     "$HERE/com.ilovemyprojects.zorinmacbridge.update.plist"     "/Library/LaunchDaemons/${UPDATE_LABEL}.plist"
else
  bootstrap_system_job     "$UPDATE_LABEL"     "$HERE/com.ilovemyprojects.zorinmacbridge.update.plist"     "/Library/LaunchDaemons/${UPDATE_LABEL}.plist"
  /bin/launchctl kickstart -k "system/${UPDATE_LABEL}" >/dev/null 2>&1 || true
fi

# SSH is the only network-facing admin transport. No custom privileged listener is opened.
# On current macOS, `systemsetup -setremotelogin on` requires Full Disk Access
# for the invoking process. Administrator privileges alone are not enough.
# Therefore P0 installation must remain successful when macOS requires the user
# to flip Remote Login once in System Settings. Everything else is installed so
# that enabling the switch immediately makes remote recovery usable.
REMOTE_LOGIN_STATE="manual_required"
if /usr/bin/nc -G 1 -z 127.0.0.1 22 >/dev/null 2>&1; then
  REMOTE_LOGIN_STATE="on"
else
  SYSTEMSETUP_OUT="$(/usr/bin/mktemp /var/tmp/zmb-systemsetup.XXXXXX)"
  cleanup_systemsetup() { /bin/rm -f "$SYSTEMSETUP_OUT"; }
  trap cleanup_systemsetup EXIT
  if /usr/sbin/systemsetup -setremotelogin on >"$SYSTEMSETUP_OUT" 2>&1; then
    REMOTE_LOGIN_STATE="on"
  else
    echo "REQUIRED: macOS did not allow Remote Login (SSH) to be enabled automatically." >&2
    /bin/cat "$SYSTEMSETUP_OUT" >&2 || true
    echo "P0 helper installation will continue." >&2
    echo "REQUIRED ACTION: System Settings > General > Sharing > Remote Login -> On." >&2
  fi
fi

# Remote Login normally creates/maintains the OpenSSH host keys. If the switch
# is already on but keys are missing, repair that safe local prerequisite now
# so fingerprint pairing cannot fail later with an empty result.
if [ "$REMOTE_LOGIN_STATE" = "on" ]; then
  /usr/bin/ssh-keygen -A >/dev/null 2>&1 || true
fi

# authorized_keys is user-owned state. Create/validate it entirely as the
# target user so a user-controlled path can never gain root file semantics.
if ! /usr/bin/sudo -u "$TARGET_USER" /bin/sh -c '
  set -e
  home="$1"
  ssh="$home/.ssh"
  keys="$ssh/authorized_keys"
  [ ! -L "$ssh" ] || exit 65
  if [ -e "$ssh" ]; then [ -d "$ssh" ] || exit 65; else /bin/mkdir -m 700 "$ssh"; fi
  /bin/chmod 700 "$ssh"
  [ ! -L "$keys" ] || exit 65
  if [ -e "$keys" ]; then
    [ -f "$keys" ] || exit 65
    /bin/chmod 600 "$keys"
  else
    (umask 077; : > "$keys")
  fi
' zmb-ssh-init "$TARGET_HOME"; then
  echo "Could not safely prepare $TARGET_HOME/.ssh/authorized_keys" >&2
  exit 78
fi

printf 'TARGET_USER=%s\n' "$TARGET_USER"
printf 'TARGET_UID=%s\n' "$TARGET_UID"
printf 'P0_OK=1\n'
printf 'REMOTE_LOGIN=%s\n' "$REMOTE_LOGIN_STATE"
/usr/local/sbin/zmb-admin status || true

echo 'SSH_FINGERPRINTS_BEGIN'
SSH_HOST_KEYS=0
for pub in /etc/ssh/ssh_host_ed25519_key.pub /etc/ssh/ssh_host_ecdsa_key.pub /etc/ssh/ssh_host_rsa_key.pub; do
  if [ -f "$pub" ]; then
    SSH_HOST_KEYS=1
    /usr/bin/ssh-keygen -lf "$pub" -E sha256 2>/dev/null || true
  fi
done
echo 'SSH_FINGERPRINTS_END'
printf 'SSH_HOST_KEYS=%s\n' "$SSH_HOST_KEYS"
if [ "$REMOTE_LOGIN_STATE" != "on" ]; then
  echo 'REQUIRED: Remote Login is OFF; Zorin pairing/System/Terminal/Projects cannot work until it is enabled.' >&2
elif [ "$SSH_HOST_KEYS" != "1" ]; then
  echo 'REQUIRED: macOS SSH host public keys are missing; fingerprint pairing is not ready.' >&2
fi
