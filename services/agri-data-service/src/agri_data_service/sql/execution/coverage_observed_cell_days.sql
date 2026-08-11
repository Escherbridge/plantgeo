-- coverage_observed_cell_days
-- Purpose: count how many distinct grid cells carry an observation on each day of one lane, so
--          `reconcile_signal` can decide which contracted days are covered, thin or missing.
-- Loaded by: agri_data_service.execution.coverage_census
-- Params: source_key (text) -- the agri.data_source.key that gates the lane; grid_name (text) --
--         the agri.spatial_cell.grid_name whose cells the lane is measured over; support_key
--         (text) -- the spatial support that discriminates lanes sharing a signal name; from_day
--         (timestamptz) -- midnight UTC of the first contracted day; through_exclusive
--         (timestamptz) -- midnight UTC of the day AFTER the last contracted day.
--
-- Parameter names appear above WITHOUT a leading colon. See "Header/bind-param trap" in
-- sql/AGENTS.md: SQLAlchemy scans comments for colon-prefixed words too, and a colon-prefixed word
-- inside a comment would mint a bind parameter that no caller supplies.
--
-- What this returns: one row per (signal, day) that has at least one observation, holding the
-- number of distinct cells that landed. A day with nothing at all produces NO row, and the caller
-- reads that absence of a row as a count of zero -- which is the honest shape, because emitting a
-- zero row would require this query to know the full calendar the contract demands, and that
-- calendar is the contract's business, not the warehouse's.
--
-- Every signal belonging to the source is returned, not just one contract's list. Two contracts
-- can share a source key -- the NASA POWER meteorology set and its soil-wetness set do exactly
-- that -- and returning the union lets one query serve both, with the caller slicing out the
-- signals each contract names. Filtering here instead would need a list-valued bind parameter,
-- which is the one shape this SQL tree deliberately avoids.
--
-- How this query works, clause by clause:
--
--   join source_release, then data_source
--     agri.signal_observation does not name its source directly; it points at the RELEASE that
--     carried it, and the release points at the source. Two joins are therefore the only way to
--     ask "which lane produced this row". This is also the gate every governed view already uses,
--     so a census built on it counts exactly the rows a serving reader would.
--
--   join spatial_cell
--     Scopes the count to one lattice. Without it a signal published on two grids would have its
--     per-day cell counts summed across both, and a day covering half of each lattice would read
--     as fully covered on neither and complete on the total.
--
--   observation.is_observed and observation.normalized_value is not null
--     A row can exist and still be a recorded ABSENCE. The Open-Meteo lane writes an explicit
--     is_observed = false, normalized_value = NULL row for each day a partly-published series was
--     missing, precisely so the hole is legible. Counting those as coverage would turn every one
--     of them back into a silent hole -- the exact failure the rows were written to prevent.
--
--   count(distinct observation.cell_id)
--     Cells, not rows. A lane that re-published one cell under a second release lineage has two
--     rows for that cell on that day, and counting rows would report 794 cells on a 397-cell
--     lattice. Distinct cell ids is the only count that maps onto the contract's cell fraction.
--
--   observed_at >= from_day and observed_at < through_exclusive
--     A half-open range on the raw timestamp column, so the b-tree index on
--     (source_release_id, observed_at) is usable. Casting observed_at to a date inside the
--     predicate would work but would force a scan; the cast is done in the SELECT list instead,
--     where it costs one conversion per returned group rather than one per stored row.
--
--   (observed_at at time zone 'UTC')::date
--     The lane stores each day as that day at 00:00Z. Reducing in UTC reproduces the publisher's
--     own day boundary; reducing in the server's local zone would shift every observation into
--     the neighbouring day for any deployment not running on UTC.
select
    observation.signal_name as signal_name,
    (observation.observed_at at time zone 'UTC')::date as observed_day,
    count(distinct observation.cell_id) as cell_count
from agri.signal_observation as observation
join agri.source_release as release on release.id = observation.source_release_id
join agri.data_source as source on source.id = release.data_source_id
join agri.spatial_cell as cell on cell.id = observation.cell_id
where source.key = :source_key
  and cell.grid_name = :grid_name
  and observation.support_key = :support_key
  and observation.is_observed
  and observation.normalized_value is not null
  and release.validation_state <> 'retracted'
  and observation.observed_at >= :from_day
  and observation.observed_at < :through_exclusive
group by observation.signal_name, (observation.observed_at at time zone 'UTC')::date
