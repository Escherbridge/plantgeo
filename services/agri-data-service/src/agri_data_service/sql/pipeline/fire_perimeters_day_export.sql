-- fire_perimeters_day_export
-- Purpose: produce ONE dated snapshot of every currently-published WFIGS incident perimeter, at
--          the exported grain (snapshot_day, unique_fire_identifier), for writing to
--          `layer=fire-perimeters/kind=observed/year=/month=/day=/part-N.parquet`.
-- Loaded by: agri_data_service.pipeline.lanes.fire_perimeters
-- Params: snapshot_day (date -- the version stamp this export run captures; stamped onto every row
--         as `snapshot_day`, matching the partition path it is written to, and NEVER used to filter)
--
-- Parameter names appear above WITHOUT a leading colon. See "Header/bind-param trap" in
-- sql/AGENTS.md -- SQLAlchemy's text() scans comments too, and a colon-prefixed word here would
-- mint a phantom bind parameter no caller supplies.
--
-- THIS IS A FULL SNAPSHOT, NOT A DAY-RANGE FILTER, AND THE PREVIOUS SHAPE IS WHY.
-- `docs/lanes/fire-perimeters.md` #4/#5: `geo.features` holds ONE ROW PER WFIGS INCIDENT, refreshed
-- in place as its polygon and containment advance -- never one row per (incident, day). Postgres
-- holds no record of what this layer published on any day but today, so there is no honest
-- `WHERE observed_day = <one day>` this query can apply.
--
-- It applied one anyway until 2026-09-04, filtering on
-- `geo.feature_observation_day(features.properties)`, and the consequence was measured: 177
-- published perimeters landed across 45 partition days (`conductor/layer-sessions/fire-perimeters.md`,
-- "Measured state, 2026-08-25"), each partition holding only the incidents whose own publisher
-- timestamp named that day and 287 further days holding governed-absence markers. A reader wanting
-- what `geo.fire_risk_tiles` draws had to union a 404-day window back to the history floor. That
-- filter was not a fabrication -- each row really did carry that date -- but it sliced a snapshot
-- along an axis the source does not have, which is the same defect from the other direction as
-- replaying the current snapshot onto every run date.
--
-- The lane is now a `static_lookup` (`pipeline/parquet/lane_registry.py`): the partition day is a
-- VERSION STAMP driven by `sql/pipeline/lane_watermark_fire_perimeters.sql`, every published row is
-- selected unconditionally, and `snapshot_day` is stamped on as the capture date. This is the
-- identical shape `evacuation_zones_day_export.sql` already uses for the identical current-state
-- feed, and `foundation/parquet/AGENTS.md`'s "Static layers use the same layout" section names it
-- the correct one.
--
-- `observed_day` SURVIVES AS A PER-ROW COLUMN and that is what makes the map's date slider still
-- answerable from ONE read: `geo.fire_risk_tiles` itself applies no date predicate (it emits
-- `observed_day` as an MVT attribute and `src/lib/map/tile-layer-date-filter.ts` filters client
-- side), so a reader reproduces exactly what Martin draws by taking the newest snapshot and
-- filtering this column in-frame, instead of listing 404 partition days.
--
-- THE FILTER BELOW IS A TRANSCRIPTION of geo.fire_risk_tiles's own WHERE clause
-- (`drizzle/0038_tile_low_zoom_routing.sql:466-472`, minus the two tile-envelope predicates) -- the
-- canonical query that already decides which rows are "this layer, live" for the map, exactly as
-- `evacuation_zones_day_export.sql` transcribes `geo.evacuation_zone_tiles`. Exporting a different
-- population than the map itself serves would silently create two disagreeing answers for "what is
-- published today", and this lane exists to REPLACE that tile function.
--
-- TWO GATES THE OLD DAY EXPORT CARRIED ARE DELIBERATELY GONE, because both narrowed this export
-- below what Martin draws:
--   * `features.geometry_id IS NOT NULL` -- borrowed from `sql/ingest/observed_days.sql`, whose job
--     is a daily-series completeness census, not a definition of "live". The tile function has no
--     such predicate: a feature not yet linked to a `geo.geometry` version still has a shape on
--     `features.geom` and is still drawn. Orphans regrow because the forward path does not maintain
--     the dimension, so this gate could silently drop a served perimeter at any time.
--   * a parseable `geo.feature_observation_day` -- implied by the old `= <one day>` equality, since
--     no date equals NULL. `drizzle/0018_fire_discovery_observation_day.sql:39-40` states the
--     intended handling of an undatable row: it "returns NULL and is treated as undated by the
--     client filter, which shows it at every date rather than hiding it." Dropping it from the
--     export inverted that. `observed_day` is nullable in the Arrow schema for exactly this row.
-- `layers.is_public IS TRUE` is added for the same transcription reason: the tile has it and the old
-- day export did not, so a layer withdrawn from publication would have kept exporting.
--
-- WHY THE GEOMETRY COLUMN IS `geo.features.geom`, NOT `geo.geometry`.
-- `conductor/RUNBOOK.md`'s decisions table: "`geo.features.geom` is authoritative and the
-- [Type-2 geo.geometry] dimension is the stale copy." That dimension does exist for this
-- producer -- WFIGS was never hit by the entity-keying bug that broke it for
-- usgs-nwis/open-meteo -- but the lane doc #4 measured only 6 of thousands of dimension rows
-- across every producer ever reaching a second version, and its forward-maintenance path has a
-- known silent-freeze failure mode (`undatable`, lane doc #4). There is no growth history there
-- worth reading. `ST_AsBinary` emits standard WKB with no SRID header; every row here is
-- `geometry(GEOMETRY,4326)` (`src/lib/server/db/schema.ts:33`), so a reader must assume SRID
-- 4326 rather than read it off the bytes.
--
-- WHY NO CELL/KEY BATCHING, UNLIKE `signal_plane_day_export.sql`. That query batches by cell_id
-- because `agri.signal_observation` has no index leading on `observed_at` (an 11 GB heap). This
-- query has no time predicate to index at all now, and the whole layer holds 177 published rows
-- (lane doc #5), several orders of magnitude below the batching threshold that motivated the signal
-- plane's design. One statement is enough. The COST that does matter is bytes, not rows: each row
-- averages 130,583 B of geometry, so one snapshot is roughly 23 MB and the caller spills it into
-- parts by geometry bytes rather than by row count.
--
-- `layers.name = 'fire-perimeters'` is a literal, not a bound parameter: it is this lane's own
-- fixed slug (`identity.py`'s `PRODUCER_BY_LAYER_NAME["fire-perimeters"]`), never request input,
-- so there is nothing here for a bind parameter to protect.
SELECT
    features.id::text                                                AS feature_id,
    features.properties ->> 'uniqueFireIdentifier'                   AS unique_fire_identifier,
    (:snapshot_day)::date                                            AS snapshot_day,
    geo.feature_observation_day(features.properties)                 AS observed_day,
    features.properties ->> 'incidentName'                           AS incident_name,
    features.properties ->> 'irwinId'                                AS irwin_id,
    (features.properties ->> 'fireDiscoveryDateTime')::timestamptz   AS fire_discovery_at,
    (features.properties ->> 'polygonDateTime')::timestamptz         AS polygon_at,
    (features.properties ->> 'gisAcres')::double precision           AS gis_acres,
    features.properties ->> 'fireCause'                              AS fire_cause,
    features.properties ->> 'incidentTypeCategory'                   AS incident_type_category,
    features.properties ->> 'pooState'                               AS poo_state,
    (features.properties ->> 'percentContained')::double precision   AS percent_contained,
    features.properties ->> 'severity'                               AS severity,
    features.status                                                  AS status,
    features.data_available_at                                       AS data_available_at,
    features.updated_at                                              AS updated_at,
    ST_AsBinary(features.geom)                                       AS geometry_wkb
FROM geo.layers AS layers
JOIN geo.features AS features
    ON features.layer_id = layers.id
WHERE layers.name = 'fire-perimeters'
  AND layers.is_public IS TRUE
  AND features.status = 'published'
  AND features.geom IS NOT NULL
ORDER BY unique_fire_identifier
