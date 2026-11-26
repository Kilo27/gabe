#!/bin/sh
# Build Gabe.app and a .dmg for macOS, then install the app into /Applications.
#   INSTALL=0 packaging/macos/build.sh  # build only
#   packaging/macos/build.sh            # signed with "Gabe Local Signing" if present (see make_signing_cert.sh), else ad-hoc
#   SIGN_ID="Developer ID Application: …" packaging/macos/build.sh   # signed for distribution
# Needs: Python venv with `pip install -e '.[desktop,gmail,build]'`, Node + npm.
# Gives dist/macos/Gabe.app, Gabe.dmg and Gabe-mac.zip (what install.sh and the updater download).
set -e
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$ROOT"
PY="${PYTHON:-$ROOT/.venv/bin/python}"
if [ -z "$PYTHON" ] && [ ! -x "$PY" ]; then
  # In a git worktree the venv lives in the main checkout.
  MAIN="$(cd "$(git rev-parse --git-common-dir)/.." && pwd)"
  PY="$MAIN/.venv/bin/python"
fi
if [ ! -x "$PY" ]; then
  echo "error: no Python venv at $PY (set PYTHON=/path/to/python)" >&2
  exit 1
fi
OUT="$ROOT/dist/macos"

if [ ! -f packaging/google_oauth_client.json ]; then
  echo "warning: packaging/google_oauth_client.json is missing; Sign in with Google won't work in this build." >&2
fi

echo "→ build stamp"
COMMIT="${GABE_COMMIT:-$(git rev-parse HEAD 2>/dev/null || true)}"
"$PY" -m gabe.updater stamp "$COMMIT"   # gabe/_build.py: the commit the updater compares with GitHub

echo "→ front end"
(cd web && npm ci --silent && npm run build --silent)

echo "→ icon"
if command -v magick >/dev/null 2>&1; then
  packaging/make_icons.sh >/dev/null
else  # the committed render of packaging/icon.svg
  mkdir -p packaging/build/icons && cp packaging/macos/Gabe.icns packaging/build/icons/Gabe.icns
fi

echo "→ app bundle"
"$PY" -m PyInstaller --noconfirm --clean --log-level WARN \
  --distpath "$OUT" --workpath "$ROOT/packaging/build/pyinstaller" packaging/macos/Gabe.spec

APP="$OUT/Gabe.app"
# Signing: a Developer ID (SIGN_ID) for distribution; otherwise the local "Gabe Local Signing"
# certificate (packaging/macos/make_signing_cert.sh), so every build keeps the same identity and
# macOS keeps Gabe's notification and Keychain permissions; ad-hoc only as a last resort.
LOCAL_ID="$(security find-identity -p codesigning 2>/dev/null | awk -F'"' '/Gabe Local Signing/ {print; exit}' | awk '{print $2}')"
[ -n "$SIGN_ID" ] || SIGN_ID="$LOCAL_ID"
case "$SIGN_ID" in
  "Developer ID"*)
    echo "→ signing (Developer ID: $SIGN_ID)"
    codesign --force --deep --options runtime --timestamp \
      --entitlements packaging/macos/entitlements.plist --sign "$SIGN_ID" "$APP" ;;
  "")
    echo "→ signing (ad-hoc; run packaging/macos/make_signing_cert.sh to keep permissions across builds)"
    codesign --force --deep --sign - "$APP" ;;
  *)
    echo "→ signing ($SIGN_ID)"
    codesign --force --deep --sign "$SIGN_ID" "$APP" ;;
esac
codesign --verify --deep --strict "$APP"

echo "→ disk image"
rm -f "$OUT/Gabe.dmg"
STAGE="$(mktemp -d)"
cp -R "$APP" "$STAGE/"
ln -s /Applications "$STAGE/Applications"
hdiutil create -quiet -volname Gabe -srcfolder "$STAGE" -ov -format UDZO "$OUT/Gabe.dmg"
rm -rf "$STAGE"

echo "→ zip"
rm -f "$OUT/Gabe-mac.zip"
ditto -c -k --keepParent "$APP" "$OUT/Gabe-mac.zip"

echo "Done: $APP"
echo "      $OUT/Gabe.dmg"
echo "      $OUT/Gabe-mac.zip"

# Install into /Applications, so the app outlives this checkout (dist/ is deleted with a worktree).
# INSTALL=0 skips this; INSTALL_DIR picks another folder (e.g. ~/Applications).
if [ "${INSTALL:-1}" != "0" ]; then
  DEST="${INSTALL_DIR:-/Applications}/Gabe.app"
  if pgrep -xq Gabe; then
    echo "→ quitting Gabe"
    osascript -e 'quit app "Gabe"' >/dev/null 2>&1 || true
    for _ in 1 2 3 4 5 6 7 8 9 10; do pgrep -xq Gabe || break; sleep 1; done
    pkill -x Gabe 2>/dev/null || true
  fi
  echo "→ installing to $DEST"
  mkdir -p "$(dirname "$DEST")"
  rm -rf "$DEST"
  ditto "$APP" "$DEST"
  codesign --verify --deep --strict "$DEST"
  echo "Installed: $DEST"
fi
