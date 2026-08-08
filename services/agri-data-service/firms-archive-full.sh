#!/usr/bin/env bash
# Drives the FIRMS lane of durable-archive-backfill.sh down to the FULL FIRMS archive floor.
#
# Retargeted 2026-08-07: this called durable-firms-archive-backfill.sh, which was generalized
# into durable-archive-backfill.sh when the USGS streamflow archive gained the same shape. The
# lock and cursor paths below are the FIRMS lane's and are unchanged, so a walk in flight at
# the time of that change resumes from exactly where it stopped.
#
# Owner decision 2026-08-07: the fire layer walks the whole archive, not the four-year
# fire-season window durable-firms-archive-backfill.sh defaults to. 2000-11-01 is not an
# arbitrary choice -- it is MODIS_SP's own published `min_date` and the oldest floor any
# member of FIRMS_HISTORY_SOURCES offers, mirrored in ingest/firms.py as
# FIRMS_ARCHIVE_EARLIEST_OBSERVATION. Asking for anything older is refused upstream.
#
# Why a wrapper instead of just passing the argument: a walk that already completed at a
# HIGHER floor leaves a `.done` sentinel, and the driver exits early on that sentinel
# forever after. The sentinel records completeness but not the floor it completed at, so a
# later decision to go deeper can never resume on its own. This clears the sentinel only
# while the recorded cursor is still above the deep floor -- i.e. only when there is
# genuinely more archive left -- and leaves it alone once the walk truly bottoms out, so
# repeated wakes settle into a no-op instead of re-walking forever.
#
# Re-invoking is always safe: the underlying walk is idempotent and cursor-resumable, and
# it holds its own lock, so a wake that lands while a walk is in flight simply exits.

set -uo pipefail
cd "$(dirname "$0")"

ARCHIVE_FLOOR="${1:-2000-11-01}"

RUN_DIR=".agri-local-runs"
CURSOR="$RUN_DIR/locks/firms-archive.cursor"
DONE="$RUN_DIR/locks/firms-archive.done"

# Clear a sentinel left by a shallower walk. String comparison is correct for ISO dates.
# With no cursor the walk has not recorded a boundary yet, so a sentinel can only have come
# from a shallower run and is cleared unconditionally.
if [ -f "$DONE" ]; then
  cursor_value="$(cat "$CURSOR" 2>/dev/null || echo '')"
  if [ -z "$cursor_value" ] || [ "$cursor_value" \> "$ARCHIVE_FLOOR" ]; then
    rm -f "$DONE"
  fi
fi

exec ./durable-archive-backfill.sh nasa-firms-archive "$ARCHIVE_FLOOR"
