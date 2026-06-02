#!/usr/bin/env bash
# Defence-in-depth watchdog for the nighttrade services.
#
# The 2026-06-02 incident was: Tailscale interface flap during a deploy →
# dashboard hit EADDRNOTAVAIL three times → launchd parked it. The
# tailnet middleware + 0.0.0.0 bind + ThrottleInterval=120s fix the
# specific failure mode. This script is the belt-and-suspenders layer:
# every N minutes, verify both services are healthy and kickstart them
# if not.
#
# Wiring options (operator picks one):
#
#   * cron       (every 5 min):
#       */5 * * * * /Users/nedimvejo/nighttrade/deploy/watchdog.sh
#
#   * launchd:   install a third plist with ProgramArguments pointing
#                here + StartInterval=300. Mirror the existing plists in
#                deploy/install.sh::make_plist (without --throttle).
#
# Read-only against the bot's DBs. Only side effect: launchctl kickstart
# on a parked service. Logs to logs/watchdog.log.
set -euo pipefail

REPO="${NT_REPO:-$HOME/nighttrade}"
LOG="$REPO/logs/watchdog.log"
PORT="${NT_DASHBOARD_PORT:-8001}"
U=$(id -u)

mkdir -p "$REPO/logs"

log() {
  printf '%s  %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$*" >> "$LOG"
}

kick() {
  local svc="$1"
  log "  kickstart $svc"
  launchctl kickstart -k "gui/$U/$svc" >/dev/null 2>&1 || true
}

# Observer: is the Python process alive?
observer_alive() {
  pgrep -f 'nighttrade observe' > /dev/null
}

# Dashboard: is the configured port bound by *any* process?
dashboard_bound() {
  lsof -i :"$PORT" -sTCP:LISTEN > /dev/null 2>&1
}

issues=0
if ! observer_alive; then
  log "WARN observer not running"
  kick com.nighttrade.observer
  issues=$((issues + 1))
fi

if ! dashboard_bound; then
  log "WARN dashboard not bound on :$PORT"
  kick com.nighttrade.dashboard
  issues=$((issues + 1))
fi

if [ "$issues" -eq 0 ]; then
  # Quiet success — only log every Nth tick (10) so the log isn't
  # spammed by 288 successful checks per day.
  tick_marker="$REPO/data/.watchdog_quiet_count"
  count=$(cat "$tick_marker" 2>/dev/null || echo "0")
  count=$((count + 1))
  if [ "$count" -ge 10 ]; then
    log "ok observer + dashboard both healthy ($count quiet ticks)"
    echo "0" > "$tick_marker"
  else
    echo "$count" > "$tick_marker"
  fi
  exit 0
fi

# Wait briefly for kickstart to take effect, then re-check
sleep 8
recovered=1
if ! observer_alive; then
  log "STILL DOWN: observer did not respawn after kickstart"
  recovered=0
fi
if ! dashboard_bound; then
  log "STILL DOWN: dashboard did not bind on :$PORT after kickstart"
  recovered=0
fi

if [ "$recovered" -eq 1 ]; then
  log "recovered after kickstart"
  exit 0
else
  log "ERROR not recovered — manual intervention required"
  exit 2
fi
