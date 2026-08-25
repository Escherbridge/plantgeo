"""Give agri.matview_refresh_state the two columns a consecutive-failure backoff needs.

Revision ID: 20260817_0025
Revises: 20260816_0024

WHAT THIS CLOSES. `agri.matview_refresh_state` records `refreshed_at` -- the last time a view
actually succeeded -- and nothing else about time. `upsert_matview_refresh_state.sql` deliberately
COALESCEs a failed attempt's NULL onto the stored value so a failure never erases a real prior
success, which is correct. The consequence is that a view which has NEVER succeeded and a view
which has never been ATTEMPTED are the same row: `refreshed_at IS NULL`. `matview_refresh.py`'s
eligibility gate reads that as "never successfully refreshed" and returns eligible, so a view whose
REFRESH cannot possibly succeed is re-attempted on EVERY tick, forever, with no backoff.

Measured on production 2026-08-17, FOUR views were in that loop, not three, and they reach it by two
different routes:

  geo.mv_signal_observation_day   refreshed_at NULL, 300,238 ms -- its 300 s timeout, to the ms
  geo.mv_soil_survey_grid         refreshed_at NULL,  86,320 ms
  geo.mv_soil_survey_union        refreshed_at NULL, 104,269 ms
  geo.mv_feature_observation_day  refreshed_at SET,  300,257 ms, outcome 'failed'

The first three take the never-succeeded branch described above. The fourth is the one an earlier
draft of this docstring missed: it HAS a prior success, so the COALESCE keeps a non-NULL
`refreshed_at`, and it re-enters on every tick through the WATERMARK gate instead -- its watermark is
`max(updated_at)` over `geo.features`, which moves on every ingest. Same unbounded loop, different
door, and the backoff added here covers both because it sits in front of the whole gate.

That is **791 s** of guaranteed-doomed REFRESH per hourly tick, not the ~449 s a first pass counted
from three views and stale durations. `agri.job_attempt` over the preceding 48 hours holds 47 failed
attempts against 2 succeeded for the `matview-refresh` definition.

TWO COLUMNS, AND WHY NEITHER IS THE OTHER.

`last_attempt_at` is written on EVERY recorded attempt, successful or not. `refreshed_at` cannot
serve as the backoff clock precisely because of the COALESCE above: on a never-succeeded view it
stays NULL forever, so "how long since we last tried this" has no answer in the current shape.

`consecutive_failures` counts attempts that landed `outcome = 'failed'` since the last success. It
is reset to 0 by any attempt that carries a non-NULL `refreshed_at`, incremented only by 'failed',
and left ALONE by every other outcome -- `skipped_missing` in particular, which costs one
`to_regclass` lookup and no REFRESH, so backing it off would buy nothing and would make a database
that simply has not had `drizzle/0029` applied yet look like a failing one.

The counter is deliberately NOT a "give up" flag. `jobs/matview_refresh.py::_backoff_seconds` caps
the wait at each spec's own `max_staleness_seconds` -- the interval that spec already declares as
"this view must be re-attempted at least this often" -- so a view that someone actually fixes
becomes eligible again on its own, with no operator action and no second switch to remember. What
the backoff suppresses is the WORK, never the SIGNAL: a view carrying `consecutive_failures > 0`
still fails its tick's report, so the lane still dead-letters and the pulse's dead-lettered
work-item census (`execution/jobs_pulse_command.py`) still reds the hourly cron exactly as it does
today.

ADDITIVE ONLY. Two nullable-or-defaulted columns and one CHECK on an eleven-row table; no rewrite,
no lock of consequence, nothing dropped. Existing rows take `consecutive_failures = 0` and
`last_attempt_at = NULL`, which the gate reads as "no standing failure on record yet" -- the next
attempt of an already-broken view records the first failure and the backoff starts from there.
"""

from collections.abc import Sequence

from alembic import op

revision = "20260817_0025"
down_revision = "20260816_0024"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# `IF NOT EXISTS` on the columns and a guarded ADD CONSTRAINT: this revision is expected to be
# applied by hand to production ahead of the deploy that carries it (RUNBOOK section 5), and a
# re-run must be a no-op rather than a 42701/42710.
_ADD_BACKOFF_COLUMNS = """
ALTER TABLE agri.matview_refresh_state
    ADD COLUMN IF NOT EXISTS last_attempt_at timestamp with time zone,
    ADD COLUMN IF NOT EXISTS consecutive_failures integer DEFAULT 0 NOT NULL
"""

# NOT VALID is not used and is not wanted. The relation holds one row per matview this codebase
# knows how to refresh -- eleven on production today -- so validating it is a scan of eleven rows,
# and a constraint that is merely NOT VALID would leave the declarative tree unable to render it
# the way pg_dump renders every other CHECK in this table.
_ADD_NONNEGATIVE_CHECK = """
DO $$ BEGIN
  ALTER TABLE agri.matview_refresh_state
      ADD CONSTRAINT ck_matview_refresh_state_nonnegative_consecutive_failures
      CHECK (consecutive_failures >= 0);
EXCEPTION
  WHEN duplicate_object THEN NULL;
END $$
"""


def upgrade() -> None:
    op.execute(_ADD_BACKOFF_COLUMNS)
    op.execute(_ADD_NONNEGATIVE_CHECK)


def downgrade() -> None:
    raise NotImplementedError(
        "Dropping consecutive_failures/last_attempt_at would restore the livelock this revision "
        "exists to end: a matview that can never refresh would again be indistinguishable from one "
        "that has never been attempted, and would again burn its full statement timeout on every "
        "hourly tick forever. Restore a verified backup rather than reversing this."
    )
