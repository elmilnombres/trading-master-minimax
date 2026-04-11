#!/usr/bin/env bash
# =============================================================================
# Health check script — run inside container by Docker healthcheck.
# Exits 0 = healthy, exits 1 = unhealthy (Docker restarts container).
#
# Usage (inside Dockerfile HEALTHCHECK):
#   HEALTHCHECK --interval=30s --timeout=10s --start-period=60s --retries=3 \
#       CMD /app/deploy/healthcheck.sh
#
# The bot IDs this script monitors are passed as arguments.
# =============================================================================

set -euo pipefail

# Default: monitor all three bots
BOT_IDS="${*:-alpha_bot beta_bot gamma_bot}"
HEARTBEAT_DIR="/app/heartbeat"
MAX_AGE_MINUTES="${MAX_AGE_MINUTES:-2}"

unhealthy=0

for bot_id in $BOT_IDS; do
    hb_file="${HEARTBEAT_DIR}/${bot_id}_heartbeat.json"

    if [[ ! -f "$hb_file" ]]; then
        echo "[healthcheck] MISSING: $hb_file"
        unhealthy=1
        continue
    fi

    # Check file modified within MAX_AGE_MINUTES
    if ! find "$hb_file" -mmin "-${MAX_AGE_MINUTES}" -print -quiet 2>/dev/null; then
        echo "[healthcheck] STALE: $hb_file (older than ${MAX_AGE_MINUTES}m)"
        unhealthy=1
        continue
    fi

    echo "[healthcheck] OK: $hb_file"
done

if [[ $unhealthy -eq 1 ]]; then
    echo "[healthcheck] UNHEALTHY — one or more heartbeats missing or stale"
    exit 1
fi

echo "[healthcheck] ALL HEALTHY"
exit 0
