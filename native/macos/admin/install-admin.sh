#!/bin/bash
set -euo pipefail

if [ "$(uname -s)" != "Darwin" ]; then
  echo "macOS only" >&2
  exit 1
fi
if [ "$(/usr/bin/id -u)" -ne 0 ]; then
  echo "This installer must run with administrator privileges." >&2
  exit 77
fi

HERE="$(cd "$(dirname "$0")" && pwd)"
TARGET_USER="${1:-}"
case "$TARGET_USER" in
  ""|root|loginwindow|_mbsetupuser) echo "Invalid target user" >&2; exit 64 ;;
esac
if ! printf '%s' "$TARGET_USER" | /usr/bin/grep -Eq '^[A-Za-z0-9._-]+$'; then
  echo "Unsupported macOS short username: $TARGET_USER" >&2
  exit 64
fi
TARGET_UID="$(/usr/bin/id -u "$TARGET_USER")"
TARGET_HOME="$(/usr/bin/dscl . -read "/Users/$TARGET_USER" NFSHomeDirectory | /usr/bin/awk '{print $2}')"
[ -n "$TARGET_HOME" ] || { echo "Could not resolve home directory" >&2; exit 69; }

ADMIN_DIR="/Library/Application Support/ZorinMacBridge/Admin"
LABEL="com.ilovemyprojects.zorinmacbridge.server"
ADMIN_LABEL="com.ilovemyprojects.zorinmacbridge.admin"

/bin/mkdir -p /usr/local/sbin /usr/local/libexec "$ADMIN_DIR"
/usr/bin/install -o root -g wheel -m 0755 "$HERE/zmb-admin" /usr/local/sbin/zmb-admin
/usr/bin/install -o root -g wheel -m 0755 "$HERE/zmb-admin-ssh" /usr/local/libexec/zmb-admin-ssh
/usr/bin/install -o root -g wheel -m 0755 "$HERE/zmb-system-daemon" /usr/local/libexec/zmb-system-daemon

CONFIG_TMP="$(/usr/bin/mktemp)"
cat > "$CONFIG_TMP" <<CFG
TARGET_USER=$TARGET_USER
AUTOHEAL=1
CFG
/usr/bin/install -o root -g wheel -m 0644 "$CONFIG_TMP" "$ADMIN_DIR/config"
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
$TARGET_USER ALL=(root) NOPASSWD: /usr/local/sbin/zmb-admin sleep
$TARGET_USER ALL=(root) NOPASSWD: /usr/local/sbin/zmb-admin reboot
$TARGET_USER ALL=(root) NOPASSWD: /usr/local/sbin/zmb-admin shutdown
SUDOERS
/usr/sbin/visudo -cf "$SUDOERS_TMP" >/dev/null
/usr/bin/install -o root -g wheel -m 0440 "$SUDOERS_TMP" /etc/sudoers.d/zorinmacbridge-admin
/bin/rm -f "$SUDOERS_TMP"

# User-session component stays a LaunchAgent because ScreenCaptureKit/input need Aqua.
/bin/mkdir -p "$TARGET_HOME/Library/LaunchAgents"
PLIST_TMP="$(/usr/bin/mktemp)"
cat > "$PLIST_TMP" <<'PLIST'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
<key>Label</key><string>com.ilovemyprojects.zorinmacbridge.server</string>
<key>ProgramArguments</key><array><string>/usr/bin/open</string><string>-a</string><string>ZorinMacBridge Server</string></array>
<key>RunAtLoad</key><true/>
</dict></plist>
PLIST
/usr/bin/install -m 0600 "$PLIST_TMP" "$TARGET_HOME/Library/LaunchAgents/${LABEL}.plist"
/bin/rm -f "$PLIST_TMP"
/usr/sbin/chown "$TARGET_USER":staff "$TARGET_HOME/Library/LaunchAgents/${LABEL}.plist"

/bin/launchctl bootout "gui/${TARGET_UID}/${LABEL}" >/dev/null 2>&1 || true
/bin/launchctl bootstrap "gui/${TARGET_UID}" "$TARGET_HOME/Library/LaunchAgents/${LABEL}.plist" >/dev/null 2>&1 || true

/bin/launchctl bootout "system/${ADMIN_LABEL}" >/dev/null 2>&1 || true
/usr/bin/install -o root -g wheel -m 0644 "$HERE/com.ilovemyprojects.zorinmacbridge.admin.plist" "/Library/LaunchDaemons/${ADMIN_LABEL}.plist"
/bin/launchctl bootstrap system "/Library/LaunchDaemons/${ADMIN_LABEL}.plist"
/bin/launchctl kickstart -k "system/${ADMIN_LABEL}" >/dev/null 2>&1 || true

# SSH is the only network-facing admin transport. No custom privileged listener is opened.
if /usr/sbin/systemsetup -setremotelogin on >/tmp/zmb-systemsetup.out 2>&1; then
  echo "REMOTE_LOGIN=on"
else
  echo "REMOTE_LOGIN=manual"
  /bin/cat /tmp/zmb-systemsetup.out >&2 || true
fi
/bin/rm -f /tmp/zmb-systemsetup.out

/bin/mkdir -p "$TARGET_HOME/.ssh"
/bin/chmod 700 "$TARGET_HOME/.ssh"
/usr/bin/touch "$TARGET_HOME/.ssh/authorized_keys"
/bin/chmod 600 "$TARGET_HOME/.ssh/authorized_keys"
/usr/sbin/chown -R "$TARGET_USER":staff "$TARGET_HOME/.ssh"

printf 'TARGET_USER=%s\n' "$TARGET_USER"
printf 'TARGET_UID=%s\n' "$TARGET_UID"
printf 'P0_OK=1\n'
/usr/local/sbin/zmb-admin status || true

echo 'SSH_FINGERPRINTS_BEGIN'
for pub in /etc/ssh/ssh_host_ed25519_key.pub /etc/ssh/ssh_host_ecdsa_key.pub /etc/ssh/ssh_host_rsa_key.pub; do
  if [ -f "$pub" ]; then /usr/bin/ssh-keygen -lf "$pub" -E sha256 2>/dev/null || true; fi
done
echo 'SSH_FINGERPRINTS_END'
