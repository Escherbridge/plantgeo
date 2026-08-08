-- drought_area_validity_counts
-- Purpose: one summary row for the whole `geo.drought_areas` table, counting the per-row validity
--          faults the report looks for -- missing shape, an unparseable release day, a release day in
--          the future, and a polygon lying entirely outside the configured bounding box.
-- Loaded by: agri_data_service.ingest.validation
-- Params: server_day (date) -- today in UTC, from server_day.sql; any day after it is impossible,
--         bbox_west / bbox_south / bbox_east / bbox_north (double precision, all four nullable) --
--         the configured bounding box corners, all NULL together when no box is configured.
--
-- The first line above is a dispatch marker the unit tests match statements on. It stays first and
-- stays spelled as it is -- see "Marker protocol" in sql/AGENTS.md.
--
-- Parameter names appear above WITHOUT a leading colon. See "Header/bind-param trap" in
-- sql/AGENTS.md: SQLAlchemy scans comments for colon-prefixed words too and would mint a bind
-- parameter nobody supplies.
--
-- What this returns: exactly one row. `total_rows` is how many drought-class polygons are stored in
-- total; every other column counts the rows failing one specific check. A zero means the check ran and
-- found nothing wrong -- with one exception, described next.
--
-- WHY THE BOUNDING-BOX CHECK IS SAFE WITH NO BOX CONFIGURED: the four ordinates arrive as NULL when
-- INGEST_BBOX is unset. ST_MakeEnvelope is declared STRICT, meaning it answers NULL as soon as any
-- argument is NULL rather than raising, so ST_Intersects answers NULL, so the FILTER condition is
-- neither true nor false and counts nothing. The counter reads 0 rather than the statement failing --
-- and the Python side, which knows whether a box was configured, reports that check as unevaluated
-- rather than as a clean pass, so the zero is never mistaken for evidence.
--
-- How this query works, clause by clause:
--
--   SELECT count(*) ... FROM geo.drought_areas   (with no GROUP BY)
--     An aggregate with no GROUP BY treats the entire table as one single group, so the result is
--     always exactly one row -- even when the table is empty, in which case every counter is 0. That
--     is deliberate: the drought layer is one stream, unlike geo.features which holds many layers side
--     by side and therefore has to be grouped.
--
--   count(*)
--     The number of rows in the group. `*` here does not mean "all columns"; it means "count rows,
--     including rows whose columns are NULL".
--
--   count(*) FILTER (WHERE <condition>)
--     FILTER restricts one aggregate to the rows matching its own condition, while the other
--     aggregates in the same SELECT still see every row. It is what lets four different questions be
--     answered from a single pass over the table. Rows failing the FILTER condition are simply not
--     counted by that one aggregate; they are not removed from the group.
--
--   FILTER (WHERE drought_areas.geom IS NULL)
--     Rows with no stored shape. `IS NULL` rather than `= NULL` because comparing anything to NULL in
--     SQL yields NULL -- neither true nor false -- so `IS` is the only test that works.
--
--   undated_day: valid_date IS NULL OR does not match the shape OR is not a real day
--     `geo.drought_areas.valid_date` is a VARCHAR, not a date column, so "is this row dated" takes
--     three tests rather than one. `~` is the "matches this regular expression" operator and `!~` its
--     negation; `^` and `$` anchor the pattern to the start and end of the whole string, `\d` means a
--     digit, and the braced number is how many of them -- so the pattern accepts exactly four digits,
--     a hyphen, two digits, a hyphen, two digits. `pg_input_is_valid` then answers true or false for
--     "could this text be parsed as a date", WITHOUT raising when the answer is no; it is what rejects
--     a well-shaped but impossible day such as the 31st of February. The three tests are joined with
--     OR, so any one of them failing counts the row once.
--
--   future_day: the same two guards, then to_date(...) > server_day
--     The guards are repeated here rather than being shared, and that repetition is load-bearing:
--     `to_date('2026-02-31', 'YYYY-MM-DD')` raises rather than answering NULL, so it must never be
--     reached for a value the guards would reject. SQL does not promise to evaluate the parts of an
--     AND in written order, but it does guarantee that a row failing an earlier conjunct is not
--     counted -- pairing the guards with the parse in one condition is what keeps the two together.
--
--   NOT ST_Intersects(drought_areas.geom, ST_MakeEnvelope(west, south, east, north, 4326))
--     PostGIS. ST_MakeEnvelope builds a rectangle from four corner ordinates; 4326 is the numeric id
--     of the WGS 84 longitude/latitude coordinate system, which is what everything in this warehouse
--     is stored in. ST_Intersects answers true when two shapes touch or overlap at all, so `NOT
--     ST_Intersects` counts polygons lying entirely outside the configured box.
--
--   (west)::double precision
--     A cast whose only job is to pin the bound parameter's type. Without it PostgreSQL has nothing to
--     infer the parameter's type from -- ST_MakeEnvelope has several overloads -- and refuses the
--     statement rather than guessing. The value is unchanged; only its declared type is fixed.
SELECT count(*) AS total_rows,
       count(*) FILTER (WHERE drought_areas.geom IS NULL) AS null_geom,
       count(*) FILTER (
           WHERE drought_areas.valid_date IS NULL
              OR drought_areas.valid_date !~ '^\d{4}-\d{2}-\d{2}$'
              OR NOT pg_input_is_valid(drought_areas.valid_date, 'date')
       ) AS undated_day,
       count(*) FILTER (
           WHERE drought_areas.valid_date ~ '^\d{4}-\d{2}-\d{2}$'
             AND pg_input_is_valid(drought_areas.valid_date, 'date')
             AND to_date(drought_areas.valid_date, 'YYYY-MM-DD') > :server_day
       ) AS future_day,
       count(*) FILTER (
           WHERE drought_areas.geom IS NOT NULL
             AND NOT ST_Intersects(
                     drought_areas.geom,
                     ST_MakeEnvelope(
                         (:bbox_west)::double precision,
                         (:bbox_south)::double precision,
                         (:bbox_east)::double precision,
                         (:bbox_north)::double precision,
                         4326
                     )
                 )
       ) AS outside_bbox
  FROM geo.drought_areas AS drought_areas
