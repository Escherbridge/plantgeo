"""Add the matview refresh ledger, three index-driven job reads, and a concurrent forecast refresh.

Revision ID: 20260816_0024
Revises: 20260814_0023

The `agri`-side half of the pre-aggregation layer. The `geo`-side half -- nine matviews, their
unique indexes and the union view -- is `drizzle/0029_pre_aggregation_layer.sql`; the two are
applied in the same maintenance window, drizzle first (see
`docs/pending-migrations/0029-pre-aggregation.md`).

Three unrelated-looking changes that are one change:

1. `agri.matview_refresh_state` -- the ledger the new `matview-refresh` lane reads at the top of
   every pulse tick and writes at the bottom. It is what makes the WATERMARK GATE possible, and the
   watermark gate is the single biggest affordability lever in the whole design. Without it, the
   nine new matviews plus the five adopted ones are fourteen scheduled full rebuilds forever;
   with it, a view whose source has not moved is skipped, and `geo.mv_signal_cell_daily` -- the only
   expensive refresh in the set -- refreshes approximately never in the current steady state
   (measured 2026-08-15: the signal plane is 9-13 days stale and the box is idle >90% of
   wall-clock) and exactly once when a lane actually lands data.

   It also answers the hazard that a rollup faithfully materialises a stalled lane: a reader who
   sees an empty day and does not check `refreshed_at` alongside it will mistake staleness for
   absence. That timestamp lives here so the ops route can surface it.

2. Three indexes on the job plane. `routers/jobs.ts` needs no matview at all -- `getLanes`'
   `DISTINCT ON` over the whole of `job_run`, `getRunHistory`'s unindexed sort and
   `getExhaustedGapWindows`' unindexable jsonb predicate become index-driven exactly as written.
   Fewer relations wins: an index costs one write amplification and no refresh schedule, where a
   matview costs a relation, a unique index, a watermark and a place in the pulse budget.

3. `agri.refresh_forecast_ml_daily_serving()` switched from a plain `REFRESH` (which takes ACCESS
   EXCLUSIVE and blocks every read of the view for its duration) to `REFRESH ... CONCURRENTLY`,
   with a self-heal for the unpopulated case. `agri.mv_forecast_ml_daily_serving` sits in
   production TODAY with `relispopulated = false` -- created, indexed, never once refreshed. That
   is the exact bug the "never leave a matview unscheduled" rule exists to prevent, and this
   revision plus the lane's registration is what makes it un-forgettable rather than merely fixed.

`agri.signal_observation` is NOT converted to a hypertable here, and no compression or retention
policy is added. Measured 2026-08-15: `timescaledb_information.hypertables` holds exactly one row
(`tracking.positions`, 0 chunks, 40 kB), so continuous aggregates are unavailable for every
relation in scope; and `create_hypertable(migrate_data => true)` over 26 GB in one transaction
against `maintenance_work_mem = 128 MB` is strictly heavier than the refresh it would replace, and
illegal anyway while `pk_signal_observation PRIMARY KEY (id)` carries inbound foreign keys. The
preconditions a later track would need are recorded in
`docs/pending-migrations/0029-pre-aggregation.md` so they do not have to be re-derived.
"""

from collections.abc import Sequence

from agri_data_service.db.sql_objects import load_object_sql
from alembic import op

revision = "20260816_0024"
down_revision = "20260814_0023"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# One row per matview this codebase knows how to refresh -- never one row per refresh attempt. At
# most a few dozen rows, ever, which is why it carries no index beyond its primary key: an ordering
# index over fourteen rows is a write cost with no read benefit, and the lane sorts them in Python
# after reading all of them in one statement.
#
# `outcome` deliberately carries NO CHECK constraint. Its vocabulary is the `MatviewRefreshOutcome`
# literal in `jobs/matview_refresh.py`, and it will grow -- `refreshed_concurrently`,
# `refreshed_unpopulated`, `skipped_unchanged`, `skipped_missing`, `deferred_budget`, `failed`. A
# CHECK here would turn adding one literal into a production migration, which is the wrong coupling:
# this column is an observability record, not a state machine anything branches on.
#
# `view_name` is the SCHEMA-QUALIFIED name, and the CHECK enforces it. Both schemas are represented
# (`geo.mv_*` and `agri.mv_forecast_ml_daily_serving`), and a bare name would silently open a second
# row for a view that already has one -- the primary key cannot catch that, so the shape is checked
# instead.
#
# `refreshed_at`, `duration_ms` and `row_count` are nullable, and NULL means something specific:
# "recorded before any refresh of this view actually succeeded". The lane orders on
# `refreshed_at ASC NULLS FIRST` precisely so that state sorts first rather than being divided by.
#
# ruff: noqa: E501 -- the DDL below is verbatim pg_dump output. Its CHECK constraint lines run past
# 120 characters and rewrapping them would make this file stop matching the declarative tree that
# tests/test_declarative_schema_parity.py compares against, which is the whole point of quoting it.
_MATVIEW_REFRESH_STATE = r"""
CREATE TABLE agri.matview_refresh_state (
    view_name character varying(200) NOT NULL,
    source_watermark text NOT NULL,
    refreshed_at timestamp with time zone,
    duration_ms integer,
    row_count bigint,
    outcome character varying(64) NOT NULL,
    CONSTRAINT ck_matview_refresh_state_nonnegative_duration CHECK (((duration_ms IS NULL) OR (duration_ms >= 0))),
    CONSTRAINT ck_matview_refresh_state_nonnegative_row_count CHECK (((row_count IS NULL) OR (row_count >= 0))),
    CONSTRAINT ck_matview_refresh_state_schema_qualified_view CHECK (((view_name)::text ~ '^[a-z_][a-z0-9_]*\.[a-z_][a-z0-9_]*$'))
);

ALTER TABLE ONLY agri.matview_refresh_state
    ADD CONSTRAINT pk_matview_refresh_state PRIMARY KEY (view_name);
"""


# `getLanes` opens with `DISTINCT ON (job_definition_id) ... ORDER BY job_definition_id,
# created_at DESC`, which without this index is a sort of every row `agri.job_run` has ever held.
# With it the DISTINCT ON becomes an index skip-scan: one descent per definition, and there are 3.
_IX_JOB_RUN_DEFINITION_CREATED = """
CREATE INDEX ix_job_run_definition_created
    ON agri.job_run USING btree (job_definition_id, created_at DESC)
"""

# `getRunHistory` sorts the whole table by `created_at DESC` and takes a page. The existing
# `ix_job_run_status_scheduled` leads with `status` and cannot serve it.
_IX_JOB_RUN_CREATED_AT = """
CREATE INDEX ix_job_run_created_at
    ON agri.job_run USING btree (created_at DESC)
"""

# `getExhaustedGapWindows` filters on a jsonb key that no index could reach, so it scanned every
# work item -- 454 of which have sat abandoned since 2026-08-08 03:41:44 with 499 attempts already
# burned. A PARTIAL index on the IMMUTABLE `payload -> key` expression removes the unindexable
# predicate without adding a relation: the index holds only the rows that carry the key at all,
# which is a small fraction of the table, so it is cheap to build and cheap to maintain.
_IX_JOB_WORK_ITEM_REOPENED_GAPS = """
CREATE INDEX ix_job_work_item_reopened_gaps
    ON agri.job_work_item USING btree ((payload -> 'reopened_from_observed_gaps'))
    WHERE ((payload -> 'reopened_from_observed_gaps') IS NOT NULL)
"""


def upgrade() -> None:
    op.execute(_MATVIEW_REFRESH_STATE)
    op.execute(_IX_JOB_RUN_DEFINITION_CREATED)
    op.execute(_IX_JOB_RUN_CREATED_AT)
    op.execute(_IX_JOB_WORK_ITEM_REOPENED_GAPS)

    # Plain CREATE INDEX, not CONCURRENTLY, and that is correct here rather than a compromise:
    # alembic runs a revision inside a transaction, `agri.job_run` holds 3 rows and
    # `agri.job_work_item` a few hundred, so the SHARE lock these take lasts milliseconds. The
    # CONCURRENTLY discipline in `scripts/apply-pre-aggregation.mjs` exists for `geo.features`,
    # which is 4.97M rows -- it is a size rule, not a house rule.

    # Body-only change: same signature, same SECURITY DEFINER posture, same pinned search_path
    # (the 20260802_0015 lockdown owns that and this revision must not relax it), so OR REPLACE is
    # legal and no dependent ever sees the function missing.
    op.execute(
        load_object_sql("functions/refresh_forecast_ml_daily_serving.sql", or_replace=True)
    )


def downgrade() -> None:
    raise NotImplementedError(
        "Dropping agri.matview_refresh_state would delete the only record of which matviews have "
        "ever been refreshed, on a database where an unrefreshed matview is indistinguishable from "
        "an empty upstream -- the exact confusion mv_forecast_ml_daily_serving has been causing. "
        "Restore a verified backup into a fresh database rather than reversing this."
    )
