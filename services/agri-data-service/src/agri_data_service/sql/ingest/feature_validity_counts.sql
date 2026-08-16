-- feature_validity_counts
-- Purpose: one row per feature layer holding the total published row count beside a counter for every
--          per-row validity fault the report knows how to look for -- missing shape, unlinked
--          geometry, missing or over-long producer id, undated or future-dated day, a shape outside
--          the configured bounding box, and a stored missing-value sentinel masquerading as a reading.
-- Loaded by: agri_data_service.ingest.validation.queries (which formats it three ways -- unscoped, one
--          inclusive day range, and undated-only -- see "THE DAY SCOPE SLOT" below)
-- Params: published_status (text) -- the one `geo.features.status` value the map serves,
--         producer_local_id_ceiling_by_stream (text holding JSON) -- an object mapping a layer name to
--         the maximum length its producer-local id may have,
--         server_day (date) -- today in UTC, from server_day.sql; any day after it is impossible,
--         bbox_west / bbox_south / bbox_east / bbox_north (double precision, all four nullable) --
--         the configured bounding box corners, all NULL together when no box is configured,
--         sentinel_property_by_stream (text holding JSON) -- an object mapping a layer name to the
--         one payload field whose value must be checked against the sentinel,
--         missing_value_sentinel (double precision) -- the numeric value an upstream publisher writes
--         in place of a reading it does not have,
--         from_day, to_day (date) -- an INCLUSIVE observation-day range, supplied ONLY by the day-range
--         form; the unscoped form and the undated-only form carry neither.
--
-- The first line above is a dispatch marker the unit tests match statements on. It stays first and
-- stays spelled as it is -- see "Marker protocol" in sql/AGENTS.md.
--
-- Parameter names appear above WITHOUT a leading colon. See "Header/bind-param trap" in
-- sql/AGENTS.md: SQLAlchemy scans comments for colon-prefixed words too and would mint a bind
-- parameter nobody supplies. That rule is stricter than usual in this file, because the day scope slot
-- below means the parameter list is not the same for all three forms.
--
-- What this returns: one row per layer, ordered by layer name. The first column is the layer name,
-- the second is how many published rows it holds, and every column after that is a count of rows
-- failing one specific check. A zero means the check ran and found nothing wrong.
--
-- WHY ONE STATEMENT AND NOT EIGHT: every counter below is computed in a single grouped pass over
-- geo.features, so production is scanned once rather than eight times. Eight separate statements
-- would also each see a slightly different snapshot of a table the hourly cron is writing to.
--
-- WHY THE BOUNDING-BOX CHECK IS SAFE WITH NO BOX CONFIGURED: the four ordinates arrive as NULL when
-- INGEST_BBOX is unset. ST_MakeEnvelope is declared STRICT, meaning it answers NULL as soon as any
-- argument is NULL rather than raising, so ST_Intersects answers NULL, so the FILTER condition is
-- neither true nor false and counts nothing. The counter therefore reads 0 instead of the statement
-- failing -- and the Python side, which knows whether a box was configured, reports that check as
-- unevaluated rather than as a clean pass. The zero here is never mistaken for evidence.
--
-- THE DAY SCOPE SLOT, AND WHY IT HAS THREE FORMS INSTEAD OF TWO. A `{{day_scope}}` slot sits in the WHERE
-- (doubled so str.format leaves this mention alone: the day-range filling is TWO lines, and substituting
-- it into a `--` comment pushed its second line out of the comment and into the statement as bare prose,
-- which made FEATURE_VALIDITY_COUNTS_FOR_DAY_RANGE invalid SQL. Any brace named here must stay doubled.)
-- clause below, filled at import time from a closed set of constants in validation/queries.py, NEVER
-- from request input -- the same load-time slot pattern observed_days.sql already uses for its own day
-- scope. It exists for the same reason: this statement scans every published row of geo.features once
-- per execution with no index to prune by, and observed_days.sql measured that same shape of scan at 81
-- to 101 seconds against the 120-second transaction statement timeout once a layer grew large enough
-- (see that file's header). Cutting the scan into date-bounded shards keeps each STATEMENT under that
-- timeout, the same trade observed_days.sql makes: total work multiplies by the shard count, but
-- thirteen statements that each finish beat one statement the database kills at 120 seconds.
--
-- Where this file differs from observed_days.sql: most of what it counts is NOT about the observation
-- day at all. total_rows, null_geom, missing_external_id, unlinked_geometry, outside_bbox and
-- missing_value_sentinel all want every published row regardless of date, and undated_day specifically
-- wants the rows whose day COULD NOT be parsed. A plain date-range predicate structurally cannot select
-- those rows -- geo.feature_observation_day returns NULL for them, and NULL fails every comparison, so
-- no range, however wide, ever matches it. A caller that sharded this scan the way observed_days.sql is
-- sharded -- narrower and narrower date ranges covering the whole calendar -- would silently lose every
-- undated row from every counter, in the exact direction this whole report exists to catch: a row that
-- is really there would be counted as though it were not.
--
-- So the day scope slot has three fillings instead of two: empty (the unscoped form, byte-for-byte what
-- this statement was before this slot existed, and what the completeness report still calls), an
-- inclusive date range (for a caller sharding the scan by day), and an undated-only predicate that is
-- the date range's complement, not a narrower case of it. A caller sharding this scan sums every counter
-- across however many date-range shards it runs PLUS one execution of the undated-only form, and that
-- sum reproduces the unscoped form's own grand total exactly -- every published row is counted by
-- precisely one of those executions, never zero and never two.
--
-- How this query works, clause by clause:
--
--   FROM geo.layers AS layers JOIN geo.features AS features ON features.layer_id = layers.id
--     A join pairs each feature row with the layer row it belongs to, so the layer's readable name is
--     available beside the feature. A plain (inner) JOIN keeps only pairs that match, which is right
--     here: a layer holding no published features has no per-row faults to report.
--
--   WHERE features.status = published_status
--     Only rows the map actually serves. A fault on a row nobody can see is not a fault the report is
--     asked about.
--
--   the day scope slot, immediately below that filter
--     Either nothing at all (the unscoped form -- every published row, regardless of date), an
--     inclusive date range on geo.feature_observation_day(features.properties) with both bounds cast to
--     date for the same type-pinning reason observed_days.sql casts its own day bounds (the day-range
--     form, for a caller sharding this scan), or a single IS NULL test on that same expression (the
--     undated-only form, which a date range can never reach). See "THE DAY SCOPE SLOT" above.
--
--   GROUP BY layers.name
--     GROUP BY collapses many rows into one row per distinct layer name. Once rows are collapsed there
--     is no single "the feature" left to select, so every other selected column must be an aggregate
--     summarising the whole group -- which is exactly what all these counters are.
--
--   count(*)
--     The number of rows in the group. `*` here does not mean "all columns"; it means "count rows,
--     including rows whose columns are NULL".
--
--   count(*) FILTER (WHERE <condition>)
--     FILTER restricts one aggregate to the rows matching its own condition, while the other
--     aggregates in the same SELECT still see every row in the group. It is what lets nine different
--     questions be answered from a single pass. Rows failing the FILTER condition are simply not
--     counted by that one aggregate; they are not removed from the group.
--
--   FILTER (WHERE features.geom IS NULL)
--     Rows with no stored shape. `IS NULL` rather than `= NULL` because comparing anything to NULL in
--     SQL yields NULL -- neither true nor false -- so `IS` is the only test that works.
--
--   FILTER (WHERE features.geometry_id IS NULL)
--     Rows never linked into the geometry dimension. They have a shape but no version history, so the
--     time-aware serving path cannot place them on any day.
--
--   nullif(btrim(coalesce(features.properties ->> 'id', '')), '') IS NULL
--     Three nested functions reading one JSON field, innermost first.
--       `properties ->> 'id'` pulls the `id` field out of the JSON payload AS TEXT. The two-arrow
--       form `->>` yields text; the one-arrow form `->` would yield JSON. A missing field yields NULL.
--       `coalesce(x, '')` returns its first non-NULL argument, so a missing field becomes an empty
--       string rather than NULL.
--       `btrim(x)` strips leading and trailing whitespace, so an id of three spaces becomes empty.
--       `nullif(x, '')` returns NULL when its two arguments are equal, turning that empty string back
--       into NULL.
--     Net effect: the condition is true when the payload carries no id, an id that is only whitespace,
--     or no `id` field at all -- one test covering all three shapes of "no usable producer id".
--
--   (producer_local_id_ceiling_by_stream)::jsonb ? layers.name
--     The parameter arrives as text and is cast to `jsonb`, PostgreSQL's parsed binary JSON type. The
--     cast is load-bearing rather than decorative: without it the value is just text and none of the
--     JSON operators below would apply to it. The `?` operator asks "does this JSON object have a
--     top-level key with this name" -- so this counter only fires for layers that actually declare a
--     ceiling, and silently skips the rest.
--
--   length(features.properties ->> 'id') > ((... ->> layers.name))::integer
--     The layer's declared ceiling is pulled out of the same JSON object by key, arriving as text, and
--     cast to `integer` so it can be compared numerically. Comparing as text would order "9" above
--     "10". An id longer than its layer's ceiling is malformed -- it means the producer changed the
--     shape of its own key.
--
--   FILTER (WHERE geo.feature_observation_day(features.properties) IS NULL)
--     Rows whose payload carries no parseable observation day. `geo.feature_observation_day` is the
--     one database function that dates a feature; calling it rather than reading a column keeps this
--     report and the map's day axis on the same rule.
--
--   FILTER (WHERE geo.feature_observation_day(features.properties) > server_day)
--     Rows dated after today in UTC. A future observation is always a publisher or parsing fault.
--
--   NOT ST_Intersects(features.geom, ST_MakeEnvelope(west, south, east, north, 4326))
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
--   jsonb_typeof(features.properties -> (<the layer's sentinel field name>)) = 'number'
--     `jsonb_typeof` names the JSON type of a value: 'number', 'string', 'null', and so on. The check
--     runs only when the stored value really is a JSON number, so a field holding the string "-999999"
--     is not silently read as the numeric sentinel. Note the single arrow `->` here, which keeps the
--     value as JSON so jsonb_typeof can inspect it; the double arrow `->>` on the next line pulls the
--     same field out as text so it can be cast to a number and compared.
--
--   (...)::double precision = missing_value_sentinel
--     The final comparison. A row counted here stores a publisher's "I have no reading" placeholder in
--     a numeric field, which every downstream consumer would otherwise read as a real measurement.
--
--   ORDER BY layers.name
--     A stable order so two runs of the report list layers identically.
SELECT layers.name AS stream,
       count(*)    AS total_rows,
       count(*) FILTER (WHERE features.geom IS NULL)          AS null_geom,
       count(*) FILTER (WHERE features.geometry_id IS NULL)   AS unlinked_geometry,
       count(*) FILTER (
           WHERE nullif(btrim(coalesce(features.properties ->> 'id', '')), '') IS NULL
       ) AS missing_external_id,
       count(*) FILTER (
           WHERE (:producer_local_id_ceiling_by_stream)::jsonb ? layers.name
             AND nullif(btrim(coalesce(features.properties ->> 'id', '')), '') IS NOT NULL
             AND length(features.properties ->> 'id')
                 > (((:producer_local_id_ceiling_by_stream)::jsonb ->> layers.name))::integer
       ) AS malformed_identity,
       count(*) FILTER (
           WHERE geo.feature_observation_day(features.properties) IS NULL
       ) AS undated_day,
       count(*) FILTER (
           WHERE geo.feature_observation_day(features.properties) > :server_day
       ) AS future_day,
       count(*) FILTER (
           WHERE features.geom IS NOT NULL
             AND NOT ST_Intersects(
                     features.geom,
                     ST_MakeEnvelope(
                         (:bbox_west)::double precision,
                         (:bbox_south)::double precision,
                         (:bbox_east)::double precision,
                         (:bbox_north)::double precision,
                         4326
                     )
                 )
       ) AS outside_bbox,
       count(*) FILTER (
           WHERE (:sentinel_property_by_stream)::jsonb ? layers.name
             AND jsonb_typeof(
                     features.properties -> ((:sentinel_property_by_stream)::jsonb ->> layers.name)
                 ) = 'number'
             AND (features.properties ->> ((:sentinel_property_by_stream)::jsonb ->> layers.name))
                     ::double precision = :missing_value_sentinel
       ) AS missing_value_sentinel
  FROM geo.layers AS layers
  JOIN geo.features AS features
    ON features.layer_id = layers.id
 WHERE features.status = :published_status
   {day_scope}
 GROUP BY layers.name
 ORDER BY layers.name
