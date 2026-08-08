-- reconcile_observed_layer_days
-- Purpose: every distinct UTC day one map layer already serves rows for. Reconciliation compares a
--          lane's planned windows against this set to decide which windows already landed.
-- Loaded by: agri_data_service.ingest.reconcile
-- Params: layer_id (text holding a UUID) -- the layer to measure,
--         published_status (text) -- the one `geo.features.status` value the map serves,
--         row_limit (int) -- a hard cap on returned rows; the Python side raises rather than
--         reconciling against a truncated day set, because a missing day would be read as "not
--         landed" and would re-walk history that is already present.
--
-- NAMING NOTE: this file is named after its Python constant `_OBSERVED_LAYER_DAYS`, per the binding
-- rule in sql/AGENTS.md, while the marker on the first line keeps its `reconcile_` prefix because
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
-- What this returns: one row per day, one column, in ascending day order. It is read into a Python set
-- -- the row order matters only in that it makes the cap below deterministic.
--
-- WHY THE COVERAGE IS READ FROM THE DATA AND NEVER FROM A CURSOR FILE: a cursor recorded where a walk
-- had REACHED, not what had LANDED. On the first full pass those differed by 169 of 298 windows. A day
-- is covered here because rows for it exist, or it is not covered; there is no third answer and no
-- file to be wrong. The module docstring in ingest/reconcile.py tells the whole story.
--
-- How this query works, clause by clause:
--
--   geo.feature_observation_day(features.properties)
--     A database function this service owns. It digs the publisher's own observation timestamp out of
--     the feature's JSON payload and reduces it to a UTC calendar day, answering NULL when the payload
--     carries no parseable one. Calling it rather than reading a column is the point: reconciliation
--     and the map's day axis then apply exactly the same rule, so "covered" here means "visible
--     there".
--
--   WHERE features.layer_id = CAST(layer_id AS uuid)
--     One layer only, and note the parameter is named here without the leading colon the statement
--     below writes it with -- a comment quoting SQL is still a comment that text() scans.
--     The CAST exists purely to pin the bound parameter's type: the parameter arrives
--     as text, `layer_id` is a `uuid` column, and PostgreSQL will not compare the two without being
--     told which type to resolve the parameter to. `CAST(x AS uuid)` is the long spelling of `x::uuid`
--     and does not change the value.
--
--   AND features.status = published_status
--     Only rows the map actually serves. A draft row is a real row but nobody can see it, so counting
--     its day as covered would settle a window whose data is invisible.
--
--   AND features.geometry_id IS NOT NULL
--     Only rows linked into the geometry dimension -- an unlinked row has no shape the time-aware
--     serving path can place. `IS NOT NULL` rather than `<> NULL` because comparing anything to NULL
--     in SQL yields NULL, neither true nor false, so `IS` is the only test that works.
--
--   AND geo.feature_observation_day(features.properties) IS NOT NULL
--     Only rows whose day could be parsed. An undated row cannot vouch for any window.
--
--   GROUP BY geo.feature_observation_day(features.properties)
--     GROUP BY collapses many rows into one row per distinct value of the listed expression -- here,
--     one row per day, however many features that day holds. Nothing is aggregated alongside it
--     because nothing else is wanted: the question is "which days", not "how many rows". The whole
--     expression is repeated rather than the output name reused, because standard SQL evaluates GROUP
--     BY before the SELECT list has produced its names.
--
--   ORDER BY 1
--     Ordering by output POSITION -- column 1 is `observed_day`. It makes the LIMIT below deterministic
--     rather than an arbitrary sample.
--
--   LIMIT row_limit
--     The cap. Reaching it is treated as an error by the caller rather than as an answer, because a
--     truncated day set would silently under-report coverage.
SELECT geo.feature_observation_day(features.properties) AS observed_day
FROM geo.features AS features
WHERE features.layer_id = CAST(:layer_id AS uuid)
  AND features.status = :published_status
  AND features.geometry_id IS NOT NULL
  AND geo.feature_observation_day(features.properties) IS NOT NULL
GROUP BY geo.feature_observation_day(features.properties)
ORDER BY 1
LIMIT :row_limit
