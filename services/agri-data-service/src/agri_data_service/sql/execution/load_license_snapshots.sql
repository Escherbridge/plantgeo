-- Purpose: collect, per series, the distinct licence snapshots of every source release that
--          contributed to its governed history, as one JSON document per series.
-- Loaded by: agri_data_service.execution.vegetation_ndvi_plane
-- Params: release_set_id (uuid) -- the governed set the history is pinned to; as_of_time
--         (timestamptz) -- the moment the reader is pretending to stand at; metric_name (text) --
--         which metric's observations to consider; cutoff_exclusive (timestamptz) -- the first
--         instant NOT allowed into the history, i.e. midnight after the cutoff day.
--
-- Parameter names appear above WITHOUT a leading colon. See "Header/bind-param trap" in
-- sql/AGENTS.md: SQLAlchemy scans comments for colon-prefixed words too, and a colon-prefixed word
-- inside a comment would mint a bind parameter that no caller supplies.
--
-- What this returns: one row per series -- its id, and a JSON array of the licence snapshots behind
-- it, each entry naming a source release and the licence frozen onto it. The caller writes that
-- document verbatim onto every forecast iteration, so each forecast carries proof of what it was
-- allowed to be built from. The predicates are deliberately identical to those in
-- load_governed_history.sql: these snapshots must describe exactly the observations that were
-- actually used, no more and no fewer.
--
-- How this query works, clause by clause:
--
--   FROM ( SELECT DISTINCT ... ) AS governed
--     An inline subquery in the FROM clause -- a query used as if it were a table. It exists to run
--     DISTINCT before the aggregation. DISTINCT collapses rows that are identical across all selected
--     columns, so a series with 900 observations from one release contributes one (series, release,
--     licence) row rather than 900. Without this step the aggregate below would repeat the same
--     licence entry once per observation and produce an enormous, useless document.
--
--   agri.forecast_timeseries_contract(release_set_id, as_of_time)
--     A set-returning function, not a table: it is called with arguments and yields rows that are
--     then read as if from a table. It returns only observations belonging to the named release set
--     that were already available at the named moment, which is the leakage guard. Every consumer of
--     governed history goes through this same shipped function, so the rule lives in one place.
--
--   WHERE contract.metric_name = ... AND contract.observed_at < cutoff_exclusive
--     The same two restrictions the history read applies: the one metric, and strictly before
--     midnight after the cutoff day so that the cutoff day is fully included and the next day cannot
--     slip in.
--
--   jsonb_build_object('source_release_id', ..., 'license_snapshot', ...)
--     Builds one JSON object per surviving row from alternating names and values, rather than from a
--     string pasted together in the query -- so the values keep their real types and no escaping
--     mistake can produce malformed JSON.
--
--   jsonb_agg(... ORDER BY governed.source_release_id)
--     An aggregate that collects the per-row objects of a group into a single JSON array. The ORDER
--     BY inside the aggregate is load-bearing, not decoration: without it the database may combine
--     the rows in any order, so the same set of licences could serialise differently between runs.
--     Since this document is stored on an iteration and compared later, an unstable order would make
--     equal evidence look unequal.
--
--   ::text AS snapshots
--     Casts the finished JSON array to text so the caller receives exactly those characters. The
--     value is written back into another row unchanged; handing over a re-serialised structure could
--     reorder keys or change spacing and break a later comparison.
--
--   GROUP BY governed.series_id
--     Collapses the distinct rows into one row per series. That is what makes jsonb_agg an aggregate
--     over a series rather than over the whole table, so each series gets its own array.
SELECT
    governed.series_id,
    jsonb_agg(
        jsonb_build_object(
            'source_release_id', governed.source_release_id,
            'license_snapshot', governed.source_release_license_snapshot
        )
        ORDER BY governed.source_release_id
    )::text AS snapshots
FROM (
    SELECT DISTINCT
        contract.series_id,
        contract.source_release_id,
        contract.source_release_license_snapshot
    FROM agri.forecast_timeseries_contract(:release_set_id, :as_of_time) AS contract
    WHERE contract.metric_name = :metric_name
      AND contract.observed_at < :cutoff_exclusive
) AS governed
GROUP BY governed.series_id
