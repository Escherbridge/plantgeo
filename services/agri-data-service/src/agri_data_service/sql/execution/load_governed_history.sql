-- Purpose: read every registered series' leakage-free NDVI history in one governed pass, as of a
--          pinned release set and a pinned moment.
-- Loaded by: agri_data_service.execution.vegetation_ndvi_plane
-- Params: release_set_id (uuid) -- the governed set the history is pinned to; as_of_time
--         (timestamptz) -- the moment the reader is pretending to stand at; metric_name (text) --
--         which metric's observations to read; cutoff_exclusive (timestamptz) -- the first instant
--         NOT allowed into the history, i.e. midnight after the cutoff day.
--
-- Parameter names appear above WITHOUT a leading colon. See "Header/bind-param trap" in
-- sql/AGENTS.md: SQLAlchemy scans comments for colon-prefixed words too, and a colon-prefixed word
-- inside a comment would mint a bind parameter that no caller supplies.
--
-- What this returns: one row per observation, for every series at once -- the series it belongs to,
-- when it was observed, its value, its checksum, and two licence strings the caller compares against
-- each other. It is deliberately one query for all series rather than one query per series: the
-- caller groups the rows into per-series histories in Python, and doing so in a single pass keeps
-- every series pinned to exactly the same release set and as-of moment.
--
-- What "leakage-free" means here: a forecast trained on data that was not yet available at its own
-- cutoff would score far better in a backtest than it ever could in production. Two independent
-- guards below make that impossible.
--
-- How this query works, clause by clause:
--
--   FROM agri.forecast_timeseries_contract(release_set_id, as_of_time) AS contract
--     A set-returning function, not a table: it is called with arguments and yields rows, and is then
--     read exactly as if it were a table. This is the first leakage guard. The function returns only
--     observations that belong to the named release set AND were already available at the named
--     moment, so knowledge that arrived later simply is not in the result. Putting that rule inside a
--     shipped database function rather than in each caller means every consumer of governed history
--     obeys the same rule, and the rule is versioned with the schema.
--
--   WHERE contract.metric_name = metric_name
--     Restricts to the one metric. A series can be joined to observations of more than one metric
--     through the contract, and mixing them would train an NDVI model on something else.
--
--   AND contract.observed_at < cutoff_exclusive
--     The second leakage guard, on the observation's own timestamp rather than on its availability.
--     Strictly less-than against midnight after the cutoff day is what makes the cutoff day itself
--     fully included while nothing from the following day can slip in. An inclusive bound against a
--     day's end would depend on how precisely that end was written; a strict bound against the next
--     midnight cannot be ambiguous.
--
--   contract.source_release_license_snapshot and contract.license_name
--     Two licence strings from different levels of the lineage: the one frozen onto the source
--     release when it was registered, and the one the contract currently approves. They are selected
--     side by side so the caller can compare them row by row and refuse any observation whose frozen
--     licence has drifted from the approved one -- a licence change is a governance event, not
--     something to discover later.
--
--   ORDER BY contract.series_id, contract.observed_at
--     A stable, total order that also does the caller's work: rows arrive grouped by series and, within
--     a series, in chronological order, so the histories can be assembled in one forward pass without
--     sorting them again in Python.
SELECT
    contract.series_id,
    contract.observed_at,
    contract.metric_value,
    contract.observation_checksum,
    contract.source_release_license_snapshot,
    contract.license_name
FROM agri.forecast_timeseries_contract(:release_set_id, :as_of_time) AS contract
WHERE contract.metric_name = :metric_name
  AND contract.observed_at < :cutoff_exclusive
ORDER BY contract.series_id, contract.observed_at
