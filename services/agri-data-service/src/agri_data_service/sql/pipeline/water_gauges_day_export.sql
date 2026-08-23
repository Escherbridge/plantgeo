-- water_gauges_day_export
-- Purpose: produce ONE calendar day of the water-gauges reading log at its true grain -- one row
--          per gauge per reading instant -- for writing to
--          `layer=water-gauges/kind=observed/year=/month=/day=/part-N.parquet`.
-- Loaded by: agri_data_service.pipeline.lanes.water_gauges
-- Params: observed_day (date -- the publisher-named calendar day being exported; see the
--         day-naming note below), row_limit (int -- a hard cap on returned rows; the caller
--         refuses a result that reached the cap rather than writing a silently truncated day)
--
-- Parameter names appear above WITHOUT a leading colon. See "Header/bind-param trap" in
-- sql/AGENTS.md -- SQLAlchemy's text() scans comments too, and a colon-prefixed word here would
-- mint a phantom bind parameter no caller supplies.
--
-- THIS IS THE TRUE READING LOG, NOT A LATEST-VALUE CACHE. docs/lanes/water-gauges.md section 1's
-- headline finding: every reading mints a NEW version of geo.features keyed on the pair of site
-- number and upstream reading time (ingest/identity.py, build_streamflow_gauge_identity), so one
-- gauge polled hourly by the forward path can legitimately hold many rows on one calendar day.
-- Unlike signal_plane_day_export.sql, which collapses repeated releases of the same cell-day down
-- to their newest one, this query keeps every reading -- collapsing here would delete real
-- sub-daily measurements rather than a release duplicate.
--
-- NO CELL BATCHING, UNLIKE THE SIGNAL PLANE. agri.signal_observation carries no index leading on
-- observed_at and forces a full heap scan without one (see signal_plane_day_export.sql).
-- geo.features already carries ix_features_layer_observation_day on
-- (layer_id, geo.feature_observation_day(properties)) INCLUDE (geometry_id) WHERE status =
-- 'published' (drizzle/0031_observation_day_axis.sql), which is exactly the (layer, day) shape
-- this query filters on -- one indexed scan per day is already bounded, so this file takes a day
-- and a row cap rather than a cell-id array.
--
-- THE DAY-NAMING TRAP (drizzle/0015_tile_observation_day.sql). geo.feature_observation_day reads
-- the PUBLISHER-NAMED day -- the first ten characters of the timestamp text, before any UTC
-- offset is applied -- never a cast of the reading instant to timestamptz and then to date. That
-- conversion alone moved 6,279 of 16,743 production water-gauges rows onto the day AFTER the one
-- they name. observed_day below calls the very function the map's time slider and tile layers
-- call, so this export lands on the day the slider actually offers. observed_at is a SEPARATE
-- column carrying the true UTC instant for provenance, and it may legitimately fall on a
-- different UTC calendar day than observed_day -- a reader wants observed_day for the map's axis
-- and observed_at for real elapsed time between readings.
--
-- WHY GEOMETRY-ORPHANED ROWS ARE KEPT, UNLIKE THE SLIDER'S OWN CENSUS. observed_days.sql filters
-- geometry_id IS NOT NULL because an unlinked row cannot be drawn on the map. This query feeds ML
-- feature completion rather than the map, and a gauge's own latitude, longitude and discharge live
-- directly in its properties independent of the geometry-dimension link -- docs/lanes/water-gauges.md
-- section 4 measured 37% of one day's rows unlinked (2026-08-04), so filtering them here would
-- silently discard over a third of real discharge measurements. geometry_linked reports the link
-- state instead of hiding the row.
--
-- WHY flowCfs AND percentile CAN BE NULL. A forward-path tick where NWIS reported nothing for a
-- site keeps that site's identity alive with a wall-clock-stamped row carrying a null flowCfs,
-- rather than dropping it or inventing a value (ingest/usgs_nwis.py, parse_gauge). percentile is
-- always null from this producer today (classify_condition is called with a literal null at both
-- call sites) and is kept as a real, honestly-null column because NWIS's own schema defines one
-- and a future pipeline change may populate it. Genuine sentinel readings and reverse-flow
-- readings down to -172,000 cfs are both handled upstream of this table (ingest/usgs_nwis.py,
-- is_missing_value_sentinel) -- this query trusts what already landed in geo.features and applies
-- no further filtering to flowCfs.
--
-- data_available_at IS ALWAYS NULL FOR THIS LANE TODAY. It is geo.features' ML leakage-boundary
-- column (src/lib/server/db/schema.ts) but no water-gauges producer is wired to supply it yet
-- (build_streamflow_gauge_identity never sets it, and FeatureIdentity.data_available_at defaults
-- to None). Exported as-is, honestly null, never backfilled with a guess. ingested_at
-- (features.created_at) is exported alongside it as a conservative, always-populated upper bound:
-- this platform could not have known a row before it was written to the warehouse, even though it
-- may well have known it earlier.
--
-- How this query works, clause by clause:
--
--   FROM geo.layers AS layers JOIN geo.features AS features ON features.layer_id = layers.id
--     Resolves the layer by its name rather than binding a uuid, so this file needs one fewer
--     parameter kept in sync with the caller's own layer slug constant.
--
--   WHERE layers.name = 'water-gauges'
--     Scopes the join to this lane's own layer. A literal, not a parameter: this file only ever
--     serves the water-gauges lane, so there is no caller-supplied input to bind here.
--
--   AND features.status = 'published'
--     Draft or rejected rows are real rows in the table but the map never serves them; an ML
--     feature plane exported from unpublished rows would train on data the intervention plan
--     cannot itself see.
--
--   AND geo.feature_observation_day(features.properties) = the observed_day parameter cast to date
--     The bounded, indexed day filter -- see the day-naming trap note above. The bind is cast
--     explicitly so PostgreSQL is never left guessing whether it is comparing a date or text.
--
--   the ->> extraction and cast pairs in the SELECT list
--     latitude, longitude, flowCfs and percentile arrive in the JSON payload as numbers or as a
--     JSON null; ->> renders each as text (or SQL NULL for a JSON null) and the explicit
--     double-precision cast parses the text into a number. updatedAt arrives as an offset-bearing
--     ISO-8601 string that ingest/identity.py already proved parseable before the row could ever
--     be written -- a row whose updatedAt failed to parse is never written at all
--     (ingest/usgs_nwis.py, build_gauge_write) -- so the timestamptz cast here cannot fail.
--
--   features.geometry_id IS NOT NULL AS geometry_linked
--     A boolean projection of the geometry-dimension link state, kept rather than filtered on --
--     see the orphan note above.
--
--   ORDER BY site_number, observed_at, features.id
--     A stable, total order: ascending on the grain this stream's schema sorts to, plus the row's
--     own id as a final tiebreaker. It also makes the row cap below deterministic -- without an
--     order, a capped result would be an arbitrary sample rather than the earliest readings.
--
--   LIMIT the row_limit parameter
--     The cap. It exists so a runaway day cannot pull an unbounded result into memory; the caller
--     treats reaching it as an error, not as an answer, because a truncated day silently drops
--     real readings rather than reporting an honest partial day.
SELECT
    features.properties ->> 'siteNo'                          AS site_number,
    (features.properties ->> 'updatedAt')::timestamptz        AS observed_at,
    geo.feature_observation_day(features.properties)          AS observed_day,
    features.properties ->> 'siteName'                        AS site_name,
    (features.properties ->> 'lat')::double precision         AS latitude,
    (features.properties ->> 'lon')::double precision         AS longitude,
    (features.properties ->> 'flowCfs')::double precision     AS flow_cfs,
    (features.properties ->> 'percentile')::double precision  AS percentile,
    features.properties ->> 'condition'                       AS condition,
    features.properties ->> 'trend'                           AS trend,
    features.properties ->> 'source'                          AS source,
    features.geometry_id IS NOT NULL                          AS geometry_linked,
    features.data_available_at                                AS data_available_at,
    features.created_at                                       AS ingested_at
FROM geo.layers AS layers
JOIN geo.features AS features
    ON features.layer_id = layers.id
WHERE layers.name = 'water-gauges'
  AND features.status = 'published'
  AND geo.feature_observation_day(features.properties) = (:observed_day)::date
ORDER BY site_number, observed_at, features.id
LIMIT :row_limit
