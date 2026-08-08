-- job_lane_state
-- Purpose: one row per (backfill lane, run, work-item status) holding how many windows sit in that
--          status and the oldest and newest window keys among them. This is how the validation report
--          learns what each lane still owes without reading any cursor file.
-- Loaded by: agri_data_service.ingest.validation
-- Params: row_limit (int) -- a hard cap on returned rows; the Python side refuses a result that
--         reaches the cap rather than reasoning about a truncated lane picture.
--
-- The first line above is a dispatch marker the unit tests match statements on. It stays first and
-- stays spelled as it is -- see "Marker protocol" in sql/AGENTS.md.
--
-- Parameter names appear above WITHOUT a leading colon. See "Header/bind-param trap" in
-- sql/AGENTS.md: SQLAlchemy scans comments for colon-prefixed words too and would mint a bind
-- parameter nobody supplies.
--
-- What this returns: one row per lane per run per status, in that order. A lane with windows in four
-- different statuses yields four rows for that run.
--
-- WHAT A SHARD KEY IS, AND WHY min/max OVER IT MEANS SOMETHING: `shard_key` is half of the
-- (job_run_id, shard_key) idempotency key, and it embeds the calendar window the work item covers. It
-- sorts lexicographically, and because the embedded dates are zero-padded, lexicographic order IS
-- chronological order. So the smallest key in a group is the oldest window and the largest is the
-- newest. See jobs/AGENTS.md.
--
-- WHY THIS IS GROUPED PER RUN AND NOT PER LANE DEFINITION: `archive_lane_run_key` puts the lane's
-- FLOOR into the `logical_run_key`, precisely so that lowering a lane's floor mints a SECOND run
-- rather than reopening a finished one. A run's window grid is anchored at its own floor, so two runs
-- of one lane hold two overlapping window sets with different shard keys. Folding them together would
-- count the same calendar day twice and report a lane as owing roughly double what it owes. The
-- alternative -- keeping only the newest run -- was rejected because it discards the superseded run's
-- dead letters, and losing a dead letter is the exact failure class this whole ledger exists to make
-- impossible. Per run, every dead letter stays visible and every total belongs to its own run.
--
-- How this query works, clause by clause:
--
--   FROM agri.job_definition AS definitions
--     The lane itself: one row per named backfill lane. It carries the readable lane name.
--
--   JOIN agri.job_run AS runs ON runs.job_definition_id = definitions.id
--     A run is one logical campaign of that lane -- see the per-run note above for why there can be
--     more than one. A plain (inner) JOIN keeps only lanes that have at least one run; a lane that was
--     defined but never planned has no windows to report, so it correctly does not appear.
--
--   JOIN agri.job_work_item AS work_items ON work_items.job_run_id = runs.id
--     A work item is one window of that run -- one unit of fetching to walk. Again an inner join: a
--     run that was recorded but never fanned out into windows contributes nothing here.
--
--   GROUP BY definitions.name, runs.logical_run_key, work_items.status
--     GROUP BY collapses many rows into one row per distinct combination of the listed expressions.
--     Once rows are collapsed there is no single "the work item" left to select, so every other
--     selected column must be an aggregate summarising the group -- there is no one shard key for a
--     group of 1,882 windows, only the smallest and the largest.
--
--   count(*) AS window_count
--     How many windows are in this status. `*` here does not mean "all columns"; it means "count rows,
--     including rows whose columns are NULL".
--
--   min(work_items.shard_key) / max(work_items.shard_key)
--     The oldest and newest window in the group, by the lexicographic-equals-chronological property
--     described above. Read against the `queued` or `retry_wait` status these two are the frontier of
--     what the lane still owes; read against `dead_letter` they bound the span that failed.
--
--   ORDER BY definitions.name, runs.logical_run_key, work_items.status
--     A stable, total order so the report does not shuffle between runs, and so the LIMIT below is
--     deterministic rather than an arbitrary sample.
--
--   LIMIT row_limit
--     The cap. It exists so an unexpectedly large ledger cannot pull an unbounded result into memory;
--     the Python side treats hitting it as an error, not as an answer.
SELECT definitions.name       AS lane,
       runs.logical_run_key   AS run_key,
       work_items.status      AS work_item_status,
       count(*)               AS window_count,
       min(work_items.shard_key) AS oldest_shard_key,
       max(work_items.shard_key) AS newest_shard_key
  FROM agri.job_definition AS definitions
  JOIN agri.job_run AS runs
    ON runs.job_definition_id = definitions.id
  JOIN agri.job_work_item AS work_items
    ON work_items.job_run_id = runs.id
 GROUP BY definitions.name, runs.logical_run_key, work_items.status
 ORDER BY definitions.name, runs.logical_run_key, work_items.status
 LIMIT :row_limit
