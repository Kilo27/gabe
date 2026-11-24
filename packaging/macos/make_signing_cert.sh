#!/bin/sh
# Create a local code-signing certificate ("Gabe Local Signing") in the login keychain, so
# every build of Gabe.app has the same signature. macOS then keeps Gabe's notification
# permission and Keychain access across rebuilds. Local use only: other Macs still need a
# Developer ID (SIGN_ID) and notarization.
set -e
NAME="Gabe Local Signing"
if security find-identity -p codesigning | grep -q "$NAME"; then
  echo "\"$NAME\" already exists."; exit 0
fi
TMP="$(mktemp -d)"; trap 'rm -rf "$TMP"' EXIT
cat > "$TMP/cert.cnf" <<CNF
[req]
distinguished_name = dn
x509_extensions = ext
prompt = no
[dn]
CN = $NAME
[ext]
basicConstraints = critical, CA:false
keyUsage = critical, digitalSignature
extendedKeyUsage = critical, codeSigning
CNF
/usr/bin/openssl req -x509 -newkey rsa:2048 -nodes -days 3650 -keyout "$TMP/key.pem" -out "$TMP/cert.pem" -config "$TMP/cert.cnf" 2>/dev/null
PASS="$(/usr/bin/openssl rand -hex 16)"
/usr/bin/openssl pkcs12 -export -inkey "$TMP/key.pem" -in "$TMP/cert.pem" -name "$NAME" -out "$TMP/id.p12" -passout "pass:$PASS"
# -T /usr/bin/codesign: codesign may use the key without asking each build.
security import "$TMP/id.p12" -k "$HOME/Library/Keychains/login.keychain-db" -P "$PASS" -T /usr/bin/codesign >/dev/null
echo "Created \"$NAME\" in your login keychain."
