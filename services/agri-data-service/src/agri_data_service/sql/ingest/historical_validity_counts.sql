-- historical_validity_counts
-- Purpose: one summary row per historical stream, counting the per-row validity faults the report
--          looks for -- missing shape, an undated row, a row dated in the future, and a shape lying
--          entirely outside the configured bounding box.
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
-- What this returns: exactly three rows, one per historical stream, whatever the tables contain. Each
-- carries the stream's total row count and one counter per check; a zero means the check ran and found
-- nothing wrong, with the bounding-box exception described next.
--
-- WHY THE BOUNDING-BOX CHECK IS SAFE WITH NO BOX CONFIGURED: the four ordinates arrive as NULL when
-- INGEST_BBOX is unset. ST_MakeEnvelope is declared STRICT, meaning it answers NULL as soon as any
-- argument is NULL rather than raising, so ST_Intersects answers NULL, so the FILTER condition is
-- neither true nor false and counts nothing. The counter reads 0 rather than the statement failing --
-- and the Python side, which knows whether a box was configured, reports that check as unevaluated
-- rather than as a clean pass, so the zero is never mistaken for evidence.
--
-- WHY 'UTC' IS PINNED EXPLICITLY: `date_bucket` is a `timestamptz`, an instant rather than a wall
-- clock. Casting one straight to `date` renders it in whatever time zone the SESSION is set to, so the
-- same stored instant could be judged "in the future" on one host and not on another for several hours
-- either side of midnight. Converting to UTC first pins the answer.
--
-- The three branches are identical apart from the table and the stream label, so the walkthrough below
-- describes one branch; it applies unchanged to the other two.
--
-- How this query works, clause by clause:
--
--   SELECT 'historical_vegetation' AS stream, ... FROM geo.historical_vegetation   (no GROUP BY)
--     An aggregate with no GROUP BY treats the entire table as one single group, so each branch always
--     yields exactly one row -- even over an empty table, in which case every counter is 0. That is
--     why the result is three rows no matter what the tables hold, which in turn is what lets the
--     report distinguish "this stream is empty" from "this stream was not measured". The stream name
--     is a literal, not a column: there is no table here naming the stream, the stream IS the table.
--
--   count(*)
--     The number of rows in the group. `*` here does not mean "all columns"; it means "count rows,
--     including rows whose columns are NULL".
--
--   count(*) FILTER (WHERE <condition>)
--     FILTER restricts one aggregate to the rows matching its own condition, while the other
--     aggregates in the same SELECT still see every row. It is what lets four different questions be
--     answered from a single pass over each table. Rows failing the FILTER condition are simply not
--     counted by that one aggregate; they are not removed from the group.
--
--   FILTER (WHERE historical.geom IS NULL)
--     Rows with no stored shape. `IS NULL` rather than `= NULL` because comparing anything to NULL in
--     SQL yields NULL -- neither true nor false -- so `IS` is the only test that works.
--
--   FILTER (WHERE historical.date_bucket IS NULL)
--     Undated rows. They contribute no day to the axis, so they are invisible to any time-sliced view
--     of this stream while still occupying storage.
--
--   FILTER (WHERE (historical.date_bucket AT TIME ZONE 'UTC')::date > server_day)
--     Rows dated after today in UTC. `AT TIME ZONE 'UTC'` re-expresses the stored instant as UTC wall
--     clock and `::date` -- PostgreSQL's short spelling of CAST(value AS date) -- discards the time of
--     day. The parentheses matter: without them the cast would bind tighter than the conversion. A
--     future-dated row is always a publisher or parsing fault.
--
--   NOT ST_Intersects(historical.geom, ST_MakeEnvelope(west, south, east, north, 4326))
--     PostGIS. ST_MakeEnvelope builds a rectangle from four corner ordinates; 4326 is the numeric id
--     of the WGS 84 longitude/latitude coordinate system, which is what everything in this warehouse
--     is stored in. ST_Intersects answers true when two shapes touch or overlap at all, so `NOT
--     ST_Intersects` counts rows lying entirely outside the configured box.
--
--   (west)::double precision
--     A cast whose only job is to pin the bound parameter's type. Without it PostgreSQL has nothing to
--     infer the parameter's type from -- ST_MakeEnvelope has several overloads -- and refuses the
--     statement rather than guessing. The value is unchanged; only its declared type is fixed.
--
--   UNION ALL
--     Stacks the three one-row results into a single three-row result. `ALL` means "keep every row"; a
--     bare UNION would additionally de-duplicate, which here could silently merge two streams whose
--     counters happened to coincide -- three empty tables would collapse to one row. The branches must
--     agree on column count and types, which they do by construction.
--
--   ORDER BY 1
--     Ordering by output POSITION rather than by name: column 1 is `stream`. Positional ordering is
--     used because an ORDER BY attached to a UNION applies to the combined result, where the
--     individual branches' table aliases are no longer in scope. It gives the report a stable row
--     order across runs.
  SELECT 'historical_vegetation' AS stream,
         count(*) AS total_rows,
         count(*) FILTER (WHERE historical.geom IS NULL) AS null_geom,
         count(*) FILTER (WHERE historical.date_bucket IS NULL) AS undated_day,
         count(*) FILTER (
             WHERE (historical.date_bucket AT TIME ZONE 'UTC')::date > :server_day
         ) AS future_day,
         count(*) FILTER (
             WHERE historical.geom IS NOT NULL
               AND NOT ST_Intersects(
                       historical.geom,
                       ST_MakeEnvelope(
                           (:bbox_west)::double precision,
                           (:bbox_south)::double precision,
                           (:bbox_east)::double precision,
                           (:bbox_north)::double precision,
                           4326
                       )
                   )
         ) AS outside_bbox
    FROM geo.historical_vegetation AS historical
UNION ALL
  SELECT 'historical_fire_data' AS stream,
         count(*) AS total_rows,
         count(*) FILTER (WHERE historical.geom IS NULL) AS null_geom,
         count(*) FILTER (WHERE historical.date_bucket IS NULL) AS undated_day,
         count(*) FILTER (
             WHERE (historical.date_bucket AT TIME ZONE 'UTC')::date > :server_day
         ) AS future_day,
         count(*) FILTER (
             WHERE historical.geom IS NOT NULL
               AND NOT ST_Intersects(
                       historical.geom,
                       ST_MakeEnvelope(
                           (:bbox_west)::double precision,
                           (:bbox_south)::double precision,
                           (:bbox_east)::double precision,
                           (:bbox_north)::double precision,
                           4326
                       )
                   )
         ) AS outside_bbox
    FROM geo.historical_fire_data AS historical
UNION ALL
  SELECT 'historical_water_drought' AS stream,
         count(*) AS total_rows,
         count(*) FILTER (WHERE historical.geom IS NULL) AS null_geom,
         count(*) FILTER (WHERE historical.date_bucket IS NULL) AS undated_day,
         count(*) FILTER (
             WHERE (historical.date_bucket AT TIME ZONE 'UTC')::date > :server_day
         ) AS future_day,
         count(*) FILTER (
             WHERE historical.geom IS NOT NULL
               AND NOT ST_Intersects(
                       historical.geom,
                       ST_MakeEnvelope(
                           (:bbox_west)::double precision,
                           (:bbox_south)::double precision,
                           (:bbox_east)::double precision,
                           (:bbox_north)::double precision,
                           4326
                       )
                   )
         ) AS outside_bbox
    FROM geo.historical_water_drought AS historical
   ORDER BY 1
