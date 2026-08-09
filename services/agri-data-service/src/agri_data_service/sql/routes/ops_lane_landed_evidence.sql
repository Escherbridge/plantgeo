-- Purpose: for each stream an archive-walk lane fills, when a row last actually landed in it,
--          how many landed in the trailing window and which observed days it has been writing
--          in the last hour -- so the /ops/backfill lanes table can tell a lane whose LEDGER
--          was abandoned apart from a lane that genuinely stopped working.
-- Loaded by: agri_data_service.routes.ops
-- Params: stream_names (text[]) -- the geo.layers names to gather evidence for. They come from
--         the one catalog that maps a lane to the stream it fills, ingest.validation.models
--         .DEFAULT_STREAM_DEFINITIONS, never from a literal spelled here. The array type is
--         attached at the call site with bindparam(..., type_=ARRAY(Text)); a .sql file has no
--         way to carry Python-side type information.
--         throughput_window_hours (int) -- how many trailing hours the row counter looks back
--         over. The lanes table divides its own counters by this same number, so an operator
--         comparing the two columns is comparing like with like. The route clamps it to at
--         least _MIN_THROUGHPUT_WINDOW_HOURS (1), which is what lets the fixed one-hour day
--         window below sit INSIDE this one rather than reaching outside the rows scanned.
--
-- Parameter names appear above WITHOUT a leading colon. See "Header/bind-param trap" in
-- sql/AGENTS.md: SQLAlchemy scans comments for colon-prefixed words too, and would mint a bind
-- parameter nobody supplies.
--
-- WHY THIS STATEMENT EXISTS
--
-- The lanes table beside it is reconstructed entirely from the agri.job_* ledger. That is sound
-- only while a lane's driver still writes to the ledger, and for the two archive walks it no
-- longer does: since 2026-08-07 durable-archive-backfill.sh tracks its own position in a local
-- cursor file and calls the bare `ingest-backfill` verb, which enqueues nothing. Their ledger
-- rows are therefore frozen mid-campaign -- succeeded counts, completion, throughput and the
-- "last recorded activity" stamp all stopped moving -- while the walks themselves keep writing
-- real rows. A dashboard reading only the ledger presents those frozen numbers as current state,
-- and an operator concludes the lane died a day ago.
--
-- This asks the opposite question, the same way ops_data_streams.sql and ops_historical_walks
-- .sql already do for the loads the ledger cannot see at all: not "what work is queued" but
-- "did anything actually land, and when". The two together are what makes the lanes table
-- honest. It deliberately does NOT repair the ledger, and nothing here writes: wiring the driver
-- back into the job/work-item/attempt/checkpoint protocol is a schema and semantics change, and
-- this is a display.
--
-- WHAT IT IS EVIDENCE OF, EXACTLY
--
-- One row per STREAM, not per lane and not per job run. Several lanes may fill the same stream
-- (a forward NRT cron and a backward archive walk both write `fire-detections`), so a count here
-- is the stream's whole recent intake and never one run's own output. The route words the line
-- it renders "stream-wide" for that reason.
--
-- WHY geo.features AND NOT agri.source_release
--
-- The plan-driven walks publish one agri.source_release per chunk, which is what makes
-- ops_historical_walks.sql cheap. The ingest lanes publish none -- MTBS is the only ingest path
-- that writes a release -- so for these two lanes the written feature rows are the only durable
-- evidence there is.
--
-- WHY IT IS SHAPED AROUND AN INDEX RANGE AND NOT A GREATEST() OVER EVERY ROW
--
-- geo.features carries (layer_id, created_at) and (layer_id, updated_at) btree indexes, added by
-- drizzle/0022_features_write_time_indexes.sql FOR THIS STATEMENT. An index can only be used when
-- the predicate names an INDEXED COLUMN directly, so every write-time test below compares
-- created_at and updated_at separately and lets the planner OR the two index ranges together.
-- The obvious spelling -- max(GREATEST(created_at, updated_at)) with count(*) FILTER beside it --
-- cannot use either index no matter what exists: GREATEST is computed per row, so the server must
-- read every row of the layer to evaluate it, and a FILTER restricts the AGGREGATE, never the
-- underlying scan. That spelling measured 2.2 s against production 2026-08-09 over the 2,487,070
-- rows these two layers hold today (Seq Scan, 295,575 buffers), the page re-runs it once a minute
-- per distinct rate window against the production pool on an UNAUTHENTICATED route, and the FIRMS
-- archive walk this panel exists to watch is 93 of 1,882 windows in -- finishing it grows the layer
-- roughly twentyfold. The route also pins a transaction-local statement_timeout, so if this ever
-- does degrade it fails loudly rather than pinning a connection.
--
-- THIS SHAPE IS SLOWER THAN THE OLD ONE UNTIL 0022 IS APPLIED, and that is not a hedge: measured
-- on production before the migration it is 3.8 s against the old 2.2 s, because the two scalar
-- maxes fall back to scanning the layer through idx_features_layer. Measured on a throwaway
-- replica at 2.4M rows carrying 0022 it is 0.33 s against the old shape's 2.1 s -- a BitmapOr over
-- both new indexes for the counter and an Index Only Scan Backward stopping at one row for each
-- max. Ship them together; if the service deploys first the page is slower for that window, and
-- bounded by the timeout, but it is not wrong.
--
-- How this query works, clause by clause:
--
--   WITH lane_layer AS (SELECT ... WHERE layer.name = ANY(:stream_names))
--     A CTE ("common table expression") -- a named subquery written up front and referenced below
--     like a table. It resolves the caller's stream names to layer ids ONCE. ANY(array) is "equal
--     to any element of this array", the array form of IN and the form a single bound parameter
--     can carry. Only the streams the caller named are ever looked at.
--
--   CROSS JOIN LATERAL (SELECT count(*) ... WHERE feature.layer_id = lane_layer.layer_id AND
--                       (created_at >= ... OR updated_at >= ...)) AS recent
--     LATERAL lets a subquery in the FROM clause see the columns of the row beside it, so this runs
--     ONCE PER NAMED LAYER with that layer's id as a constant. The window predicate sits here, in
--     the scan's own WHERE, rather than in an aggregate FILTER -- that is the whole point: it
--     decides which rows are READ, where a FILTER only decides which read rows are counted. Both
--     halves of the OR are needed because a walk does two things -- a day the archive has never
--     seen arrives as an INSERT and moves created_at, while a re-walked day whose payload changed
--     arrives as a refresh and moves only updated_at, so reading created_at alone would call a
--     re-walk idle -- and written as an OR of two indexed ranges the planner takes a bitmap index
--     scan of each and ORs the results. A bitmap OR de-duplicates by row, so a row inside both
--     ranges is still counted exactly once. LATERAL rather than an ordinary join is load-bearing
--     and was measured: joining geo.features to the layer CTE on layer_id makes the planner hash
--     the two together, and a hashed join key cannot be pushed into an index condition, so the same
--     predicate falls back to a sequential scan of the whole table.
--
--     CROSS rather than LEFT, and no COALESCE, because an aggregate with no GROUP BY returns
--     exactly one row even when nothing matches -- count 0 and NULL for the rest. A named stream
--     that wrote nothing in the window, or holds no features at all, therefore still reports an
--     honest zero instead of vanishing from the result and leaving the route to guess whether it
--     asked about a stream that does not exist.
--
--   now() - make_interval(hours => CAST(:throughput_window_hours AS integer))
--     Interval arithmetic. now() is the current transaction timestamp and make_interval(hours => N)
--     builds a span of N hours; subtracting gives the start of the window. make_interval is used
--     rather than a quoted interval literal because a bound parameter cannot appear inside quotes,
--     and the CAST pins the parameter to integer so the database never has to guess its type.
--
--   min/max(geo.feature_observation_day(feature.properties)) FILTER (... interval '1 hour')
--     Which days the lane has been writing lately, read off the rows themselves. This is the
--     honest replacement for the ledger's frontier column on an abandoned lane: a walk stepping
--     backward through 2018 says so here even though its ledger frontier is frozen at the shard
--     key it stopped claiming. It is the same function ops_data_streams.sql uses, so both panels
--     read a feature's observation day exactly one way.
--
--     A FILTER is right HERE and wrong on the counter above, because these two aggregates are not
--     narrowing the scan -- they are declining to run an expensive function on most of the rows it
--     already returned. Dating a row runs a regex, a validity probe and a to_date over its jsonb,
--     and measured against production 2026-08-09 that costs about twenty times what counting one
--     costs: the same two columns over a 24-hour window took the whole statement from 1.4 s to
--     10.0 s, because 24 hours of these two lanes is 1.2M rows and one hour is roughly 47k.
--     PostgreSQL evaluates a filtered aggregate's argument only for rows that pass its FILTER, so
--     the function runs on the hour's rows alone.
--
--     The one hour is a FIXED span and deliberately not the caller's window, which is the only
--     hard-coded interval in this file, and the route labels the pair "(last hour)" on the page so
--     it is never read as the same window as the count beside it. An hour is also the more useful
--     question -- this pair is "where is it working now", not "where has it been" -- and when a
--     lane has been quiet for longer than that both columns come back NULL, which reads correctly
--     as "nothing written recently enough to say". Because the route's window is at least one hour,
--     the hour is always a subset of the rows this CTE already read.
--
--   (SELECT max(feature.created_at) ...), (SELECT max(feature.updated_at) ...)
--     "When did a row last land in this layer at all", with no window on it. Two separate maxes
--     wrapped in one GREATEST rather than one max of a per-row GREATEST: max(GREATEST(a, b)) and
--     GREATEST(max(a), max(b)) are the same number, but only the second can be answered by walking
--     an index backwards from its end and stopping at the first row. GREATEST returns the larger of
--     its arguments and ignores NULL ones, so a layer that has only ever been inserted into still
--     answers, and a layer with no rows at all answers NULL.
--
--   no filter on feature.status
--     Deliberate, and the one place this query differs from ops_data_streams.sql, which counts
--     only published rows because only published rows are servable. The question here is not
--     "what can the map draw" but "did this lane do any work", and a row awaiting review is work
--     that happened. Counting only published rows would report a lane whose output is held in
--     review as dead.
--
--   ORDER BY lane_layer.stream
--     A stable, total order so the dashboard's rows do not shuffle between refreshes.
WITH lane_layer AS (
    SELECT
        layer.id AS layer_id,
        layer.name AS stream
    FROM geo.layers AS layer
    WHERE layer.name = ANY(:stream_names)
)
SELECT
    lane_layer.stream,
    GREATEST(
        (
            SELECT max(feature.created_at)
            FROM geo.features AS feature
            WHERE feature.layer_id = lane_layer.layer_id
        ),
        (
            SELECT max(feature.updated_at)
            FROM geo.features AS feature
            WHERE feature.layer_id = lane_layer.layer_id
        )
    ) AS last_write_at,
    recent.rows_in_window,
    recent.oldest_day_written_recently,
    recent.newest_day_written_recently
FROM lane_layer
CROSS JOIN LATERAL (
    SELECT
        count(*) AS rows_in_window,
        min(geo.feature_observation_day(feature.properties)) FILTER (
            WHERE feature.created_at >= now() - interval '1 hour'
               OR feature.updated_at >= now() - interval '1 hour'
        ) AS oldest_day_written_recently,
        max(geo.feature_observation_day(feature.properties)) FILTER (
            WHERE feature.created_at >= now() - interval '1 hour'
               OR feature.updated_at >= now() - interval '1 hour'
        ) AS newest_day_written_recently
    FROM geo.features AS feature
    WHERE feature.layer_id = lane_layer.layer_id
      AND (
          feature.created_at >= now() - make_interval(hours => CAST(:throughput_window_hours AS integer))
          OR feature.updated_at >= now() - make_interval(hours => CAST(:throughput_window_hours AS integer))
      )
) AS recent
ORDER BY lane_layer.stream
