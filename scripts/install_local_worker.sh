#!/usr/bin/env bash
# Keep the local worker running in the background so YouTube/TikTok links you
# paste into the web UI get picked up and downloaded from your home connection
# automatically — no need to remember to run anything.
#
#   scripts/install_local_worker.sh [interval_seconds] [browser]
#     interval_seconds  how often to poll the server   (default: 300)
#     browser           optional cookie source: chrome | firefox | safari | brave
#
# Uninstall:
#   launchctl unload ~/Library/LaunchAgents/dev.nodepilot.scrapper.localworker.plist

set -euo pipefail

INTERVAL="${1:-300}"
BROWSER="${2:-}"
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LABEL="dev.nodepilot.scrapper.localworker"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"

if [ ! -f "$REPO/data/deploy.env" ]; then
  echo "Missing $REPO/data/deploy.env (needs SERVER_URL and ACCESS_TOKEN)." >&2
  exit 1
fi

mkdir -p "$HOME/Library/LaunchAgents" "$REPO/logs"

BROWSER_ARGS=""
if [ -n "$BROWSER" ]; then
  BROWSER_ARGS="    <string>--browser</string><string>$BROWSER</string>"
fi

cat > "$PLIST" <<PLIST_EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>$LABEL</string>
  <key>ProgramArguments</key>
  <array>
    <string>/usr/bin/python3</string>
    <string>$REPO/scripts/local_worker.py</string>
    <string>--watch</string>
    <string>--interval</string><string>$INTERVAL</string>
$BROWSER_ARGS
  </array>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>StandardOutPath</key><string>$REPO/logs/local_worker.log</string>
  <key>StandardErrorPath</key><string>$REPO/logs/local_worker.log</string>
</dict>
</plist>
PLIST_EOF

launchctl unload "$PLIST" 2>/dev/null || true
launchctl load "$PLIST"

echo "Installed: $LABEL — polls every ${INTERVAL}s${BROWSER:+ (cookies from $BROWSER)}"
echo "Logs:   tail -f $REPO/logs/local_worker.log"
echo "Stop:   launchctl unload $PLIST"
