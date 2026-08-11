-- coverage_absence_windows
-- Purpose: read back the day spans one lane's provider was asked for and answered nothing to, so a
--          gap filler stops re-walking days that upstream genuinely never published.
-- Loaded by: agri_data_service.execution.coverage_census
-- Params: source_key (text) -- the agri.data_source.key that gates the lane; support_key (text) --
--         the spatial support that discriminates lanes sharing a signal name; from_day
--         (timestamptz) -- midnight UTC of the first contracted day; through_exclusive
--         (timestamptz) -- midnight UTC of the day AFTER the last contracted day.
--
-- Parameter names appear above WITHOUT a leading colon. See "Header/bind-param trap" in
-- sql/AGENTS.md: SQLAlchemy scans comments for colon-prefixed words too, and a colon-prefixed word
-- inside a comment would mint a bind parameter that no caller supplies.
--
-- What this returns: one row per distinct (signal, window start day, window end day) that any cell
-- recorded as no_data, carrying HOW MANY distinct cells recorded it. The caller expands each window
-- into its days and only treats a day as excused once that count reaches the same cell floor an
-- observation would have to clear.
--
-- The count is the whole point of this query's shape, and it was measured 2026-08-11: production
-- holds 1,965 no_data rows, and 1,568 of them are 98 permanently out-of-domain ERA5-Land cells
-- writing four-year spans for 16 signals. Reading "any cell said no_data" as "the lane is excused"
-- would have marked 1,556 of that lane's 1,560 contracted days satisfied while the provider was in
-- fact publishing 1,470 cells on every one of them -- a lane reporting success having written
-- nothing, one branch over. An absence is evidence about the cells that recorded it, and it
-- generalises to the lane only when it covers the lane.
--
-- status = 'no_data' only, on purpose. The table's four statuses are not interchangeable:
--   complete -- the window landed in full; the observation plane already proves that.
--   partial  -- some days landed. Partial is not complete and is still refillable, so it must NOT
--               be read as an absence; doing so is how a settler writes a silent hole back in.
--   failed   -- the fetch broke. A broken fetch is not evidence about the provider, and treating
--               it as one would let one outage retire a real gap forever.
--   no_data  -- we asked and the provider published nothing. The only status that is evidence.
--
-- How this query works, clause by clause:
--
--   join source_release, then data_source
--     The audit row points at the RELEASE whose fetch produced the evidence, and the release
--     points at the source. Two joins are the only path from an audit row to a lane key, and it is
--     the same path the observation census takes, so both halves agree on what "this lane" means.
--
--   window_end >= from_day and window_start < through_exclusive
--     Overlap, not containment. An audit window recorded across a boundary -- one plan covering
--     four calendar years, of which the contract only requires the last two -- still explains the
--     days inside the contracted span, and a containment test would discard it and re-walk them.
--
--   group by the three projected columns, counting distinct cell ids
--     Collapses one row per cell into one row per span, and keeps the number of cells that span
--     speaks for. A 397-cell lattice that genuinely published nothing writes 397 identical spans
--     per signal and this reports 397; a single out-of-domain cell writes one and this reports 1.
--     Distinct cell ids and not rows, because one cell re-probed under a second release lineage
--     has two audit rows for the same span and is still one cell of evidence.
select
    audit.signal_name as signal_name,
    (audit.window_start at time zone 'UTC')::date as window_start_day,
    (audit.window_end at time zone 'UTC')::date as window_end_day,
    count(distinct audit.cell_id) as absent_cell_count
from agri.signal_coverage_audit as audit
join agri.source_release as release on release.id = audit.source_release_id
join agri.data_source as source on source.id = release.data_source_id
where source.key = :source_key
  and audit.support_key = :support_key
  and audit.status = 'no_data'
  and release.validation_state <> 'retracted'
  and audit.window_end >= :from_day
  and audit.window_start < :through_exclusive
group by
    audit.signal_name,
    (audit.window_start at time zone 'UTC')::date,
    (audit.window_end at time zone 'UTC')::date
