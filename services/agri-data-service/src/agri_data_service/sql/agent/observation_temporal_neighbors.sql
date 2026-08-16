-- agent_observation_temporal_neighbors
-- Purpose: for any one of the map's 24 catalogue surfaces, find the nearest COVERED day before
--          the caller's day and the nearest one after it, each carrying its real gap in days.
-- Loaded by: agri_data_service.agent.tools
-- Params: surface_name (text -- a slider capability catalogue name, verbatim),
--         day (date -- the day the map has selected),
--         search_from/search_through (date -- the inclusive span the search may walk)
--
-- Parameter names appear above WITHOUT a leading colon -- see "Header/bind-param trap" in
-- sql/AGENTS.md.
--
-- This is the general-surface twin of signal_neighbors_in_time.sql, and it exists for the same
-- reason: a neighbouring observation handed back without its gap is indistinguishable from an
-- exact answer. A reader told "the vegetation index is 0.42" who is not also told that the
-- reading is eleven days older than the day on screen will treat it as the day's value. So every
-- row here carries which side of the day it fell on, its own covered day, and the signed and
-- unsigned gap between the two.
--
-- The search span is bounded, which makes "no covered day on this side" a statement about the
-- span searched rather than about all of history -- and the caller reports the span alongside the
-- result so the claim can be read for what it is.
--
-- THE SOURCE IS geo.v_observation_day_census, the same view the map's slider reads. Walking
-- outward from a day is exactly the access pattern its (surface_name, observed_day) key serves:
-- each half below is one index range scan stopped after a single row. That is why the caller may
-- ask for a six-month window without it costing anything -- the window bounds the ANSWER, not the
-- work.
--
-- How this query works, clause by clause:
--
--   WITH before_day AS (... ORDER BY observed_day DESC LIMIT 1)
--     A CTE ("common table expression") -- a named subquery defined up front and referenced below
--     like a table. This one walks backwards from the caller's day and stops at the first covered
--     day it finds. Ordering descending and taking one row is what makes it the LATEST day still
--     earlier than the one asked about; because the ordering column is the index's own, the
--     database stops after reading a single entry instead of sorting anything.
--
--   census.observed_day < CAST(day AS date)
--     Strictly earlier. The bind is wrapped in CAST(... AS date) rather than written with a
--     trailing double-colon cast because a double colon immediately after a bind name would stop
--     SQLAlchemy from recognising the bind at all.
--
--   census.observed_day >= search_from
--     The bound on how far back the walk may go, inclusive.
--
--   after_day AS (... ORDER BY observed_day LIMIT 1)
--     The mirror image: strictly later than the caller's day, ordered ascending so the winner is
--     the EARLIEST day still later, bounded above by search_through.
--
--   UNION ALL
--     Stacks the two halves into one result. ALL rather than a plain UNION because the halves
--     cannot overlap by construction -- one is strictly before the day and the other strictly
--     after -- so paying for duplicate removal would buy nothing.
--
--   neighbours.observed_day - CAST(day AS date) AS day_offset
--     Whole days between the covered day and the day asked about, signed: negative for a day that
--     precedes it, positive for one that follows. Subtracting two dates in PostgreSQL yields a
--     plain integer count of days.
--
--   abs(...) AS distance_days
--     The same gap as a magnitude, so a reader who only wants "how far off is this" does not have
--     to interpret a sign. The side column already says which direction.
--
--   ORDER BY distance_days, neighbours.side
--     A total order, nearest first, so a caller reading only the first row reads the closest
--     neighbour on either side rather than an arbitrary one of the two.
WITH before_day AS (
    SELECT
        'before' AS side,
        census.surface_kind,
        census.observed_day,
        census.observation_count,
        census.distinct_key_count,
        census.newest_observed_at
    FROM geo.v_observation_day_census AS census
    WHERE census.surface_name = :surface_name
      AND census.observed_day < CAST(:day AS date)
      AND census.observed_day >= :search_from
    ORDER BY census.observed_day DESC
    LIMIT 1
),
after_day AS (
    SELECT
        'after' AS side,
        census.surface_kind,
        census.observed_day,
        census.observation_count,
        census.distinct_key_count,
        census.newest_observed_at
    FROM geo.v_observation_day_census AS census
    WHERE census.surface_name = :surface_name
      AND census.observed_day > CAST(:day AS date)
      AND census.observed_day <= :search_through
    ORDER BY census.observed_day
    LIMIT 1
),
neighbours AS (
    SELECT * FROM before_day
    UNION ALL
    SELECT * FROM after_day
)
SELECT
    neighbours.side,
    CAST(:surface_name AS text) AS surface_name,
    neighbours.surface_kind,
    neighbours.observed_day,
    neighbours.observed_day - CAST(:day AS date) AS day_offset,
    abs(neighbours.observed_day - CAST(:day AS date)) AS distance_days,
    neighbours.observation_count,
    neighbours.distinct_key_count,
    neighbours.newest_observed_at
FROM neighbours
ORDER BY distance_days, neighbours.side
