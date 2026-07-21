#!/usr/bin/env bash
# Install a macOS launchd job that refreshes the server's cookies from your
# local browser once a day. Cookies expire, so a stale jar silently stops
# helping — this keeps it current without you remembering.
#
#   scripts/install_cookie_sync.sh [browser] [hour]
#     browser  chrome | firefox | safari | edge | brave   (default: chrome)
#     hour     0-23 local time                            (default: 9)
#
# Uninstall:  launchctl unload ~/Library/LaunchAgents/dev.nodepilot.scrapper.cookiesync.plist

set -euo pipefail

BROWSER="${1:-chrome}"
HOUR="${2:-9}"
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LABEL="dev.nodepilot.scrapper.cookiesync"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"

if [ ! -f "$REPO/data/deploy.env" ]; then
  echo "Missing $REPO/data/deploy.env (needs SERVER_URL and ACCESS_TOKEN)." >&2
  exit 1
fi

mkdir -p "$HOME/Library/LaunchAgents" "$REPO/logs"

cat > "$PLIST" <<PLIST_EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>$LABEL</string>
  <key>ProgramArguments</key>
  <array>
    <string>/usr/bin/python3</string>
    <string>$REPO/scripts/sync_cookies.py</string>
    <string>--browser</string><string>$BROWSER</string>
  </array>
  <key>StartCalendarInterval</key>
  <dict><key>Hour</key><integer>$HOUR</integer><key>Minute</key><integer>0</integer></dict>
  <key>StandardOutPath</key><string>$REPO/logs/cookie_sync.log</string>
  <key>StandardErrorPath</key><string>$REPO/logs/cookie_sync.log</string>
  <key>RunAtLoad</key><false/>
</dict>
</plist>
PLIST_EOF

launchctl unload "$PLIST" 2>/dev/null || true
launchctl load "$PLIST"

echo "Installed: $LABEL — refreshes $BROWSER cookies daily at ${HOUR}:00"
echo "Logs: $REPO/logs/cookie_sync.log"
echo "Run once now:  python3 $REPO/scripts/sync_cookies.py --browser $BROWSER"
