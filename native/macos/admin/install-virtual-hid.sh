#!/bin/bash
set -euo pipefail
[ "$(uname -s)" = Darwin ] || { echo 'macOS only' >&2; exit 1; }
[ "$(/usr/bin/id -u)" -eq 0 ] || { echo 'Virtual HID setup must run as root.' >&2; exit 77; }
HERE="$(cd "$(dirname "$0")" && pwd)"
MODE="${1:-full}"
case "$MODE" in full|--prepare|--finalize) ;; *) echo "Unknown Virtual HID setup mode: $MODE" >&2; exit 64 ;; esac
VERSION='8.8.0'
PKG_SHA256='2b6e323bf690fde05b24ca2648536916a274d01496e3826dc8b050b63f8b9842'
PKG_NAME="Karabiner-DriverKit-VirtualHIDDevice-${VERSION}.pkg"
BUNDLED_PKG="$HERE/../virtualhid/$PKG_NAME"
MANAGER='/Applications/.Karabiner-VirtualHIDDevice-Manager.app/Contents/MacOS/Karabiner-VirtualHIDDevice-Manager'
DAEMON='/Library/Application Support/org.pqrs/Karabiner-DriverKit-VirtualHIDDevice/Applications/Karabiner-VirtualHIDDevice-Daemon.app/Contents/MacOS/Karabiner-VirtualHIDDevice-Daemon'
SOCKET='/Library/Application Support/org.pqrs/tmp/rootonly/karabiner_virtual_hid_device_service.sock'
EXTENSION_ID='org.pqrs.Karabiner-DriverKit-VirtualHIDDevice'
APP_BIN='/Applications/ZorinMacBridge Server.app/Contents/MacOS/ZorinMacBridge Server'
LABEL='com.ilovemyprojects.zorinmacbridge.virtualhid'
PLIST="/Library/LaunchDaemons/${LABEL}.plist"
SOURCE_PLIST="$HERE/${LABEL}.plist"
ACTIVATE_LOG='/var/tmp/zmb-vhid-activate.log'
SELFTEST_LOG='/var/tmp/zmb-vhid-selftest.log'

extension_line() {
  /usr/bin/systemextensionsctl list 2>/dev/null | /usr/bin/grep -F "$EXTENSION_ID" | /usr/bin/tail -n 1 || true
}

extension_ready() {
  local line
  line="$(extension_line)"
  [ -n "$line" ] && printf '%s' "$line" | /usr/bin/grep -Fqi '[activated enabled]'
}

selftest_ready() {
  [ -x "$APP_BIN" ] || return 1
  ZMB_VHID_SELFTEST_WAIT=10 "$APP_BIN" --self-test-virtual-hid >"$SELFTEST_LOG" 2>&1
}

install_package() {
  if [ ! -x "$MANAGER" ] || [ ! -x "$DAEMON" ]; then
    [ -f "$BUNDLED_PKG" ] || { echo "Bundled virtual HID package missing: $BUNDLED_PKG" >&2; return 66; }
    ACTUAL_SHA=$(/usr/bin/shasum -a 256 "$BUNDLED_PKG" | /usr/bin/awk '{print $1}')
    [ "$ACTUAL_SHA" = "$PKG_SHA256" ] || { echo "Virtual HID package SHA-256 mismatch: $ACTUAL_SHA" >&2; return 65; }
    /usr/sbin/pkgutil --check-signature "$BUNDLED_PKG" >/dev/null || { echo 'Virtual HID package signature verification failed.' >&2; return 65; }
    /usr/sbin/installer -pkg "$BUNDLED_PKG" -target / >/dev/null
  fi
  [ -x "$MANAGER" ] || { echo 'Virtual HID manager was not installed.' >&2; return 69; }
  [ -x "$DAEMON" ] || { echo 'Virtual HID daemon was not installed.' >&2; return 69; }
}

install_daemon_job() {
  /usr/bin/plutil -lint "$SOURCE_PLIST" >/dev/null
  /bin/launchctl bootout "system/${LABEL}" >/dev/null 2>&1 || true
  /usr/bin/install -o root -g wheel -m 0644 "$SOURCE_PLIST" "$PLIST"
  /bin/launchctl enable "system/${LABEL}" >/dev/null 2>&1 || true
  /bin/launchctl bootstrap system "$PLIST" >/dev/null 2>&1 || true
  /bin/launchctl kickstart -k "system/${LABEL}" >/dev/null 2>&1 || true
}

# Legacy/manual full mode is bounded so DriverKit approval can never hold the
# entire P0 installer open indefinitely. Setup Assistant uses --prepare and
# submits activation separately from the logged-in Aqua session.
run_manager_bounded() {
  local action="$1" pid attempt
  "$MANAGER" "$action" >>"$ACTIVATE_LOG" 2>&1 &
  pid=$!
  attempt=0
  while /bin/kill -0 "$pid" >/dev/null 2>&1; do
    if [ "$attempt" -ge 20 ]; then
      /bin/kill "$pid" >/dev/null 2>&1 || true
      /bin/sleep 0.2
      /bin/kill -9 "$pid" >/dev/null 2>&1 || true
      wait "$pid" >/dev/null 2>&1 || true
      return 124
    fi
    /bin/sleep 0.25
    attempt=$((attempt + 1))
  done
  wait "$pid" >/dev/null 2>&1 || true
  return 0
}

install_package

if [ "$MODE" = "--prepare" ]; then
  if extension_ready; then
    echo 'virtual_hid=prepared-extension-ready'
    echo "virtual_hid_extension_state=$(extension_line)"
    echo "virtual_hid_version=$VERSION"
    exit 0
  fi
  echo 'virtual_hid=prepared-approval-required'
  echo "virtual_hid_extension=${EXTENSION_ID}"
  echo "virtual_hid_extension_state=$(extension_line)"
  echo "virtual_hid_version=$VERSION"
  exit 0
fi

if [ "$MODE" = "full" ]; then
  : >"$ACTIVATE_LOG"
  if ! extension_ready; then run_manager_bounded activate || true; fi
  if ! extension_ready; then run_manager_bounded forceActivate || true; fi
fi

if ! extension_ready; then
  echo 'virtual_hid=approval-required'
  echo "virtual_hid_extension=${EXTENSION_ID}"
  echo "virtual_hid_extension_state=$(extension_line)"
  echo "virtual_hid_version=$VERSION"
  echo 'REQUIRED: approve the ZorinMacBridge virtual HID Driver Extension in System Settings. Setup Assistant will keep checking automatically.' >&2
  /usr/bin/tail -n 20 "$ACTIVATE_LOG" >&2 2>/dev/null || true
  exit 75
fi

install_daemon_job

/bin/launchctl kickstart -k "system/${LABEL}" >/dev/null 2>&1 || true
socket_ready=0
for _ in $(/usr/bin/seq 1 40); do
  if [ -S "$SOCKET" ]; then socket_ready=1; break; fi
  /bin/sleep 0.25
done
if [ "$socket_ready" != 1 ]; then
  echo 'virtual_hid=daemon-socket-missing'
  echo "virtual_hid_extension_state=$(extension_line)"
  echo "virtual_hid_version=$VERSION"
  exit 76
fi

for phase in 1 2; do
  if selftest_ready; then
    echo 'virtual_hid=ready'
    echo "virtual_hid_extension_state=$(extension_line)"
    echo "virtual_hid_version=$VERSION"
    exit 0
  fi
  if [ "$phase" = 1 ]; then
    /bin/launchctl kickstart -k "system/${LABEL}" >/dev/null 2>&1 || true
    /bin/sleep 1
  fi
done

echo 'virtual_hid=devices-not-ready'
echo "virtual_hid_extension_state=$(extension_line)"
echo "virtual_hid_version=$VERSION"
/usr/bin/tail -n 20 "$SELFTEST_LOG" >&2 2>/dev/null || true
/usr/bin/tail -n 40 /var/log/zorinmacbridge-virtualhid.err >&2 2>/dev/null || true
/usr/bin/tail -n 40 /var/log/karabiner/virtual_hid_device_service.log >&2 2>/dev/null || true
exit 76
