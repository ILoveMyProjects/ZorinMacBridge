#!/bin/bash
set -euo pipefail
[ "$(uname -s)" = Darwin ] || { echo 'macOS only' >&2; exit 1; }
[ "$(/usr/bin/id -u)" -eq 0 ] || { echo 'Virtual HID setup must run as root.' >&2; exit 77; }
HERE="$(cd "$(dirname "$0")" && pwd)"
VERSION='8.8.0'
PKG_SHA256='2b6e323bf690fde05b24ca2648536916a274d01496e3826dc8b050b63f8b9842'
PKG_NAME="Karabiner-DriverKit-VirtualHIDDevice-${VERSION}.pkg"
BUNDLED_PKG="$HERE/../virtualhid/$PKG_NAME"
MANAGER='/Applications/.Karabiner-VirtualHIDDevice-Manager.app/Contents/MacOS/Karabiner-VirtualHIDDevice-Manager'
DAEMON='/Library/Application Support/org.pqrs/Karabiner-DriverKit-VirtualHIDDevice/Applications/Karabiner-VirtualHIDDevice-Daemon.app/Contents/MacOS/Karabiner-VirtualHIDDevice-Daemon'
SOCKET='/Library/Application Support/org.pqrs/tmp/rootonly/karabiner_virtual_hid_device_service.sock'
LABEL='com.ilovemyprojects.zorinmacbridge.virtualhid'
PLIST="/Library/LaunchDaemons/${LABEL}.plist"
SOURCE_PLIST="$HERE/${LABEL}.plist"

if [ ! -x "$MANAGER" ] || [ ! -x "$DAEMON" ]; then
  [ -f "$BUNDLED_PKG" ] || { echo "Bundled virtual HID package missing: $BUNDLED_PKG" >&2; exit 66; }
  ACTUAL_SHA=$(/usr/bin/shasum -a 256 "$BUNDLED_PKG" | /usr/bin/awk '{print $1}')
  [ "$ACTUAL_SHA" = "$PKG_SHA256" ] || { echo "Virtual HID package SHA-256 mismatch: $ACTUAL_SHA" >&2; exit 65; }
  /usr/sbin/pkgutil --check-signature "$BUNDLED_PKG" >/dev/null || { echo 'Virtual HID package signature verification failed.' >&2; exit 65; }
  /usr/sbin/installer -pkg "$BUNDLED_PKG" -target / >/dev/null
fi
[ -x "$MANAGER" ] || { echo 'Virtual HID manager was not installed.' >&2; exit 69; }
[ -x "$DAEMON" ] || { echo 'Virtual HID daemon was not installed.' >&2; exit 69; }

# Submit the already-signed DriverKit system extension. macOS may keep the
# activation request pending until its one-time Driver Extensions approval.
if ! "$MANAGER" activate >/var/tmp/zmb-vhid-activate.log 2>&1; then
  "$MANAGER" forceActivate >>/var/tmp/zmb-vhid-activate.log 2>&1 || true
fi

/usr/bin/plutil -lint "$SOURCE_PLIST" >/dev/null
/bin/launchctl bootout "system/${LABEL}" >/dev/null 2>&1 || true
/usr/bin/install -o root -g wheel -m 0644 "$SOURCE_PLIST" "$PLIST"
/bin/launchctl enable "system/${LABEL}" >/dev/null 2>&1 || true
/bin/launchctl bootstrap system "$PLIST" >/dev/null 2>&1 || true
/bin/launchctl kickstart -k "system/${LABEL}" >/dev/null 2>&1 || true

for _ in $(/usr/bin/seq 1 40); do
  if [ -S "$SOCKET" ]; then
    echo 'virtual_hid=ready'
    echo "virtual_hid_version=$VERSION"
    exit 0
  fi
  /bin/sleep 0.25
done
echo 'virtual_hid=pending-activation'
echo "virtual_hid_version=$VERSION"
exit 75
