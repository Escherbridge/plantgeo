-- Purpose: measure how much of one governed release was contributed by ONE registration pass's own
--          cell selection, so a pass whose cells resolved to nothing is refused even when the
--          release it joined is already full of somebody else's rows.
-- Loaded by: agri_data_service.execution.vegetation_ndvi_plane
-- Params: source_release_id (uuid) -- the governed release the pass registered or rejoined;
--         prefixed_cell_keys (text[]) -- one batch of grid-qualified cell keys, the same form
--         insert_forecast_series.sql resolves; grid_name (text) -- the analysis grid, also the
--         cell-key prefix; metric_name (text) -- which metric's series to count;
--         transform_version (text) -- which transform's series to count.
--
-- Parameter names appear above WITHOUT a leading colon. See "Header/bind-param trap" in
-- sql/AGENTS.md: SQLAlchemy scans comments for colon-prefixed words too, and a colon-prefixed word
-- inside a comment would mint a bind parameter that no caller supplies. For the same reason the
-- clause walkthrough below names columns and parameters in prose rather than quoting the statement.
--
-- What this returns: exactly one row, always -- observation_count and series_count, both 0 when the
-- selection contributed nothing. The caller turns a zero into an explicit refusal.
--
-- Why this exists next to release_materialisation.sql, which looks almost the same: that statement
-- asks what the whole release holds, and on a re-run against an already-registered release the
-- answer is "everything the first pass wrote" no matter what this pass did. A pass whose cell keys
-- resolve to no registered cell -- a typo, or keys whose upstream features were re-ingested under a
-- changed cellKey -- therefore writes nothing and still finds a full release. This statement asks
-- the narrower question the refusal actually needs: how much of the release is reachable through
-- THIS pass's cells. Release-wide counts stay, but for reporting, not for the gate.
--
-- How this query works, clause by clause:
--
--   count(*)::bigint AS observation_count
--     How many governed observations in this release hang off the selected cells. The cast pins the
--     width the caller reads rather than leaving it to the driver's default mapping.
--
--   count(DISTINCT observation.series_id)::bigint AS series_count
--     How many of the selected cells actually carry an observation. DISTINCT counts a series once
--     however many days it contributed, so this is a count of covered cells, not of rows -- the
--     number the caller compares against how many cells it asked for.
--
--   INNER JOIN agri.forecast_series ... INNER JOIN agri.spatial_cell ...
--     Walks observation to series to cell, which is the same chain load_observations.sql walks in
--     the opposite direction when it decides what to write. Matching that chain exactly is the
--     point: this statement must count precisely the rows that statement would have produced, so
--     the three matching conditions below repeat its own. INNER on both, so an observation whose
--     series or cell is missing contributes nothing rather than being counted against a cell that
--     does not exist.
--
--   the metric and transform conditions, each wrapped in a cast to varchar
--     A cell can carry several series; matching on the cell alone would count some other metric's
--     observations as though they were this transform's. The casts pin each parameter to the column
--     type it is compared against, so the database compares like with like instead of resolving an
--     untyped parameter by guesswork.
--
--   the grid_name condition
--     Not redundant with the prefixed keys: it is a second, independent guard that a key can only
--     ever resolve to a cell on the grid it names.
--
--   ANY(CAST(prefixed_cell_keys AS text[]))
--     "equals any element of this array" -- the set-membership form of an equality test, which is
--     how one statement covers a whole batch of keys. The cast pins the parameter's type: a bare
--     bind parameter has none of its own and the database will not guess which kind of array it
--     received. The caller batches the selection and sums the results. That sum is only sound
--     because register_governed_plane dedupes the selection before batching, and the reason is
--     worth stating: a repeated key would be double-counted across a batch boundary (two batches,
--     two matches) but NOT within one batch, because this is set membership and matches a value
--     once however many times it appears in the array. So a duplicate would both inflate the
--     summed counts and deflate them relative to the caller's own len(), in the same pass. The
--     gate survives either way -- 0 + 0 is still 0, and no duplicate can manufacture a row -- but
--     the reported per-pass coverage would be wrong, so the dedup is upstream of both.
--
--   (no GROUP BY)
--     A SELECT made entirely of aggregates over a filtered join collapses to exactly one row, even
--     when the filter matches nothing -- which is why the empty case arrives as a row of zeroes
--     rather than as no row at all, and why the caller can read it without a null check.
--
-- Measured cost, not asserted (EXPLAIN ANALYZE on the production release, 184,409 rows over 1,568
-- series, one full 200-key batch): 29.6 ms, 29,556 rows aggregated. The cell keys drive a bitmap
-- index scan on uq_spatial_cell_cell_key, and the selected series then drive a nested loop whose
-- inner side is an INDEX ONLY scan of uq_forecast_observation_source_event -- the uniqueness
-- constraint's own index, whose leading columns are exactly the release and the series this
-- statement filters on, with Heap Fetches: 0. So unlike the release-wide sibling this one really is
-- index-bounded, and for a structural reason: the selection supplies the selectivity the
-- release-wide predicate does not have. Re-measure before trusting it on a much larger release.
SELECT
    count(*)::bigint AS observation_count,
    count(DISTINCT observation.series_id)::bigint AS series_count
FROM agri.forecast_observation AS observation
INNER JOIN agri.forecast_series AS series
    ON series.id = observation.series_id
INNER JOIN agri.spatial_cell AS cell
    ON cell.id = series.spatial_cell_id
WHERE observation.source_release_id = :source_release_id
  AND series.metric_name = CAST(:metric_name AS varchar)
  AND series.source_transform_version = CAST(:transform_version AS varchar)
  AND cell.grid_name = :grid_name
  AND cell.cell_key = ANY(CAST(:prefixed_cell_keys AS text[]))
