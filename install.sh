#!/bin/sh
# Install Gabe on a Mac:
#   curl -fsSL https://raw.githubusercontent.com/Kilo27/gabe/main/install.sh | sh
# Downloads the latest build of Gabe for Mac, puts it in /Applications (or ~/Applications when
# /Applications isn't writable) and opens it. Run it again to reinstall; Gabe updates itself after.
set -e
REPO="${GABE_REPO:-Kilo27/gabe}"
URL="https://github.com/$REPO/releases/download/mac-latest/Gabe-mac.zip"

[ "$(uname -s)" = Darwin ] || { echo "This installer is for macOS." >&2; exit 1; }
[ "$(uname -m)" = arm64 ] || { echo "Gabe for Mac needs an Apple silicon Mac (M1 or later)." >&2; exit 1; }

DIR="${INSTALL_DIR:-/Applications}"
if [ -z "$INSTALL_DIR" ] && [ ! -w /Applications ]; then DIR="$HOME/Applications"; fi
mkdir -p "$DIR"

TMP="$(mktemp -d)"; trap 'rm -rf "$TMP"' EXIT
echo "Downloading Gabe…"
curl -fL --progress-bar -o "$TMP/Gabe.zip" "$URL"
ditto -x -k "$TMP/Gabe.zip" "$TMP/x"
[ -d "$TMP/x/Gabe.app" ] || { echo "The download didn't contain Gabe.app." >&2; exit 1; }

if pgrep -xq Gabe; then
  osascript -e 'quit app "Gabe"' >/dev/null 2>&1 || true
  for _ in 1 2 3 4 5 6 7 8 9 10; do pgrep -xq Gabe || break; sleep 1; done
  pkill -x Gabe 2>/dev/null || true
fi

rm -rf "$DIR/Gabe.app"
ditto "$TMP/x/Gabe.app" "$DIR/Gabe.app"
xattr -dr com.apple.quarantine "$DIR/Gabe.app" 2>/dev/null || true
echo "Installed $DIR/Gabe.app"
open "$DIR/Gabe.app"
