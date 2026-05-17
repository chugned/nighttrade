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
