-- agent_signal_coverage_on_day
-- Purpose: read back what the ingest lanes already recorded about one caller-named day near a
--          point -- complete, partial, no_data or failed -- so an empty value answer can be
--          explained instead of merely reported.
-- Loaded by: agri_data_service.agent.tools
-- Params: longitude/latitude (double precision), radius_meters (double precision),
--         cell_limit (int), day_start/day_end (timestamptz -- the UTC midnight the day opens on
--         and the UTC midnight the next day opens on), signal_names (text[], empty array means
--         "every signal"), row_limit (int)
--
-- Parameter names appear above WITHOUT a leading colon -- see "Header/bind-param trap" in
-- sql/AGENTS.md.
--
-- agri.signal_coverage_audit is the warehouse's existing record of what we asked an upstream for
-- and what it answered. A day the provider genuinely never published carries a no_data row there,
-- written at ingest time; that is evidence, not a hole, and it is the difference between "upstream
-- has nothing for 2024-03-14" and "we do not know why 2024-03-14 is empty". This statement exists
-- so the agent can tell those two apart. Nothing new is recorded anywhere -- this reads the table
-- the lanes already fill.
--
-- How this query works, clause by clause:
--
--   WITH nearby_cells AS (...)
--     The same bounded "cells near this point" CTE the value statement uses, so the audit is read
--     over exactly the cells the value came from. Reading the audit over a wider set of cells than
--     the value would let an absence recorded somewhere else appear to explain this point.
--
--   ST_SetSRID / ST_MakePoint / ::geography / ST_DWithin
--     Builds the caller's coordinate into a PostGIS point stamped with SRID 4326 (WGS84 -- ordinary
--     GPS longitude/latitude), casts it to geography so distance is measured in metres on the
--     curved earth rather than in degrees, and tests "within radius metres" in the index-friendly
--     form.
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
        cell.id AS cell_id,
        ST_Distance(
            cell.centroid::geography,
            ST_SetSRID(ST_MakePoint(:longitude, :latitude), 4326)::geography
        ) AS distance_m
    FROM agri.spatial_cell AS cell
    WHERE ST_DWithin(
        cell.centroid::geography,
        ST_SetSRID(ST_MakePoint(:longitude, :latitude), 4326)::geography,
        :radius_meters
    )
    ORDER BY distance_m
    LIMIT :cell_limit
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
