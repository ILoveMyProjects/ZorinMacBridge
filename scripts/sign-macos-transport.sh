#!/usr/bin/env bash
set -euo pipefail

APP="${1:?usage: sign-macos-transport.sh /path/to/App.app}"
TMP="${RUNNER_TEMP:-/tmp}/zmb-transport-signing-${RANDOM}-${RANDOM}"
KEYCHAIN="$TMP/transport.keychain-db"
KEYCHAIN_PASSWORD="$(openssl rand -hex 24)"
P12_PASSWORD="$(openssl rand -hex 24)"
IDENTITY="ZorinMacBridge Release Transport"
mkdir -p "$TMP"

ORIGINAL_KEYCHAINS=()
while IFS= read -r keychain_line; do
  keychain_line="$(printf '%s' "$keychain_line" | sed -E 's/^[[:space:]]*"(.*)"[[:space:]]*$/\1/')"
  if [ -n "$keychain_line" ]; then
    ORIGINAL_KEYCHAINS+=("$keychain_line")
  fi
done < <(security list-keychains -d user 2>/dev/null)
ORIGINAL_DEFAULT="$(security default-keychain -d user 2>/dev/null | sed -E 's/^[[:space:]]*"(.*)"[[:space:]]*$/\1/' || true)"

restore_keychains() {
  if [ "${#ORIGINAL_KEYCHAINS[@]}" -gt 0 ]; then
    security list-keychains -d user -s "${ORIGINAL_KEYCHAINS[@]}" >/dev/null 2>&1 || true
  fi
  if [ -n "$ORIGINAL_DEFAULT" ]; then
    security default-keychain -d user -s "$ORIGINAL_DEFAULT" >/dev/null 2>&1 || true
  fi
}

cleanup() {
  restore_keychains
  sudo security remove-trusted-cert -d "$TMP/cert.pem" >/dev/null 2>&1 || true
  security lock-keychain "$KEYCHAIN" >/dev/null 2>&1 || true
  security delete-keychain "$KEYCHAIN" >/dev/null 2>&1 || true
  rm -rf "$TMP"
}
trap cleanup EXIT

# This identity is deliberately short-lived. It is only a transport signature
# so pre-v0.6 updaters accept the migration release. Once installed, v0.6+
# re-signs the staged app with the persistent per-Mac local identity.
echo '[transport-sign] generating ephemeral code-signing identity'
openssl req -x509 -newkey rsa:3072 -sha256 -nodes -days 30 \
  -keyout "$TMP/key.pem" -out "$TMP/cert.pem" \
  -subj "/CN=$IDENTITY/O=ILoveMyProjects/OU=ZorinMacBridge/" \
  -addext 'basicConstraints=critical,CA:TRUE' \
  -addext 'keyUsage=critical,digitalSignature,keyCertSign' \
  -addext 'extendedKeyUsage=critical,codeSigning' >/dev/null 2>&1
openssl pkcs12 -export -out "$TMP/identity.p12" -inkey "$TMP/key.pem" -in "$TMP/cert.pem" \
  -name "$IDENTITY" -passout "pass:$P12_PASSWORD"

echo '[transport-sign] creating isolated keychain'
security create-keychain -p "$KEYCHAIN_PASSWORD" "$KEYCHAIN"
security set-keychain-settings -lut 21600 "$KEYCHAIN"
security unlock-keychain -p "$KEYCHAIN_PASSWORD" "$KEYCHAIN"

echo '[transport-sign] importing identity'
security import "$TMP/identity.p12" \
  -k "$KEYCHAIN" -P "$P12_PASSWORD" -A \
  -T /usr/bin/codesign -T /usr/bin/security \
  -t cert -f pkcs12 >/dev/null

# A custom keychain can contain a valid identity yet codesign may still return
# errSecItemNotFound if that keychain is not in the user search list. Put the
# temporary keychain first and make it the temporary default for this CI step.
echo '[transport-sign] exposing isolated keychain to codesign'
security list-keychains -d user -s "$KEYCHAIN" "${ORIGINAL_KEYCHAINS[@]}"
security default-keychain -d user -s "$KEYCHAIN"
security unlock-keychain -p "$KEYCHAIN_PASSWORD" "$KEYCHAIN"

# Best effort only. macOS 15/26 runners have versions where this command can
# spuriously return errSecItemNotFound. Import -A plus the search/default setup
# above is sufficient for this ephemeral CI keychain.
security set-key-partition-list \
  -S apple-tool:,apple:,codesign: -s -k "$KEYCHAIN_PASSWORD" "$KEYCHAIN" \
  >/dev/null 2>&1 || echo '[transport-sign] note: set-key-partition-list unavailable; continuing with import ACL'

# A self-signed identity must be trusted before it is considered a valid code
# signing identity. The trust entry exists only for the lifetime of this job.
echo '[transport-sign] trusting ephemeral identity for code signing'
sudo security add-trusted-cert -d -r trustRoot -p codeSign \
  -k /Library/Keychains/System.keychain "$TMP/cert.pem" >/dev/null

IDENTITIES="$(security find-identity -v -p codesigning "$KEYCHAIN" || true)"
if ! printf '%s\n' "$IDENTITIES" | grep -Fq "\"$IDENTITY\""; then
  echo 'ERROR: imported transport signing identity is not visible to codesign.' >&2
  printf '%s\n' "$IDENTITIES" >&2
  exit 1
fi
if ! security find-key -a "$KEYCHAIN" >/dev/null 2>&1; then
  echo 'ERROR: transport signing private key was not imported into the isolated keychain.' >&2
  security find-certificate -a -Z -c "$IDENTITY" "$KEYCHAIN" >&2 || true
  exit 1
fi

sign_one() {
  local target="$1"
  if ! codesign --force --timestamp=none --sign "$IDENTITY" "$target"; then
    echo "ERROR: codesign could not sign: $target" >&2
    echo '--- visible code-signing identities ---' >&2
    security find-identity -v -p codesigning "$KEYCHAIN" >&2 || true
    echo '--- keychain search list ---' >&2
    security list-keychains -d user >&2 || true
    echo '--- default keychain ---' >&2
    security default-keychain -d user >&2 || true
    exit 1
  fi
}

echo '[transport-sign] signing nested native libraries'
while IFS= read -r -d '' item; do
  sign_one "$item"
done < <(find "$APP/Contents" -type f \( -name '*.dylib' -o -name '*.so' \) -print0)

echo '[transport-sign] signing application bundle'
codesign --force --deep --options runtime --timestamp=none \
  --sign "$IDENTITY" "$APP"
codesign --verify --deep --strict --verbose=2 "$APP"
if codesign -dvvv "$APP" 2>&1 | grep -q 'Signature=adhoc'; then
  echo 'ERROR: transport app unexpectedly ended up ad-hoc signed.' >&2
  exit 1
fi

echo '[transport-sign] OK'
