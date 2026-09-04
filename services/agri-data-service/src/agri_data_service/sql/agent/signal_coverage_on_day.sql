-- agent_signal_coverage_on_day
-- Purpose: read back what the ingest lanes already recorded about one caller-named day near a
--          point -- complete, partial, no_data or failed -- so an empty value answer can be
--          explained instead of merely reported.
-- Loaded by: agri_data_service.agent.tools
-- Params: cell_ids (text[] -- the spatial-cell ids the value answer came from, resolved from the
--         Parquet signal plane), cell_distances (double precision[] -- each cell's distance from
--         the probe point in metres, positionally paired with cell_ids), day_start/day_end
--         (timestamptz -- the UTC midnight the day opens on and the UTC midnight the next day
--         opens on), signal_names (text[], empty array means "every signal"), row_limit (int)
--
-- Parameter names appear above WITHOUT a leading colon -- see "Header/bind-param trap" in
-- sql/AGENTS.md.
--
-- THE CELLS ARRIVE AS A PARAMETER NOW, AND THAT IS THE WHOLE OF THE 2026-09-04 CHANGE. This
-- statement used to resolve "cells near the point" itself, from agri.spatial_cell. That table is
-- in the retirement track's "drop now" class and is already absent from production, so the CTE
-- that read it could no longer run at all. The cells are instead resolved from the Parquet signal
-- plane -- which carries cell_longitude/cell_latitude beside every row -- by the same call that
-- read the day's values, and handed here as two positionally-paired arrays. That is STRICTER than
-- the join it replaces: the audit is now read over exactly the cells the value answer came from,
-- rather than over every cell the radius admitted whether it contributed or not.
--
-- agri.signal_coverage_audit is the warehouse's existing record of what we asked an upstream for
-- and what it answered. A day the provider genuinely never published carries a no_data row there,
-- written at ingest time; that is evidence, not a hole, and it is the difference between "upstream
-- has nothing for 2024-03-14" and "we do not know why 2024-03-14 is empty". This statement exists
-- so the agent can tell those two apart. Nothing new is recorded anywhere -- this reads the table
-- the lanes already fill.
--
-- WHY THIS ONE STATEMENT STILL READS POSTGRESQL while its siblings moved to Parquet. It is not
-- environmental data: agri.signal_coverage_audit is a GOVERNANCE record of what an upstream was
-- asked for and what it answered, and the retirement track's inventory classes it "keep". The read
-- is also bounded on both sides -- the cell array is capped upstream at MAX_CELL_FANOUT cells
-- before the audit is touched, and the window overlap caps it to the audit rows spanning one day.
-- An indexed range read over a governance ledger is not the whole-plane scan the retirement exists
-- to remove.
--
-- It is also the one question the Parquet warehouse cannot answer. A governed-absence marker
-- settles a whole LANE-DAY and says the source had nothing; this table is grained by signal, cell
-- and fetched window and says WHY nothing landed for one of them. Folding a reason-for-absence
-- ledger into a day-level marker would lose exactly the column that makes an empty day
-- explainable. The day-level half of the question is answered instead by the four warehouse
-- states, which travel in the same payload as `day_state`, and the two are reported side by side.
--
-- How this query works, clause by clause:
--
--   WITH nearby_cells AS (...)
--     The cells the value answer came from, unpacked from the two arrays the caller bound. Reading
--     the audit over a wider set of cells than the value would let an absence recorded somewhere
--     else appear to explain this point.
--
--   unnest(cell_ids, cell_distances) AS entry(cell_id, distance_m)
--     unnest() with two arguments walks two arrays IN STEP, emitting one row per position with a
--     value from each. The two arrays are built together in Python from one ordered list of cells,
--     so position i is the same cell in both; the alias list after AS names the two columns that
--     come out. A single-argument unnest per array plus a join would need a position key the caller
--     does not have.
--
--   CAST(cell_ids AS uuid[])
--     The Parquet signal plane stores cell_id as text, because Parquet has no uuid type. The audit
--     table's cell_id is a real uuid, so the array is cast once here rather than every row being
--     cast on the way past -- and a value that is not a uuid fails loudly at the cast instead of
--     silently matching nothing.
--
--   audit.window_start < day_end AND audit.window_end >= day_start
--     The overlap test. An audit row describes a WINDOW a lane fetched, not a single day, so the
--     question is whether that window contains the day being asked about. Two ranges overlap when
--     each one starts before the other ends. window_end is written as the last instant of its last
--     day (23:59:59.999999 UTC), so comparing it with >= against the opening midnight is correct,
--     while window_start is compared with a strict < against the following midnight so a window
--     that only begins the next day is not counted.
--
--   cardinality(signal_names) = 0 OR audit.signal_name = ANY(signal_names)
--     The optional filter. cardinality() is the array's length, so an empty array means "no filter
--     requested" and every signal passes. ANY(array) is the SQL spelling of "is in this list" for
--     an array-typed bind parameter.
--
--   GROUP BY signal_name, source_parameter, support_key, status
--     status is part of the key on purpose. A day where 300 cells came back complete and 97 came
--     back no_data is two different facts, and collapsing them to one row would destroy the more
--     interesting of the two.
--
--   sum(expected_observation_count) / sum(received_observation_count)
--     What the lane expected from the upstream against what it actually got, added up over the
--     cells in range. A partial row with received well under expected is a thin day, which is a
--     different thing again from an absent one.
--
--   min(audit.window_start) / max(audit.window_end)
--     The real span of the audit rows behind the group, returned so a reader can see that a
--     no_data verdict may have been recorded for a whole month rather than for this day alone.
WITH nearby_cells AS (
    SELECT
        entry.cell_id,
        entry.distance_m
    FROM unnest(
        CAST(:cell_ids AS uuid[]),
        CAST(:cell_distances AS double precision[])
    ) AS entry(cell_id, distance_m)
)
SELECT
    audit.signal_name,
    audit.source_parameter,
    audit.support_key,
    audit.status,
    count(*) AS audit_row_count,
    count(DISTINCT audit.cell_id) AS cell_count,
    sum(audit.expected_observation_count) AS expected_observation_count,
    sum(audit.received_observation_count) AS received_observation_count,
    min(audit.window_start) AS earliest_window_start,
    max(audit.window_end) AS latest_window_end,
    min(nearby_cells.distance_m) AS nearest_cell_distance_m
FROM agri.signal_coverage_audit AS audit
INNER JOIN nearby_cells ON nearby_cells.cell_id = audit.cell_id
WHERE audit.window_start < :day_end
  AND audit.window_end >= :day_start
  AND (cardinality(:signal_names) = 0 OR audit.signal_name = ANY(:signal_names))
GROUP BY audit.signal_name, audit.source_parameter, audit.support_key, audit.status
ORDER BY audit.signal_name, audit.status
LIMIT :row_limit
