-- evacuation_zones_day_export
-- Purpose: produce ONE dated snapshot of every currently-published Oregon OEM evacuation area, at
--          the exported grain (snapshot_day, natural_key), for writing to
--          `layer=evacuation-zones/kind=observed/year=/month=/day=/part-N.parquet`.
-- Loaded by: agri_data_service.pipeline.lanes.evacuation_zones
-- Params: snapshot_day (date -- the UTC calendar day this export run captures; stamped onto every
--         row as `snapshot_day`, matching the partition path it is written to)
--
-- Parameter names appear above WITHOUT a leading colon. See "Header/bind-param trap" in
-- sql/AGENTS.md -- SQLAlchemy's text() scans comments too, and a colon-prefixed word here would
-- mint a phantom bind parameter no caller supplies.
--
-- THIS IS A FULL SNAPSHOT, NOT A DAY-RANGE FILTER, AND THAT IS DELIBERATE. `geo.features` holds
-- Oregon's feed as CURRENT STATE, refreshed in place (sql/ingest/refresh_features.sql), and
-- `geo.geometry`'s Type-2 chain versions only the polygon SHAPE, never the evacuation level or
-- other attributes (docs/lanes/evacuation-zones.md §3-4). There is therefore no honest
-- `WHERE observed_at >= snapshot_day AND observed_at < snapshot_day + 1` this query could apply --
-- Postgres holds no record of what was published on any day but today. Every row currently
-- published is selected unconditionally, and `snapshot_day` is stamped on as the capture date,
-- never used to filter. `foundation/parquet/AGENTS.md`'s "Static layers use the same layout"
-- section names this the correct shape for this lane: one full re-snapshot per release day, on
-- the identical `year=/month=/day=` object layout every other lane uses.
--
-- THE FILTER BELOW IS A TRANSCRIPTION of geo.evacuation_zone_tiles's own WHERE clause
-- (drizzle/0015_tile_observation_day.sql:144-151) -- the canonical query that already decides
-- which rows are "this layer, live" for the map. Exporting a different population than what the
-- map itself serves would silently create two disagreeing answers for "what is published today".
--
-- observed_at IS NEVER last_edited_date. It reads `properties->>'observedAt'`, which
-- build_evacuation_zone_write (ingest/evacuation_zones.py:314-322) sets to the upstream's own
-- created_date and leaves absent (-> SQL NULL) for an area Oregon never dated -- never guessed as
-- "now". No COALESCE(observedAt, updatedAt, ...) fallback is applied here even though the
-- tile-serving read model (geo.feature_observation_day) applies one for map display: that
-- fallback launders PlantGeo's own polling clock into an "observed" time, which is exactly the
-- fabrication a time-honest export must refuse.
--
-- Geometry is read off `feature.geom`, the same column the tile function renders, and encoded as
-- plain WKB (ST_AsBinary) rather than EWKB: the SRID is a schema-level constant
-- (EVACUATION_ZONES_GEOMETRY_SRID in warehouse/schemas/evacuation_zones.py), not a per-row fact,
-- because the column type fixes it at 4326.
--
-- geo.geometry is LEFT JOINed, not INNER JOINed: a feature not yet linked to a geometry version
-- (`feature.geometry_id IS NULL`) still has a shape on `feature.geom` and must still export,
-- rather than silently vanishing from the snapshot because a provenance column could not be
-- filled. Its two provenance columns are nullable for exactly this case.
--
-- `producer` and `natural_key` are NOT read off `geo.geometry` because that would tie their
-- presence to the same LEFT JOIN. This lane has exactly one producer
-- (EVACUATION_ZONES_PRODUCER, ingest/evacuation_zones.py:65), so both are reconstructed directly
-- from the literal and `properties->>'globalId'`, in the same `{producer}:{producer_local_id}`
-- shape `FeatureIdentity.natural_key` mints (drizzle/0008_geometry_dimension.sql:9-19).
SELECT
    feature.properties ->> 'globalId' AS global_id,
    'or-oem-evacuation-areas:' || (feature.properties ->> 'globalId') AS natural_key,
    'or-oem-evacuation-areas' AS producer,
    (:snapshot_day)::date AS snapshot_day,
    feature.properties ->> 'evacuationAreaName' AS evacuation_area_name,
    feature.properties ->> 'fireName' AS fire_name,
    feature.properties ->> 'county' AS county,
    feature.properties ->> 'hazardType' AS hazard_type,
    (feature.properties ->> 'evacuationLevel')::integer AS evacuation_level,
    feature.properties ->> 'evacuationLevelLabel' AS evacuation_level_label,
    feature.properties ->> 'severity' AS severity,
    (feature.properties ->> 'structuresWithin')::double precision AS structures_within,
    (feature.properties ->> 'addressesWithin')::double precision AS addresses_within,
    (feature.properties ->> 'populationWithin')::double precision AS population_within,
    feature.properties ->> 'editorName' AS editor_name,
    (feature.properties ->> 'observedAt')::timestamptz AS observed_at,
    feature.properties ->> 'source' AS source,
    ST_AsBinary(feature.geom) AS geometry_wkb,
    geometry.geometry_id::text AS geometry_version_id,
    geometry.version_valid_from AS geometry_version_valid_from,
    geometry.last_confirmed_at AS geometry_last_confirmed_at,
    feature.data_available_at,
    feature.updated_at AS feature_updated_at
FROM geo.features AS feature
JOIN geo.layers AS layer ON layer.id = feature.layer_id
LEFT JOIN geo.geometry AS geometry ON geometry.geometry_id = feature.geometry_id
WHERE layer.name = 'evacuation-zones'
  AND layer.is_public IS TRUE
  AND feature.status = 'published'
  AND feature.geom IS NOT NULL
ORDER BY natural_key
