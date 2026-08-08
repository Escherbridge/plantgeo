-- reconcile_lane_run_windows
-- Purpose: every planned window of one backfill lane run, with the run it belongs to, its status and
--          its payload. Reconciliation reads this to learn what the lane thinks it still owes.
-- Loaded by: agri_data_service.ingest.reconcile
-- Params: logical_run_key (text) -- the run to read, identified by the stable key that embeds the
--         lane and its floor day,
--         row_limit (int) -- a hard cap on returned rows; the Python side raises rather than
--         reconciling a partial window list, because an unseen window would never be settled.
--
-- NAMING NOTE: this file is named after its Python constant `_LANE_RUN_WINDOWS`, per the binding rule
-- in sql/AGENTS.md, while the marker on the first line keeps its `reconcile_` prefix because
-- tests/test_ingest_reconcile.py hard-codes the marker strings. The two are allowed to differ; the
-- marker is a wire protocol, the filename is a naming convention.
--
-- The first line above is a dispatch marker, not documentation. The unit tests answer
-- AsyncSession.execute from a recording stub and route each statement by the first single-word comment
-- line in it, so that line must stay first and stay spelled exactly as it is. No other comment line in
-- this file may be a bare `--` followed by one word, or it would become a rival marker. See "Marker
-- protocol" in sql/AGENTS.md.
--
-- Parameter names appear above WITHOUT a leading colon. See "Header/bind-param trap" in
-- sql/AGENTS.md: SQLAlchemy scans comments for colon-prefixed words too and would mint a bind
-- parameter nobody supplies.
--
-- What this returns: one row per work item, ordered by shard key, carrying the run's internal id, the
-- shard key, the item's current status, and the item's JSON payload. The run id comes back on every
-- row because the caller needs it to address the follow-up write, and reading it here saves a second
-- round trip.
--
-- WHY A RUN KEY AND NOT A LANE NAME: `archive_lane_run_key` embeds the lane's floor day in the run
-- key, so lowering a lane's floor mints a SECOND run rather than reopening a finished one. Two runs of
-- one lane hold two overlapping window grids with different shard keys, and settling them together
-- would double-count calendar days. One run key means one grid.
--
-- How this query works, clause by clause:
--
--   FROM agri.job_run AS runs JOIN agri.job_work_item AS items ON items.job_run_id = runs.id
--     A join stitches two tables together on a matching condition -- here each work item is paired
--     with the run it belongs to. A plain (inner) JOIN keeps only pairs that match, which is what is
--     wanted: a run that was recorded but never fanned out into windows has nothing to reconcile.
--
--   WHERE runs.logical_run_key = logical_run_key
--     One run. The filter is on the RUN's stable key rather than on its internal id, so the caller
--     never has to look the run up first. Note the parameter is named here without the leading colon
--     the statement below writes it with -- a comment quoting SQL is still a comment that text()
--     scans, and a colon-prefixed word in one mints a bind parameter.
--
--   items.payload
--     The work item's own JSON document. Reconciliation reads the window bounds back out of it rather
--     than re-deriving them from the shard key, so a change to how windows are laid out cannot make
--     the reconciler disagree with the planner about what a window covers.
--
--   ORDER BY items.shard_key
--     Shard keys embed zero-padded dates, so lexicographic order IS chronological order. The order
--     also makes the LIMIT below deterministic rather than an arbitrary sample.
--
--   LIMIT row_limit
--     The cap. Reaching it is treated as an error by the caller rather than as an answer: a window
--     that never came back could never be settled, and the lane would silently re-walk it forever.
SELECT runs.id           AS job_run_id,
       items.shard_key   AS shard_key,
       items.status      AS status,
       items.payload     AS payload
FROM agri.job_run AS runs
JOIN agri.job_work_item AS items
  ON items.job_run_id = runs.id
WHERE runs.logical_run_key = :logical_run_key
ORDER BY items.shard_key
LIMIT :row_limit
