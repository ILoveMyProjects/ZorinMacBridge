#!/bin/bash
set -euo pipefail

if [ "$(uname -s)" != "Darwin" ]; then
  echo "macOS only" >&2
  exit 1
fi
if [ "$(/usr/bin/id -u)" -ne 0 ]; then
  echo "Administrator privileges required" >&2
  exit 77
fi

TARGET_USER="${1:-}"
MODE="${2:-full-clean}"
case "$TARGET_USER" in
  ""|root|loginwindow|_mbsetupuser) echo "Invalid target user" >&2; exit 64 ;;
esac
if ! printf '%s' "$TARGET_USER" | /usr/bin/grep -Eq '^[A-Za-z0-9._-]+$'; then
  echo "Unsupported macOS short username: $TARGET_USER" >&2
  exit 64
fi
case "$MODE" in
  full-clean|keep-config) ;;
  *) echo "Invalid uninstall mode: $MODE" >&2; exit 64 ;;
esac

TARGET_HOME="$(/usr/bin/dscl . -read "/Users/$TARGET_USER" NFSHomeDirectory 2>/dev/null | /usr/bin/awk '{print $2}')"
TARGET_UID="$(/usr/bin/id -u "$TARGET_USER")"
APP="/Applications/ZorinMacBridge Server.app"
USER_AGENT="$TARGET_HOME/Library/LaunchAgents/com.ilovemyprojects.zorinmacbridge.server.plist"

# Stop only ZorinMacBridge-owned launch jobs. Remote Login itself is intentionally
# left unchanged because the user may rely on it for ordinary SSH access.
/bin/launchctl bootout "gui/$TARGET_UID/com.ilovemyprojects.zorinmacbridge.server" >/dev/null 2>&1 || true
/bin/launchctl bootout "system/com.ilovemyprojects.zorinmacbridge.admin" >/dev/null 2>&1 || true
/bin/launchctl bootout "system/com.ilovemyprojects.zorinmacbridge.update" >/dev/null 2>&1 || true
/bin/launchctl bootout "system/com.ilovemyprojects.zorinmacbridge.virtualhid" >/dev/null 2>&1 || true
/bin/launchctl bootout loginwindow/com.ilovemyprojects.zorinmacbridge.prelogin >/dev/null 2>&1 || true
/bin/launchctl unload -w -S LoginWindow "/Library/LaunchAgents/com.ilovemyprojects.zorinmacbridge.prelogin.plist" >/dev/null 2>&1 || true

# Stop the application if it is still alive. Do not use a broad kill pattern.
/usr/bin/pkill -x "ZorinMacBridge Server" >/dev/null 2>&1 || true

/bin/rm -f \
  "/Library/LaunchDaemons/com.ilovemyprojects.zorinmacbridge.admin.plist" \
  "/Library/LaunchDaemons/com.ilovemyprojects.zorinmacbridge.update.plist" \
  "/Library/LaunchDaemons/com.ilovemyprojects.zorinmacbridge.virtualhid.plist" \
  "/Library/LaunchAgents/com.ilovemyprojects.zorinmacbridge.prelogin.plist" \
  /etc/sudoers.d/zorinmacbridge-admin \
  /usr/local/sbin/zmb-admin \
  /usr/local/libexec/zmb-admin-ssh \
  /usr/local/libexec/zmb-system-daemon \
  /var/log/zorinmacbridge-update.log \
  /var/log/zorinmacbridge-update.err \
  /var/log/zorinmacbridge-prelogin.log \
  /var/log/zorinmacbridge-prelogin.err \
  /var/log/zorinmacbridge-virtualhid.log \
  /var/log/zorinmacbridge-virtualhid.err
/bin/rm -rf "/Library/Application Support/ZorinMacBridge/Admin" /var/run/zorinmacbridge-admin

# Remove only ZMB administration keys; preserve unrelated authorized_keys lines.
if [ -n "$TARGET_HOME" ]; then
  if ! /usr/bin/sudo -u "$TARGET_USER" /bin/sh -c '
    set -e
    home="$1"
    ssh="$home/.ssh"
    keys="$ssh/authorized_keys"
    [ ! -L "$ssh" ] || exit 65
    [ -e "$keys" ] || exit 0
    [ -f "$keys" ] && [ ! -L "$keys" ] || exit 65
    tmp=$(/usr/bin/mktemp "$ssh/.zmb-clean-uninstall-keys.XXXXXX")
    trap '\''/bin/rm -f "$tmp"'\'' EXIT HUP INT TERM
    /usr/bin/grep -v '\''ZorinMacBridge-Admin:'\'' "$keys" > "$tmp" || true
    /bin/chmod 600 "$tmp"
    /bin/mv -f "$tmp" "$keys"
    trap - EXIT HUP INT TERM
  ' zmb-clean-uninstall "$TARGET_HOME"; then
    echo "Could not safely remove ZorinMacBridge admin keys" >&2
    exit 78
  fi
fi

# The per-user LaunchAgent belongs to ZMB. Remove it as that user so a hostile
# symlink cannot redirect the privileged cleanup.
if [ -n "$TARGET_HOME" ]; then
  /usr/bin/sudo -u "$TARGET_USER" /bin/sh -c '
    set -e
    path="$1"
    [ ! -L "$path" ] || exit 65
    /bin/rm -f "$path"
  ' zmb-clean-uninstall "$USER_AGENT"
fi

# Full-clean removes only ZMB-owned user state. The shared pqrs virtual HID
# package is intentionally retained because another application may use it.
if [ "$MODE" = "full-clean" ] && [ -n "$TARGET_HOME" ]; then
  /usr/bin/sudo -u "$TARGET_USER" /bin/sh -c '
    set -e
    home="$1"
    for path in \
      "$home/.zorin-mac-bridge" \
      "$home/Library/Application Support/ZorinMacBridge"; do
      [ ! -L "$path" ] || exit 65
      /bin/rm -rf "$path"
    done
  ' zmb-clean-uninstall "$TARGET_HOME"
fi

/bin/rm -rf "$APP"

echo "ZMB_REMOVED=1"
echo "MODE=$MODE"
echo "Remote Login was left unchanged."
echo "The shared Karabiner DriverKit VirtualHIDDevice package was left unchanged."
