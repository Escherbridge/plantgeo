-- Purpose: list the registered NDVI series with their pinned contract snapshots, geometry links and
--          licence facts, optionally narrowed to a chosen set of cells.
-- Loaded by: agri_data_service.execution.vegetation_ndvi_plane
-- Params: data_source_key (text) -- the data source whose series to list; metric_name (text) -- which
--         metric's series to list; cell_keys (text[], nullable) -- the bare entity keys to restrict
--         to, or empty to mean "every cell".
--
-- Parameter names appear above WITHOUT a leading colon. See "Header/bind-param trap" in
-- sql/AGENTS.md: SQLAlchemy scans comments for colon-prefixed words too, and a colon-prefixed word
-- inside a comment would mint a bind parameter that no caller supplies.
--
-- What this returns: one row per matching series -- its id and key, the entity and spatial cell it
-- describes, its contract checksum, the contract snapshot rendered as text, and the governance facts
-- the caller re-checks in Python: the source's review state, its licence name and URL, and its
-- citation. Those four are selected precisely so the caller can refuse a series that is not behind an
-- approved, licensed, citable source, and name the offending series when it does.
--
-- What the contract is: a frozen description of what a series promises -- its metric, units, support
-- and lineage -- together with a checksum over that description. Every forecast records the contract
-- checksum it was produced under, so a later change to the series definition is visible as a
-- mismatch rather than silently reinterpreting old forecasts.
--
-- How this query works, clause by clause:
--
--   FROM agri.v_forecast_timeseries_contract AS contract
--     A view, not a table: a stored query that presents the joined-up contract for each series as if
--     it were one wide table. Reading the view rather than re-joining its parts here means this
--     statement and every other consumer see the same definition of a contract, and a change to that
--     definition happens in one place.
--
--   WHERE contract.data_source_key = ... AND contract.metric_name = ...
--     Restricts to the one lane: this data source, this metric. Without both, a series for another
--     metric on the same source would be returned and simulated with NDVI assumptions.
--
--   (CAST(cell_keys AS text[]) IS NULL OR contract.entity_key = ANY(CAST(cell_keys AS text[])))
--     The optional filter, expressed without changing the statement's shape. OR is satisfied if
--     either side holds, so when the parameter is empty the first branch is true and every series
--     passes; when it holds keys, the first branch is false and the second restricts to them. ANY
--     means "equals any element of this array" -- the set-membership form of an equality test. The
--     casts pin the parameter's type: a bare bind parameter carries no type of its own, and an empty
--     one is doubly ambiguous, so the database is told it is an array of text in both branches.
--     Writing the option this way keeps one SQL file for both cases rather than assembling the text
--     conditionally in Python.
--
--   contract.contract_snapshot::text AS contract_snapshot
--     The snapshot is stored as a JSON document; casting it to text hands the caller the exact
--     characters rather than a re-serialised structure. That matters because the snapshot is
--     eventually written back into an iteration row and compared against a checksum -- re-encoding it
--     on the way through could reorder keys or change spacing and invalidate the comparison.
--
--   ORDER BY contract.series_key
--     A stable, total order. The caller iterates these series to write iterations, and a fixed order
--     makes a run reproducible and its logs comparable between runs.
SELECT
    contract.series_id,
    contract.series_key,
    contract.entity_key,
    contract.spatial_cell_id,
    contract.contract_checksum,
    contract.contract_snapshot::text AS contract_snapshot,
    contract.data_source_review_state,
    contract.license_name,
    contract.license_url,
    contract.citation
FROM agri.v_forecast_timeseries_contract AS contract
WHERE contract.data_source_key = :data_source_key
  AND contract.metric_name = :metric_name
  AND (
        CAST(:cell_keys AS text[]) IS NULL
        OR contract.entity_key = ANY(CAST(:cell_keys AS text[]))
  )
ORDER BY contract.series_key
