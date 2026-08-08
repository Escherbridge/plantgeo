-- Purpose: one page of features that have a shape but were never linked into the geometry dimension.
--          The geometry repair pass walks the whole table through this statement, page by page, and
--          mints the missing version rows for each one it finds.
-- Loaded by: agri_data_service.ingest.backfill
-- Params: cursor (text holding a UUID) -- the last feature id of the previous page; the first call
--         passes the all-zeroes UUID so that every row sorts after it,
--         batch_size (int) -- how many rows this page may return.
--
-- Parameter names appear above WITHOUT a leading colon. See "Header/bind-param trap" in
-- sql/AGENTS.md: SQLAlchemy scans comments for colon-prefixed words too and would mint a bind
-- parameter nobody supplies.
--
-- What this returns: up to batch_size rows, in ascending feature-id order, each carrying the feature's
-- id, its layer's id and readable name, and its whole JSON payload rendered as text.
--
-- WHY THE PAGING IS A KEYSET ON feature.id AND NOT AN OFFSET: this statement is run repeatedly against
-- a table the repair pass is itself writing to. OFFSET paging re-counts from the start of the result
-- on every page, so rows that stop qualifying between pages shift everything after them backwards and
-- the walk skips whatever slid across the boundary. Carrying the last id forward instead -- "give me
-- the next rows after this one" -- is stable under concurrent change: a row either sorts after the
-- cursor or it does not, and nothing can move it.
--
-- WHY THE CURSOR IS THE ID AND NOT A WRITE CLOCK: the row's own timestamp columns record when the
-- warehouse last touched the row, not when the observation happened, and the repair pass rewrites rows
-- as it goes. Paging on a column the pass itself moves would let a repaired row jump back in front of
-- the cursor and be walked twice, or forward past it and never be walked at all. The id is immutable,
-- so it cannot do either. tests/test_ingest_backfill.py asserts this statement's text mentions no such
-- column, which is why none is named here.
--
-- How this query works, clause by clause:
--
--   FROM geo.features AS feature JOIN geo.layers AS layer ON layer.id = feature.layer_id
--     A join stitches two tables together on a matching condition -- here each feature is paired with
--     the layer it belongs to, so the layer's readable name comes back beside it. The repair pass
--     needs that name to choose which producer's identity rule to re-apply. A plain (inner) JOIN keeps
--     only pairs that match, which is right: a feature with no layer is not a thing this schema holds.
--
--   WHERE feature.geometry_id IS NULL
--     The orphans -- features never linked to a geometry version. `IS NULL` rather than `= NULL`
--     because comparing anything to NULL in SQL yields NULL, neither true nor false, so `IS` is the
--     only test that works. This is the whole definition of "needs repair".
--
--   AND feature.geom IS NOT NULL
--     And only those that actually have a shape to link. A feature with neither a link nor a shape has
--     nothing for the pass to work from; it is a different fault, reported elsewhere.
--
--   AND feature.id > CAST(cursor AS uuid)
--     The keyset. Note the parameter is named here without the leading colon the statement below
--     writes it with -- a comment quoting SQL is still a comment that text() scans for bind
--     parameters. `>` against the previous page's last id yields the rows that come after it. The CAST
--     exists purely to pin the bound parameter's type: the parameter arrives as text, `feature.id` is
--     a `uuid` column, and PostgreSQL will not compare the two without being told which type to
--     resolve the parameter to. `CAST(x AS uuid)` is the long spelling of `x::uuid`; the value is
--     unchanged, only its declared type is fixed.
--
--   feature.properties::text
--     The payload handed back as text rather than as JSON. `::type` is PostgreSQL's short spelling of
--     CAST(value AS type). The Python side parses it itself, so it sees exactly the bytes the database
--     holds rather than a driver's reconstruction of them.
--
--   ORDER BY feature.id
--     Ascending id order, which is the same order the keyset above steps through. The two must agree:
--     a keyset cursor is only meaningful against the ordering it was taken from, and without an ORDER
--     BY the database is free to return rows in any order it likes.
--
--   LIMIT batch_size
--     The page size, so one call reads a bounded amount however large the orphan set is.
SELECT feature.id AS feature_id,
       feature.layer_id AS layer_id,
       layer.name AS layer_name,
       feature.properties::text AS properties_json
FROM geo.features AS feature
JOIN geo.layers AS layer ON layer.id = feature.layer_id
WHERE feature.geometry_id IS NULL
  AND feature.geom IS NOT NULL
  AND feature.id > CAST(:cursor AS uuid)
ORDER BY feature.id
LIMIT :batch_size
