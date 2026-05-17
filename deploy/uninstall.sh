#!/usr/bin/env bash
# Remove the nighttrade launchd services.
set -euo pipefail

AGENTS="$HOME/Library/LaunchAgents"
U="$(id -u)"

for label in com.nighttrade.observer com.nighttrade.dashboard; do
  plist="$AGENTS/$label.plist"
  launchctl bootout "gui/$U/$label" 2>/dev/null || true
  rm -f "$plist"
  echo "removed $label"
done

pkill -f 'nighttrade observe' 2>/dev/null || true
pkill -f 'nighttrade dashboard' 2>/dev/null || true
echo "nighttrade services stopped and removed."
