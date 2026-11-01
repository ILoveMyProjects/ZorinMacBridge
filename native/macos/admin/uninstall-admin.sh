#!/bin/bash
set -euo pipefail
if [ "$(uname -s)" != "Darwin" ]; then echo "macOS only" >&2; exit 1; fi
if [ "$(/usr/bin/id -u)" -ne 0 ]; then echo "Administrator privileges required" >&2; exit 77; fi
TARGET_USER="${1:-}"
case "$TARGET_USER" in ""|root|loginwindow|_mbsetupuser) echo "Invalid target user" >&2; exit 64 ;; esac
TARGET_HOME="$(/usr/bin/dscl . -read "/Users/$TARGET_USER" NFSHomeDirectory 2>/dev/null | /usr/bin/awk '{print $2}')"

/bin/launchctl bootout "system/com.ilovemyprojects.zorinmacbridge.admin" >/dev/null 2>&1 || true
/bin/rm -f "/Library/LaunchDaemons/com.ilovemyprojects.zorinmacbridge.admin.plist"
/bin/rm -f /etc/sudoers.d/zorinmacbridge-admin
/bin/rm -f /usr/local/sbin/zmb-admin /usr/local/libexec/zmb-admin-ssh /usr/local/libexec/zmb-system-daemon
/bin/rm -rf "/Library/Application Support/ZorinMacBridge/Admin" /var/run/zorinmacbridge-admin

# Remove only ZMB administration keys; preserve all unrelated SSH keys and settings.
if [ -n "$TARGET_HOME" ] && [ -f "$TARGET_HOME/.ssh/authorized_keys" ]; then
  TMP="$(/usr/bin/mktemp)"
  /usr/bin/grep -v 'ZorinMacBridge-Admin:' "$TARGET_HOME/.ssh/authorized_keys" > "$TMP" || true
  /usr/bin/install -m 0600 "$TMP" "$TARGET_HOME/.ssh/authorized_keys"
  /usr/sbin/chown "$TARGET_USER":staff "$TARGET_HOME/.ssh/authorized_keys"
  /bin/rm -f "$TMP"
fi

echo 'P0_REMOVED=1'
echo 'Remote Login was intentionally left unchanged because it may be used for normal SSH access.'
