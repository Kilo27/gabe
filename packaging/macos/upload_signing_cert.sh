#!/bin/sh
# Give the "Mac app" GitHub workflow this Mac's "Gabe Local Signing" identity (make_signing_cert.sh),
# so the builds everyone installs are all signed the same way and macOS keeps Gabe's Keychain and
# notification permissions across updates. Sets the MACOS_CERT_P12 and MACOS_CERT_PASSWORD secrets.
# Needs the GitHub CLI (gh auth login). macOS asks once to allow exporting the key.
set -e
NAME="Gabe Local Signing"
REPO="${GABE_REPO:-Kilo27/gabe}"
OSSL=/usr/bin/openssl
security find-identity -p codesigning | grep -q "$NAME" || { echo "No \"$NAME\" identity. Run make_signing_cert.sh first." >&2; exit 1; }
TMP="$(mktemp -d)"; trap 'rm -rf "$TMP"' EXIT
P1="$($OSSL rand -hex 16)"; P2="$($OSSL rand -hex 16)"
security export -k "$HOME/Library/Keychains/login.keychain-db" -t identities -f pkcs12 -P "$P1" -o "$TMP/all.p12"
$OSSL pkcs12 -in "$TMP/all.p12" -passin "pass:$P1" -nodes -out "$TMP/all.pem" 2>/dev/null
# Keep only the Gabe Local Signing key and certificate.
awk -v n="$NAME" '
  /^Bag Attributes/ { keep = 0 }
  /friendlyName:/   { keep = index($0, n) > 0 }
  keep' "$TMP/all.pem" > "$TMP/gabe.pem"
grep -q "BEGIN CERTIFICATE" "$TMP/gabe.pem" && grep -q "PRIVATE KEY" "$TMP/gabe.pem" || { echo "Couldn't find \"$NAME\" in the export." >&2; exit 1; }
$OSSL pkcs12 -export -in "$TMP/gabe.pem" -name "$NAME" -out "$TMP/gabe.p12" -passout "pass:$P2"
base64 -i "$TMP/gabe.p12" | tr -d '\n' | gh secret set MACOS_CERT_P12 --repo "$REPO"
printf '%s' "$P2" | gh secret set MACOS_CERT_PASSWORD --repo "$REPO"
echo "Saved \"$NAME\" to $REPO's MACOS_CERT_P12 / MACOS_CERT_PASSWORD secrets."
