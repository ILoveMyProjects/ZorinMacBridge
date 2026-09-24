#!/usr/bin/env bash
set -euo pipefail

APP="${1:?usage: sign-macos-transport.sh /path/to/App.app}"
TMP="${RUNNER_TEMP:-/tmp}/zmb-transport-signing-${RANDOM}-${RANDOM}"
KEYCHAIN="$TMP/transport.keychain-db"
KEYCHAIN_PASSWORD="$(openssl rand -hex 24)"
P12_PASSWORD="$(openssl rand -hex 24)"
IDENTITY="ZorinMacBridge Release Transport"
mkdir -p "$TMP"
trap 'sudo security remove-trusted-cert -d "$TMP/cert.pem" >/dev/null 2>&1 || true; security delete-keychain "$KEYCHAIN" >/dev/null 2>&1 || true; rm -rf "$TMP"' EXIT

# This signature is intentionally ephemeral. Its only purpose is to make the
# downloaded release a normally signed transport artifact so old updaters can
# verify it. The installer/updater re-signs the app on each Mac with that Mac's
# persistent local identity before installation.
openssl req -x509 -newkey rsa:3072 -sha256 -nodes -days 30 \
  -keyout "$TMP/key.pem" -out "$TMP/cert.pem" \
  -subj "/CN=$IDENTITY/O=ILoveMyProjects/OU=ZorinMacBridge/" \
  -addext 'basicConstraints=critical,CA:TRUE' \
  -addext 'keyUsage=critical,digitalSignature,keyCertSign' \
  -addext 'extendedKeyUsage=critical,codeSigning' >/dev/null 2>&1
openssl pkcs12 -export -out "$TMP/identity.p12" -inkey "$TMP/key.pem" -in "$TMP/cert.pem" \
  -name "$IDENTITY" -passout "pass:$P12_PASSWORD"
SHA1="$(openssl x509 -in "$TMP/cert.pem" -noout -fingerprint -sha1 | sed 's/^.*=//' | tr -d ':')"

security create-keychain -p "$KEYCHAIN_PASSWORD" "$KEYCHAIN"
security unlock-keychain -p "$KEYCHAIN_PASSWORD" "$KEYCHAIN"
security import "$TMP/identity.p12" -k "$KEYCHAIN" -P "$P12_PASSWORD" -T /usr/bin/codesign -T /usr/bin/security >/dev/null
security set-key-partition-list -S apple-tool:,apple:,codesign: -s -k "$KEYCHAIN_PASSWORD" "$KEYCHAIN" >/dev/null
sudo security add-trusted-cert -d -r trustRoot -p codeSign -k /Library/Keychains/System.keychain "$TMP/cert.pem" >/dev/null

while IFS= read -r -d '' item; do
  codesign --force --timestamp=none --keychain "$KEYCHAIN" --sign "$SHA1" "$item"
done < <(find "$APP/Contents" -type f \( -name '*.dylib' -o -name '*.so' \) -print0)

codesign --force --deep --options runtime --timestamp=none --keychain "$KEYCHAIN" --sign "$SHA1" "$APP"
codesign --verify --deep --strict --verbose=2 "$APP"
if codesign -dvvv "$APP" 2>&1 | grep -q 'Signature=adhoc'; then
  echo 'ERROR: transport app unexpectedly ended up ad-hoc signed.' >&2
  exit 1
fi
echo 'Transport signature OK. Installed copies will be re-signed locally with a persistent per-Mac identity.'
