#!/bin/bash
set -euo pipefail
if [ "$(uname -s)" != "Darwin" ]; then echo "macOS only" >&2; exit 1; fi
if [ "$(/usr/bin/id -u)" -ne 0 ]; then echo "Administrator privileges required" >&2; exit 77; fi
TARGET_USER="${1:-}"
case "$TARGET_USER" in ""|root|loginwindow|_mbsetupuser) echo "Invalid target user" >&2; exit 64 ;; esac
if ! printf '%s' "$TARGET_USER" | /usr/bin/grep -Eq '^[A-Za-z0-9._-]+$'; then
  echo "Unsupported macOS short username: $TARGET_USER" >&2
  exit 64
fi
TARGET_HOME="$(/usr/bin/dscl . -read "/Users/$TARGET_USER" NFSHomeDirectory 2>/dev/null | /usr/bin/awk '{print $2}')"

/bin/launchctl bootout "system/com.ilovemyprojects.zorinmacbridge.admin" >/dev/null 2>&1 || true
/bin/launchctl bootout "system/com.ilovemyprojects.zorinmacbridge.update" >/dev/null 2>&1 || true
/bin/launchctl bootout "system/com.ilovemyprojects.zorinmacbridge.virtualhid" >/dev/null 2>&1 || true
/bin/launchctl bootout loginwindow/com.ilovemyprojects.zorinmacbridge.prelogin >/dev/null 2>&1 || true
/bin/launchctl unload -w -S LoginWindow "/Library/LaunchAgents/com.ilovemyprojects.zorinmacbridge.prelogin.plist" >/dev/null 2>&1 || true
/bin/rm -f "/Library/LaunchDaemons/com.ilovemyprojects.zorinmacbridge.admin.plist" "/Library/LaunchDaemons/com.ilovemyprojects.zorinmacbridge.update.plist" "/Library/LaunchDaemons/com.ilovemyprojects.zorinmacbridge.virtualhid.plist" "/Library/LaunchAgents/com.ilovemyprojects.zorinmacbridge.prelogin.plist"
/bin/rm -f /etc/sudoers.d/zorinmacbridge-admin
/bin/rm -f /usr/local/sbin/zmb-admin /usr/local/libexec/zmb-admin-ssh /usr/local/libexec/zmb-system-daemon
/bin/rm -f /var/log/zorinmacbridge-update.log /var/log/zorinmacbridge-update.err /var/log/zorinmacbridge-prelogin.log /var/log/zorinmacbridge-prelogin.err /var/log/zorinmacbridge-virtualhid.log /var/log/zorinmacbridge-virtualhid.err
/bin/rm -rf "/Library/Application Support/ZorinMacBridge/Admin" /var/run/zorinmacbridge-admin

# Remove only ZMB administration keys; preserve all unrelated SSH keys and settings.
# Perform the complete read/filter/replace as TARGET_USER. Root must not read or
# overwrite a path controlled by that user.
if [ -n "$TARGET_HOME" ]; then
  if ! /usr/bin/sudo -u "$TARGET_USER" /bin/sh -c '
    set -e
    home="$1"
    ssh="$home/.ssh"
    keys="$ssh/authorized_keys"
    [ ! -L "$ssh" ] || exit 65
    [ -e "$keys" ] || exit 0
    [ -f "$keys" ] && [ ! -L "$keys" ] || exit 65
    tmp=$(/usr/bin/mktemp "$ssh/.zmb-uninstall-keys.XXXXXX")
    trap '"'"'/bin/rm -f "$tmp"'"'"' EXIT HUP INT TERM
    /usr/bin/grep -v '"'"'ZorinMacBridge-Admin:'"'"' "$keys" > "$tmp" || true
    /bin/chmod 600 "$tmp"
    /bin/mv -f "$tmp" "$keys"
    trap - EXIT HUP INT TERM
  ' zmb-uninstall "$TARGET_HOME"; then
    echo "Could not safely remove ZorinMacBridge admin keys" >&2
    exit 78
  fi
fi

echo 'P0_REMOVED=1'
echo 'Remote Login was intentionally left unchanged because it may be used for normal SSH access.'
