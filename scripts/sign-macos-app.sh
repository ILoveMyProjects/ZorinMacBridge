#!/usr/bin/env bash
set -euo pipefail

APP="${1:?usage: sign-macos-app.sh /path/to/App.app}"

if [[ -z "${MACOS_CERTIFICATE_P12_BASE64:-}" || -z "${MACOS_CERTIFICATE_PASSWORD:-}" || -z "${MACOS_SIGNING_IDENTITY:-}" ]]; then
  echo "Developer ID secrets are not configured; using ad-hoc signing."
  echo "WARNING: macOS may require Screen Recording/Accessibility approval again after each updated build."
  codesign --force --deep --sign - "$APP"
  codesign --verify --deep --strict --verbose=2 "$APP"
  exit 0
fi

KEYCHAIN_PASSWORD="zmb-${GITHUB_RUN_ID:-local}-${GITHUB_RUN_ATTEMPT:-1}"
KEYCHAIN_PATH="${RUNNER_TEMP:-/tmp}/zorinmacbridge-signing.keychain-db"
P12_PATH="${RUNNER_TEMP:-/tmp}/zorinmacbridge-developer-id.p12"

python3 - "$P12_PATH" <<'PY'
import base64, os, pathlib, sys
raw = os.environ['MACOS_CERTIFICATE_P12_BASE64'].strip()
pathlib.Path(sys.argv[1]).write_bytes(base64.b64decode(raw))
PY

security create-keychain -p "$KEYCHAIN_PASSWORD" "$KEYCHAIN_PATH"
security set-keychain-settings -lut 21600 "$KEYCHAIN_PATH"
security unlock-keychain -p "$KEYCHAIN_PASSWORD" "$KEYCHAIN_PATH"
security import "$P12_PATH" -k "$KEYCHAIN_PATH" -P "$MACOS_CERTIFICATE_PASSWORD" -T /usr/bin/codesign -T /usr/bin/security
security set-key-partition-list -S apple-tool:,apple:,codesign: -s -k "$KEYCHAIN_PASSWORD" "$KEYCHAIN_PATH" >/dev/null
security list-keychains -d user -s "$KEYCHAIN_PATH"
security find-identity -v -p codesigning "$KEYCHAIN_PATH"

codesign --force --deep --options runtime --timestamp --sign "$MACOS_SIGNING_IDENTITY" "$APP"
codesign --verify --deep --strict --verbose=2 "$APP"
echo "Developer ID signing complete."
codesign -d -r- "$APP" 2>&1 | sed -n '1,8p'
