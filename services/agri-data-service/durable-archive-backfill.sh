#!/usr/bin/env bash
# Durable, resumable driver for an `ingest-backfill` archive walk.
#
# Distinct from durable-backfill.sh, which drives the PLAN-based historical lanes
# (era5/nasa/open-meteo) through their own checkpoint tables. `ingest-backfill` has no
# checkpoint table at all -- `history_chunks` anchors chunks at the window start with a
# fixed step, so the same window always yields the same grid and an operator resumes by
# naming a boundary. A cursor file is therefore the whole of the durability contract here.
#
# Usage: ./durable-archive-backfill.sh <source-token> [earliest-day]
#   source-token: nasa-firms-archive | usgs-streamflow-archive
#
# Built to be re-invoked on a schedule and to make progress every time. One invocation
# walks until it finishes or is killed; the next picks up from the cursor. Re-walking is
# always safe: the feature writer refreshes by `properties->>'id'` and its diff rejects an
# unchanged payload, so a repeated day is a no-op rather than a duplicate.

set -uo pipefail
cd "$(dirname "$0")"

SOURCE_TOKEN="${1:?source token required: nasa-firms-archive | usgs-streamflow-archive}"

# Per-source walk shape. The two differ in every number that matters, and each is a measured
# value rather than a preference -- see the comments on each case.
case "$SOURCE_TOKEN" in
  nasa-firms-archive)
    SLUG="firms-archive"
    # Matches the vegetation layer's earliest observed day, so the fire layer can fill the
    # same four-year axis the slider already draws.
    DEFAULT_FLOOR="2022-08-05"
    # One day per fetched chunk, always. Peak PNW fire season runs ~21k detections/day, so
    # the default 7-day chunk overruns even the 50000 ceiling -- and an overrun does NOT
    # thin the sample, it drops the OLDEST days of the chunk whole and writes nothing. A
    # too-large chunk is a silent hole in the record. Measured 2026-08-06: a 5-day July
    # chunk saw 23,651 records and wrote 0.
    CHUNK_DAYS=1
    # Small, because peak-season days are slow: one 2026-07-23 chunk took 11.5 minutes.
    # A window is the unit a killed run redoes, so it is kept near an hour of work.
    WINDOW_DAYS=5
    NEEDS_FIRMS_KEY=1
    ;;
  usgs-streamflow-archive)
    SLUG="streamflow-archive"
    # The daily-values service reaches the 1890s, but USGS_DAILY_VALUES_EARLIEST refuses a
    # walk below this day for the reason given there: no other layer can populate those years.
    DEFAULT_FLOOR="2022-08-05"
    # Measured 2026-08-07: the full PNW box yields ~807 gauge-days per day, so a 10-day chunk
    # is ~8,070 records -- comfortably inside the 50000 ceiling, and far fewer round trips
    # than FIRMS needs. Daily values are one reading per gauge per day, not a detection cloud.
    CHUNK_DAYS=10
    WINDOW_DAYS=30
    NEEDS_FIRMS_KEY=0
    ;;
  *)
    echo "unknown source token: $SOURCE_TOKEN" >&2
    exit 2
    ;;
esac

FLOOR="${2:-$DEFAULT_FLOOR}"

# The PNW pilot extract, identical to INGEST_BBOX on plantgeo-ingest-cron. Passing it
# explicitly is required: INGEST_BBOX is unset in every local .env, and without it every
# chunk exits `skipped` with "INGEST_BBOX is not configured" -- which reads as a refusal
# rather than as a missing variable.
BBOX="${INGEST_BBOX:--125,42,-111,49}"

# The ceiling the ingest layer allows. FIRMS peak days (~21k) clear it with room; the
# default 10000 does not.
export INGEST_MAX_SOURCE_RECORDS=50000

RUN_DIR=".agri-local-runs"
LOG="$RUN_DIR/logs/durable-$SLUG.log"
LOCK="$RUN_DIR/locks/$SLUG.lock"
CURSOR="$RUN_DIR/locks/$SLUG.cursor"
DONE="$RUN_DIR/locks/$SLUG.done"
# Windows that exhausted their retries, one "START..END" per line. This ledger is what makes
# the walk actually complete rather than merely reach the floor -- see walk_window.
FAILURES="$RUN_DIR/locks/$SLUG.failures"

# Attempts per window before it is banked as a failure, and the backoff between them.
#
# Not defensive padding: measured 2026-08-07 over the first full pass, 169 of 298 FIRMS windows
# and 46 of 95 streamflow windows failed, almost entirely `ConnectError` with a couple of
# `getaddrinfo failed`. That signature is local socket and DNS exhaustion from firing windows
# back to back, not an upstream that is down -- the same days succeed on a later attempt.
WINDOW_ATTEMPTS=3
RETRY_BACKOFF_SECONDS=30

# Breathing room between windows, for the same reason. A few seconds per window is nothing
# against a multi-hour walk and it is what keeps the connection table from filling up.
INTER_WINDOW_SECONDS=5

mkdir -p "$RUN_DIR/logs" "$RUN_DIR/locks"

log() { printf '%s %s\n' "$(date -u +%FT%TZ)" "$*" | tee -a "$LOG"; }

if [ -f "$DONE" ]; then
  exit 0
fi

# mkdir is atomic, so it doubles as lock acquisition -- same idiom as durable-backfill.sh.
# This is what makes the script safe to put on a schedule: a tick that fires while the
# previous one is still walking exits immediately instead of starting a second walk over
# the same window. A lock whose recorded PID is gone is stale (killed run, reboot) and is
# reclaimed rather than blocking the lane forever.
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

# The FIRMS key lives only on the cron service; no local .env carries it. Read once for the
# whole walk rather than per invocation, because `railway variables` is a network round trip.
if [ "$NEEDS_FIRMS_KEY" -eq 1 ] && [ -z "${NASA_FIRMS_KEY:-}" ]; then
  NASA_FIRMS_KEY="$(railway variables --service plantgeo-ingest-cron --kv 2>/dev/null \
    | grep '^NASA_FIRMS_KEY=' | cut -d= -f2)"
  if [ -z "${NASA_FIRMS_KEY:-}" ]; then
    log "FATAL: NASA_FIRMS_KEY unavailable from the environment or the Railway CLI"
    exit 1
  fi
  export NASA_FIRMS_KEY
fi

# The walk runs BACKWARD, newest window first, and that is a correctness choice rather than a
# preference. getSliderCapabilities does not take the axis from min(observed_day): it keeps
# only the newest CLUSTER of days separated by no more than 45 days, then applies a density
# floor. Filling from 2022 forward would build a second cluster four years away from the live
# one and move the slider's axis start by nothing at all until the two finally met -- so days
# of correct work would look like no work. Walking backward extends the existing cluster one
# window at a time, and the axis grows with every window that lands.
if [ -f "$CURSOR" ]; then
  WINDOW_END="$(cat "$CURSOR")"
  log "=== $SOURCE_TOKEN resuming: next window ends $WINDOW_END (floor $FLOOR)"
else
  WINDOW_END="$(date -u +%F)"
  log "=== $SOURCE_TOKEN starting: floor=$FLOOR bbox=$BBOX window=${WINDOW_DAYS}d chunk=${CHUNK_DAYS}d"
fi

# Walks one window, retrying a transient upstream refusal before giving up on it.
# Returns 0 when the window landed, 1 when every attempt failed.
walk_window() {
  window_start="$1"
  window_end="$2"
  attempt=1
  while [ "$attempt" -le "$WINDOW_ATTEMPTS" ]; do
    log "--- $SOURCE_TOKEN window $window_start..$window_end (attempt $attempt/$WINDOW_ATTEMPTS)"
    ./run-backfill.sh data ingest-backfill \
      --source "$SOURCE_TOKEN" \
      --since "$window_start" \
      --until "$window_end" \
      --chunk-days "$CHUNK_DAYS" \
      --bbox "$BBOX" >> "$LOG" 2>&1
    if [ $? -eq 0 ]; then
      return 0
    fi
    # Linear rather than exponential: the fault being backed off from is a local connection
    # table that drains on its own, not an upstream asking for ever-longer waits.
    sleep $((RETRY_BACKOFF_SECONDS * attempt))
    attempt=$((attempt + 1))
  done
  log "$SOURCE_TOKEN window $window_start..$window_end failed $WINDOW_ATTEMPTS attempts; banking it"
  echo "$window_start..$window_end" >> "$FAILURES"
  return 1
}

# Re-walks the banked ledger. Each pass rewrites it with only what still fails, so a pass that
# fixes nothing leaves it unchanged and the loop stops rather than spinning.
retry_ledger() {
  pass=1
  while [ -s "$FAILURES" ] && [ "$pass" -le "$WINDOW_ATTEMPTS" ]; do
    remaining="$(wc -l < "$FAILURES" | tr -d ' ')"
    log "=== $SOURCE_TOKEN retry pass $pass over $remaining banked window(s)"
    mv "$FAILURES" "$FAILURES.inflight"
    while IFS= read -r banked; do
      [ -n "$banked" ] || continue
      walk_window "${banked%%..*}" "${banked##*..}"
      sleep "$INTER_WINDOW_SECONDS"
    done < "$FAILURES.inflight"
    rm -f "$FAILURES.inflight"
    pass=$((pass + 1))
  done
}

# Repair before opening new ground. A lane with a deep floor spends many wakes descending, and
# holes banked early would otherwise wait for the whole descent to finish before anything
# retried them -- for the FIRMS lane, whose floor is 2000-11-01, that is effectively never.
retry_ledger

# Descend to the floor. The cursor advances past a failed window rather than stalling the whole
# walk on one bad day -- but the window is banked in the ledger first, so advancing is no longer
# the same as abandoning it. That distinction is the fix for a run that reached the floor with
# 46 of 95 windows silently missing and still wrote its completion sentinel.
while [ "$WINDOW_END" \> "$FLOOR" ]; do
  WINDOW_START="$(date -u -d "$WINDOW_END - $WINDOW_DAYS days" +%F)"
  if [ "$WINDOW_START" \< "$FLOOR" ]; then
    WINDOW_START="$FLOOR"
  fi

  walk_window "$WINDOW_START" "$WINDOW_END"

  WINDOW_END="$WINDOW_START"
  echo "$WINDOW_END" > "$CURSOR"
  sleep "$INTER_WINDOW_SECONDS"
done

# Anything banked during the descent above gets its passes too, rather than waiting for the
# next wake.
retry_ledger

# The sentinel means "every window landed", never merely "the cursor reached the floor". Leaving
# it unwritten is what lets the 15-minute schedule pick the lane back up: the next wake resumes
# from the cursor, and while the ledger has entries the walk is not done.
if [ -s "$FAILURES" ]; then
  log "=== $SOURCE_TOKEN reached $FLOOR with $(wc -l < "$FAILURES" | tr -d ' ') window(s) unresolved; NOT marking done"
  exit 1
fi

rm -f "$FAILURES"
log "=== $SOURCE_TOKEN walk complete down to $FLOOR, every window landed"
touch "$DONE"
