#!/bin/sh
# Build Gabe.app and a .dmg for macOS.
#   packaging/macos/build.sh            # ad-hoc signed (runs on this Mac; others get a Gatekeeper warning)
#   SIGN_ID="Developer ID Application: …" packaging/macos/build.sh   # signed for distribution
# Needs: Python venv with `pip install -e '.[desktop,gmail,build]'`, Node + npm, ImageMagick.
set -e
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$ROOT"
PY="${PYTHON:-$ROOT/.venv/bin/python}"
OUT="$ROOT/dist/macos"

if [ ! -f packaging/google_oauth_client.json ]; then
  echo "warning: packaging/google_oauth_client.json is missing; Sign in with Google won't work in this build." >&2
fi

echo "→ front end"
(cd web && npm ci --silent && npm run build --silent)

echo "→ icon"
packaging/make_icons.sh >/dev/null

echo "→ app bundle"
"$PY" -m PyInstaller --noconfirm --clean --log-level WARN \
  --distpath "$OUT" --workpath "$ROOT/packaging/build/pyinstaller" packaging/macos/Gabe.spec

APP="$OUT/Gabe.app"
echo "→ signing (${SIGN_ID:-ad-hoc})"
if [ -n "$SIGN_ID" ]; then
  codesign --force --deep --options runtime --timestamp --sign "$SIGN_ID" "$APP"
else
  codesign --force --deep --sign - "$APP"
fi
codesign --verify --deep --strict "$APP"

echo "→ disk image"
rm -f "$OUT/Gabe.dmg"
STAGE="$(mktemp -d)"
cp -R "$APP" "$STAGE/"
ln -s /Applications "$STAGE/Applications"
hdiutil create -quiet -volname Gabe -srcfolder "$STAGE" -ov -format UDZO "$OUT/Gabe.dmg"
rm -rf "$STAGE"

echo "Done: $APP"
echo "      $OUT/Gabe.dmg"
