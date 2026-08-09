#!/usr/bin/env bash
# One-off: work through .agri-local-runs/locks/firms-archive.failures, the 169 windows
# banked since 2026-08-07 that account for the 2022-10-12..2026-07-23 hole in fire-detections.
# Confirmed live tonight that these are transient network failures, not a real upstream gap --
# a manually re-run window succeeded first try. Rewrites the failures file to only what still
# fails, same semantics as durable-archive-backfill.sh's own retry_ledger().
set -uo pipefail
cd "$(dirname "$0")"
set -a; . ./.env; set +a
export NASA_FIRMS_KEY="${NASA_FIRMS_KEY:-$(railway variables --service plantgeo-ingest-cron --kv 2>/dev/null | grep '^NASA_FIRMS_KEY=' | cut -d= -f2)}"
export INGEST_MAX_SOURCE_RECORDS=50000
BBOX="-125,42,-111,49"
FAILURES=".agri-local-runs/locks/firms-archive.failures"
LOG=".agri-local-runs/logs/fill-firms-gap.log"
STILL_FAILING="${FAILURES}.still"
: > "$STILL_FAILING"
log() { printf '%s %s\n' "$(date -u +%FT%TZ)" "$*" | tee -a "$LOG"; }

log "=== starting gap-fill pass over $(wc -l < "$FAILURES") banked windows"
n=0
ok=0
while IFS= read -r banked; do
  [ -n "$banked" ] || continue
  n=$((n+1))
  start="${banked%%..*}"
  end="${banked##*..}"
  attempt=1
  success=0
  while [ "$attempt" -le 3 ]; do
    log "[$n] window $start..$end attempt $attempt/3"
    if ./run-backfill.sh ingest-backfill --source nasa-firms-archive --since "$start" --until "$end" --chunk-days 1 --bbox "$BBOX" >> "$LOG" 2>&1; then
      success=1
      ok=$((ok+1))
      break
    fi
    sleep $((30 * attempt))
    attempt=$((attempt+1))
  done
  if [ "$success" -eq 0 ]; then
    echo "$banked" >> "$STILL_FAILING"
    log "[$n] window $start..$end still failing after 3 attempts; re-banked"
  fi
  sleep 3
done < "$FAILURES"

mv "$STILL_FAILING" "$FAILURES"
log "=== gap-fill pass done: $ok/$n windows resolved, $(wc -l < "$FAILURES") still banked"
