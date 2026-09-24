#!/usr/bin/env bash
set -euo pipefail

APP="${1:?usage: sign-macos-transport.sh /path/to/App.app}"
TMP="${RUNNER_TEMP:-/tmp}/zmb-transport-signing-${RANDOM}-${RANDOM}"
KEYCHAIN="$TMP/transport.keychain-db"
KEYCHAIN_PASSWORD="$(openssl rand -hex 24)"
P12_PASSWORD="$(openssl rand -hex 24)"
IDENTITY="ZorinMacBridge Release Transport"
mkdir -p "$TMP"

cleanup() {
  sudo security remove-trusted-cert -d "$TMP/cert.pem" >/dev/null 2>&1 || true
  security lock-keychain "$KEYCHAIN" >/dev/null 2>&1 || true
  security delete-keychain "$KEYCHAIN" >/dev/null 2>&1 || true
  rm -rf "$TMP"
}
trap cleanup EXIT

# This signature is intentionally ephemeral. Its only purpose is to make the
# downloaded release a normally signed transport artifact so pre-v0.6 updaters
# can accept the migration release. Installed copies are re-signed on each Mac
# with that Mac's persistent local identity before installation.
echo '[transport-sign] generating ephemeral code-signing identity'
openssl req -x509 -newkey rsa:3072 -sha256 -nodes -days 30 \
  -keyout "$TMP/key.pem" -out "$TMP/cert.pem" \
  -subj "/CN=$IDENTITY/O=ILoveMyProjects/OU=ZorinMacBridge/" \
  -addext 'basicConstraints=critical,CA:TRUE' \
  -addext 'keyUsage=critical,digitalSignature,keyCertSign' \
  -addext 'extendedKeyUsage=critical,codeSigning' >/dev/null 2>&1
openssl pkcs12 -export -out "$TMP/identity.p12" -inkey "$TMP/key.pem" -in "$TMP/cert.pem" \
  -name "$IDENTITY" -passout "pass:$P12_PASSWORD"
SHA1="$(openssl x509 -in "$TMP/cert.pem" -noout -fingerprint -sha1 | sed 's/^.*=//' | tr -d ':')"

echo '[transport-sign] creating isolated keychain'
security create-keychain -p "$KEYCHAIN_PASSWORD" "$KEYCHAIN"
security set-keychain-settings -lut 600 "$KEYCHAIN"
security unlock-keychain -p "$KEYCHAIN_PASSWORD" "$KEYCHAIN"

# macOS 15 hosted runners can return "The specified item could not be found in
# the keychain" from set-key-partition-list even immediately after a successful
# import. This keychain is ephemeral and deleted at job end, so import with -A
# instead of relying on that brittle partition-list mutation.
echo '[transport-sign] importing identity'
security import "$TMP/identity.p12" -k "$KEYCHAIN" -P "$P12_PASSWORD" -A >/dev/null

# codesign requires a trusted chain when selecting a signing identity. Trust the
# temporary self-signed root for the code-signing policy only for this CI job.
echo '[transport-sign] trusting ephemeral identity for code signing'
sudo security add-trusted-cert -d -r trustRoot -p codeSign \
  -k /Library/Keychains/System.keychain "$TMP/cert.pem" >/dev/null

IDENTITIES="$(security find-identity -v -p codesigning "$KEYCHAIN" || true)"
if ! printf '%s\n' "$IDENTITIES" | tr -d ' ' | grep -qi "$SHA1"; then
  echo 'ERROR: imported transport signing identity is not visible to codesign.' >&2
  printf '%s\n' "$IDENTITIES" >&2
  exit 1
fi

echo '[transport-sign] signing nested native libraries'
while IFS= read -r -d '' item; do
  codesign --force --timestamp=none --keychain "$KEYCHAIN" --sign "$SHA1" "$item"
done < <(find "$APP/Contents" -type f \( -name '*.dylib' -o -name '*.so' \) -print0)

echo '[transport-sign] signing application bundle'
codesign --force --deep --options runtime --timestamp=none \
  --keychain "$KEYCHAIN" --sign "$SHA1" "$APP"
codesign --verify --deep --strict --verbose=2 "$APP"
if codesign -dvvv "$APP" 2>&1 | grep -q 'Signature=adhoc'; then
  echo 'ERROR: transport app unexpectedly ended up ad-hoc signed.' >&2
  exit 1
fi

echo '[transport-sign] OK'
