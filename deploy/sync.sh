#!/usr/bin/env bash
# Push code changes into the live ~/nighttrade deployment and restart it.
#
# The live services run from ~/nighttrade (macOS will not let background
# services read ~/Desktop). Edit anywhere, then run this to deploy.
#
# Usage:  deploy/sync.sh [source-dir] [port]
#         source-dir defaults to ~/Desktop/coding/nighttrade, port to 8001
set -euo pipefail

SRC="${1:-$HOME/Desktop/coding/nighttrade}"
DEST="$HOME/nighttrade"
PORT="${2:-8001}"

if [ ! -d "$SRC/src/nighttrade" ]; then
  echo "ERROR: $SRC does not look like a nighttrade checkout." >&2
  exit 1
fi

# Note: deploy/_svc-run.sh is a machine-specific generated wrapper — never
# sync it, or a stale copy could repoint the services at the wrong path.
rsync -a --delete \
  --exclude='.git' --exclude='__pycache__' --exclude='*.pyc' \
  --exclude='.pytest_cache' --exclude='logs' --exclude='artifacts' \
  --exclude='data' --exclude='reports' --exclude='*.egg-info' \
  --exclude='deploy/_svc-run.sh' \
  "$SRC/" "$DEST/"
echo "synced $SRC -> $DEST"

# Reinstall — regenerates the wrapper + plists for THIS machine and reloads.
"$DEST/deploy/install.sh" "$PORT"

# launchd's KeepAlive doesn't always respawn after a graceful SIGTERM
# (observed 2026-06-02 — the observer stayed dead after install.sh's
# bootout/bootstrap because the prior process exited 0). Force-kick
# both services so we never leave a deploy half-done.
U=$(id -u)
for svc in com.nighttrade.observer com.nighttrade.dashboard; do
  launchctl kickstart -k "gui/$U/$svc" >/dev/null 2>&1 || true
done

# Health check — observer must show a PID within 15s, dashboard must
# bind :$PORT. If either doesn't, surface the failure loudly so the
# operator catches it now (not next morning).
sleep 2  # let launchd settle
fail=0
if ! pgrep -f 'nighttrade observe' >/dev/null; then
  echo "  WARN: observer did not respawn after kickstart" >&2
  fail=1
fi
if ! lsof -i :"$PORT" -sTCP:LISTEN >/dev/null 2>&1; then
  echo "  WARN: dashboard not bound on :$PORT after kickstart" >&2
  fail=1
fi
if [ "$fail" -eq 0 ]; then
  echo "  health: observer + dashboard both up"
fi
