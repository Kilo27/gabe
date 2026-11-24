#!/bin/bash
# Install (or reinstall) a macOS LaunchAgent that runs `gabe brief` at the time
# and on the days set under `briefing:` in the config. launchd uses the Mac's
# local timezone. A run missed while the Mac was asleep fires on wake.
#
#   scripts/install_launchd.sh            # install / update
#   scripts/install_launchd.sh uninstall  # remove
set -euo pipefail

LABEL="com.gabe.daily-brief"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
CONFIG="${GABE_CONFIG:-$ROOT/config/gabe.yaml}"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
LOGS="$ROOT/data/logs"
DOMAIN="gui/$(id -u)"

launchctl bootout "$DOMAIN/$LABEL" 2>/dev/null || true
if [[ "${1:-}" == "uninstall" ]]; then
  rm -f "$PLIST"
  echo "Removed $LABEL."
  exit 0
fi

mkdir -p "$LOGS" "$(dirname "$PLIST")"

# Turn briefing.time / briefing.days into StartCalendarInterval entries.
INTERVALS="$("$ROOT/.venv/bin/python" - "$CONFIG" <<'PY'
import sys
from gabe.briefing import BriefingSettings, DAYS
from gabe.config import load_config
s = BriefingSettings.from_config(load_config(sys.argv[1]))
h, m = (int(x) for x in s.time.split(":"))
for d in s.days:
    wd = (DAYS.index(d) + 1) % 7  # launchd: 0 = Sunday, 1 = Monday
    print(f"    <dict><key>Weekday</key><integer>{wd}</integer>"
          f"<key>Hour</key><integer>{h}</integer><key>Minute</key><integer>{m}</integer></dict>")
PY
)"

cat > "$PLIST" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>$LABEL</string>
  <key>ProgramArguments</key>
  <array>
    <string>$ROOT/.venv/bin/gabe</string>
    <string>-c</string><string>$CONFIG</string>
    <string>brief</string>
  </array>
  <key>WorkingDirectory</key><string>$ROOT</string>
  <key>StartCalendarInterval</key>
  <array>
$INTERVALS
  </array>
  <key>StandardOutPath</key><string>$LOGS/daily-brief.log</string>
  <key>StandardErrorPath</key><string>$LOGS/daily-brief.err.log</string>
</dict>
</plist>
PLIST

plutil -lint "$PLIST" >/dev/null
launchctl bootstrap "$DOMAIN" "$PLIST"
echo "Installed $LABEL -> $PLIST"
echo "Logs: $LOGS/daily-brief.log, $LOGS/daily-brief.err.log"
echo "Run now: launchctl kickstart $DOMAIN/$LABEL"
