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
$TARGET_USER ALL=(root) NOPASSWD: /usr/local/sbin/zmb-admin repair-launch-agent
$TARGET_USER ALL=(root) NOPASSWD: /usr/local/sbin/zmb-admin pairing-list
$TARGET_USER ALL=(root) NOPASSWD: /usr/local/sbin/zmb-admin unpair *
$TARGET_USER ALL=(root) NOPASSWD: /usr/local/sbin/zmb-admin logs
$TARGET_USER ALL=(root) NOPASSWD: /usr/local/sbin/zmb-admin check-update
$TARGET_USER ALL=(root) NOPASSWD: /usr/local/sbin/zmb-admin update-now
$TARGET_USER ALL=(root) NOPASSWD: /usr/local/sbin/zmb-admin set-auto-update off
$TARGET_USER ALL=(root) NOPASSWD: /usr/local/sbin/zmb-admin set-auto-update notify
$TARGET_USER ALL=(root) NOPASSWD: /usr/local/sbin/zmb-admin set-auto-update install
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

/bin/launchctl bootout "system/${ADMIN_LABEL}" >/dev/null 2>&1 || true
/usr/bin/install -o root -g wheel -m 0644 "$HERE/com.ilovemyprojects.zorinmacbridge.admin.plist" "/Library/LaunchDaemons/${ADMIN_LABEL}.plist"
/bin/launchctl bootstrap system "/Library/LaunchDaemons/${ADMIN_LABEL}.plist"
/bin/launchctl kickstart -k "system/${ADMIN_LABEL}" >/dev/null 2>&1 || true

/usr/bin/install -o root -g wheel -m 0644 "$HERE/com.ilovemyprojects.zorinmacbridge.update.plist" "/Library/LaunchDaemons/${UPDATE_LABEL}.plist"
# Do not unload the updater job from inside an update it is currently performing.
# The new plist is on disk immediately; normal setup/reboot reloads it.
if [ "${ZMB_UPDATE_IN_PROGRESS:-0}" != "1" ]; then
  /bin/launchctl bootout "system/${UPDATE_LABEL}" >/dev/null 2>&1 || true
  /bin/launchctl bootstrap system "/Library/LaunchDaemons/${UPDATE_LABEL}.plist"
  /bin/launchctl kickstart -k "system/${UPDATE_LABEL}" >/dev/null 2>&1 || true
fi

# SSH is the only network-facing admin transport. No custom privileged listener is opened.
# P0 is not considered installed until Remote Login is actually enabled.
SYSTEMSETUP_OUT="$(/usr/bin/mktemp /var/tmp/zmb-systemsetup.XXXXXX)"
cleanup_systemsetup() { /bin/rm -f "$SYSTEMSETUP_OUT"; }
trap cleanup_systemsetup EXIT
if ! /usr/sbin/systemsetup -setremotelogin on >"$SYSTEMSETUP_OUT" 2>&1; then
  echo "ERROR: Could not enable macOS Remote Login (SSH) automatically." >&2
  /bin/cat "$SYSTEMSETUP_OUT" >&2 || true
  echo "Open System Settings > General > Sharing > Remote Login, turn it on, then run setup again." >&2
  exit 78
fi
echo "REMOTE_LOGIN=on"

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
/usr/local/sbin/zmb-admin status || true

echo 'SSH_FINGERPRINTS_BEGIN'
for pub in /etc/ssh/ssh_host_ed25519_key.pub /etc/ssh/ssh_host_ecdsa_key.pub /etc/ssh/ssh_host_rsa_key.pub; do
  if [ -f "$pub" ]; then /usr/bin/ssh-keygen -lf "$pub" -E sha256 2>/dev/null || true; fi
done
echo 'SSH_FINGERPRINTS_END'
