-- fire_perimeters_day_export
-- Purpose: produce every WFIGS incident dated to ONE calendar day, at the exported grain
--          (observed_day, unique_fire_identifier), for writing to
--          `layer=fire-perimeters/kind=observed/year=/month=/day=/part-N.parquet`.
-- Loaded by: agri_data_service.pipeline.lanes.fire_perimeters
-- Params: observed_day (date -- the one UTC calendar day being exported)
--
-- Parameter names appear above WITHOUT a leading colon. See "Header/bind-param trap" in
-- sql/AGENTS.md -- SQLAlchemy's text() scans comments too, and a colon-prefixed word here would
-- mint a phantom bind parameter no caller supplies.
--
-- THIS LANE IS NOT DAILY-SERIES, AND THE FILTER BELOW EXISTS TO KEEP THAT HONEST.
-- `docs/lanes/fire-perimeters.md` #4/#5: `geo.features` holds ONE ROW PER WFIGS INCIDENT,
-- refreshed in place as its polygon and containment advance -- never one row per (incident, day).
-- The layer's own declared temporal shape is "event"
-- (`src/lib/server/services/environmental-read-model.ts:3000`), explicitly not "daily_series".
-- An exporter that used the RUN date (whatever day this job happens to execute on) would
-- silently manufacture a daily time series that isn't one, replaying the same current-snapshot
-- row on every day it runs -- exactly the trap the lane doc names. Filtering instead on
-- `geo.feature_observation_day(features.properties) = :observed_day` -- THE SAME function
-- `sql/ingest/observed_days.sql`'s completeness census and the map's own date slider already
-- read -- means a row lands on the ONE day its own publisher-supplied timestamp dates it to,
-- and nowhere else. A day with zero qualifying rows is real information (most calendar days see
-- no incident dated to them at all), not a fetch failure; the caller records that as a governed
-- absence rather than attempting an empty write.
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
-- query's day predicate, `geo.feature_observation_day(features.properties)`, IS indexed --
-- `ix_features_layer_observation_day` on `(layer_id, geo.feature_observation_day(properties))`,
-- confirmed used by the planner (`sql/ingest/observed_days.sql`'s own header) -- and the whole
-- layer holds 177 published rows (lane doc #5), several orders of magnitude below the batching
-- threshold that motivated the signal plane's design. One statement is enough.
--
-- THE SAME THREE GATES `sql/ingest/observed_days.sql` USES, reproduced here so this export never
-- diverges from what the map serves and what the completeness census counts: `status =
-- 'published'`, `geometry_id IS NOT NULL`, and a parseable `geo.feature_observation_day`. A
-- fourth, `geom IS NOT NULL`, is this query's own addition: it is this file's NOT NULL contract
-- on `geometry_wkb`, not a rule the census shares, guarding against an unlinked-but-somehow-
-- geometryless row ever reaching a binary column declared non-nullable.
--
-- `layers.name = 'fire-perimeters'` is a literal, not a bound parameter: it is this lane's own
-- fixed slug (`identity.py`'s `PRODUCER_BY_LAYER_NAME["fire-perimeters"]`), never request input,
-- so there is nothing here for a bind parameter to protect.
SELECT
    features.id::text                                                AS feature_id,
    features.properties ->> 'uniqueFireIdentifier'                   AS unique_fire_identifier,
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
  AND features.status = 'published'
  AND features.geometry_id IS NOT NULL
  AND features.geom IS NOT NULL
  AND geo.feature_observation_day(features.properties) = (:observed_day)::date
