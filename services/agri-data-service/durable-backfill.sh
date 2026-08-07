#!/usr/bin/env bash
# Durable, resumable driver for one historical backfill lane.
#
# Designed to be re-invoked on a schedule. Every step is idempotent, so a crashed or
# killed run costs at most the in-flight unit: the lane's own checkpoint decides where
# work resumes, and persist refuses to run until that checkpoint is complete.
#
# Usage: ./durable-backfill.sh <lane> <plan-path>
#   lane: era5 | nasa | open-meteo
#
# Exit 0 means "nothing more to do right now" — including the normal case where the
# fetch is still incomplete and persist correctly declined. Only a genuine fault exits non-zero.

set -uo pipefail
cd "$(dirname "$0")"

LANE="${1:?lane required}"
PLAN="${2:?plan path required}"
SLUG="$(basename "$PLAN" .json)"
RUN_DIR=".agri-local-runs"
LOG="$RUN_DIR/logs/durable-$SLUG.log"
LOCK="$RUN_DIR/locks/$SLUG.lock"
DONE="$RUN_DIR/locks/$SLUG.done"

mkdir -p "$RUN_DIR/logs" "$RUN_DIR/locks"

log() { printf '%s %s\n' "$(date -u +%FT%TZ)" "$*" >> "$LOG"; }

if [ -f "$DONE" ]; then
  exit 0
fi

# mkdir is atomic, so it doubles as the lock acquisition. A lock whose recorded PID is
# gone is stale (killed run, reboot) and is reclaimed rather than blocking forever.
if ! mkdir "$LOCK" 2>/dev/null; then
  holder="$(cat "$LOCK/pid" 2>/dev/null || echo '')"
  if [ -n "$holder" ] && kill -0 "$holder" 2>/dev/null; then
    exit 0
  fi
  log "reclaiming stale lock from pid ${holder:-unknown}"
  rm -rf "$LOCK"
  mkdir "$LOCK" 2>/dev/null || exit 0
fi
echo $$ > "$LOCK/pid"
trap 'rm -rf "$LOCK"' EXIT

log "=== wake: lane=$LANE plan=$PLAN"

log "--- ${LANE} backfill (resumes from checkpoint)"
./run-backfill.sh "historical-${LANE}-backfill" --plan "$PLAN" >> "$LOG" 2>&1
log "backfill exit=$?"

# Exit code alone is NOT a completeness signal. The era5 lane exits non-zero on an
# incomplete checkpoint, but the open-meteo lane persists whatever chunks it has and
# still exits 0, reporting incompleteness only as a field in its JSON output. Treating
# exit 0 as "done" therefore retires a lane that has barely started, so the report field
# is authoritative and the exit code is only a secondary condition.
log "--- ${LANE} persist (writes what is cached; finalizes only when complete)"
persist_out="$RUN_DIR/logs/.persist-$SLUG.out"
./run-backfill.sh "historical-${LANE}-persist" --plan "$PLAN" > "$persist_out" 2>&1
persist_status=$?
cat "$persist_out" >> "$LOG"
log "persist exit=$persist_status"

incomplete=0
if grep -q '"finalization_blocked_by_incomplete_coverage": true' "$persist_out"; then
  incomplete=1
  log "persist reported incomplete coverage; lane stays open"
fi
if grep -q '"finalization_blocked_by_stale_release_set_as_of": true' "$persist_out"; then
  incomplete=1
  log "persist reported a stale release_set_as_of; lane stays open"
fi

if [ "$persist_status" -eq 0 ] && [ "$incomplete" -eq 0 ]; then
  date -u +%FT%TZ > "$DONE"
  log "=== LANE COMPLETE — persisted and finalized; future wakes are no-ops"
else
  log "=== still incomplete; will resume on the next wake"
fi

exit 0
