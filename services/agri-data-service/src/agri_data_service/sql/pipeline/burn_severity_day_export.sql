-- burn_severity_day_export
-- Purpose: produce every published MTBS burned-area boundary dated to ONE release-publication
--          day, for writing to `layer=burn-severity/kind=observed/year=/month=/day=/part-N.parquet`.
-- Loaded by: agri_data_service.pipeline.lanes.burn_severity
-- Params: release_day (date -- the UTC calendar day of one MTBS release's publication date, i.e.
--         the date part of properties->>'observedAt'. This lane is release-based, not daily: it
--         has exactly FIVE such days across all of history today -- docs/lanes/burn-severity.md
--         section 3.)
--
-- Parameter names appear above WITHOUT a leading colon. See "Header/bind-param trap" in
-- sql/AGENTS.md -- SQLAlchemy's text() scans comments too, and a colon-prefixed word here would
-- mint a phantom bind parameter no caller supplies.
--
-- THIS IS A TRANSCRIPTION, NOT A NEW QUERY, of geo.burn_severity_tiles
-- (drizzle/0012_burn_severity_tiles.sql), the map's own MVT function for this layer -- so this
-- export reads exactly the population the map already serves, with the same defensive jsonb
-- casts that migration's own comment says were paid for by a prior bug
-- (0010_sensor_tile_properties.sql: a bare cast turned one malformed upstream row into a 500 for
-- a whole tile). Differences from the tile function, enumerated:
--
--   * SCOPED TO ONE RELEASE DAY. The tile function serves the whole layer at every zoom; this
--     query answers only the rows dated to `release_day`.
--   * ignition_date/observed_day/data_available_at are cast to real date/timestamp types here.
--     The tile function leaves them as raw text (MVT has no date type and the map only displays
--     them); both source strings are always well-formed ISO-8601, written by exactly one
--     producer (ingest/mtbs.py build_mtbs_write, mtbs.py:1021-1056), never user input.
--   * GEOMETRY IS EXPORTED AS WKB (ST_AsBinary), not simplified/projected MVT geometry. The tile
--     function reprojects to EPSG:3857 and clips to a tile envelope for rendering; this export
--     keeps the native EPSG:4326 polygon the source publishes, unmodified.
--   * allowed_client_exposure IS A HARDCODED FALSE LITERAL, not a column read off any table.
--     Burn-severity's ingest path bypasses agri.source_release/agri.data_source entirely
--     (mtbs.py:944-954: "the warehouse path is deliberately NOT the source-ingest verb"), so
--     there is no governed database row to join for this the way signal_plane_day_export.sql
--     joins agri.source_release. The restriction is ingest-module policy instead: "allowed_
--     client_exposure stays false: nothing MTBS-derived may reach a public CDN without a fresh
--     licensing review" (mtbs.py:171, MTBS_PURPOSE). Carrying the constant forward here is what
--     stops a future Parquet/PMTiles reader from losing this still-live restriction. Note this
--     disagrees with geo.layers.is_public = true for this layer
--     (drizzle/0011_burn_severity_layer.sql:17) -- that column is a map-rendering toggle, not
--     this licensing gate, and the two have never been reconciled; the more restrictive policy
--     wins here.
--
-- WHY THERE IS NO ROW-BATCHING PARAMETER (unlike signal_plane_day_export.sql's cell_ids array):
-- this lane's entire history is 541 rows across FIVE release days
-- (docs/lanes/burn-severity.md section 5), so one release day is at most a few hundred rows --
-- nowhere near the 11 GB heap that forces signal_observation to batch by cell. What
-- pipeline/lanes/burn_severity.py still bounds is how many of those rows land in one Parquet
-- part file, because the risk here is bytes-per-row (a full-resolution polygon can be tens of
-- megabytes), not row count.
--
-- Clause by clause:
--   FROM geo.features JOIN geo.layers
--     geo.features holds one CURRENT row per MTBS Fire_ID for this layer -- the producer
--     refreshes a fire's row in place rather than versioning a new row per release
--     (ingest/mtbs.py:1154-1156, "re-running is cheap and idempotent") -- so there is no history
--     table to replay. Reading current state IS reading the release history here, because a
--     fire's release day never moves once its ignition year's governed release date is set
--     (ingest/mtbs.py MTBS_ANNUAL_RELEASE_DATES).
--   WHERE layer.name = 'burn-severity'
--     Scopes to this layer alone; geo.features holds every layer's rows in one table.
--   AND feature.status = 'published'
--     Excludes anything not yet through moderation. MTBS rows default to 'published' (no
--     producer sets another status); this guards the export against a future review path.
--   AND feature.geom IS NOT NULL
--     geom is populated by a trigger from properties.geometry
--     (geo.sync_feature_geom_from_properties, drizzle/0008_geometry_dimension.sql:113). This
--     excludes the structurally-impossible case of a row whose geometry sync has not yet run,
--     rather than emit a NULL into a WKB column this lane's schema declares NOT NULL.
--   AND (properties->>'observedAt')::timestamptz::date = (release_day)::date
--     The release-day filter itself. `observedAt` carries the MTBS release's PUBLICATION date
--     (mtbs.py:1031), never `ignitionDate` -- using the ignition date would date a fire by when
--     it burned rather than by when this platform could have known about it, which is exactly
--     the lookahead leak the lane contract forbids (docs/lanes/burn-severity.md section 4).
SELECT
    feature.id::text AS feature_id,
    feature.properties ->> 'fireId' AS fire_id,
    'mtbs:' || (feature.properties ->> 'fireId') AS natural_key,
    feature.properties ->> 'releaseIdentifier' AS release_identifier,
    feature.properties ->> 'mappingRevision' AS mapping_revision,
    CASE WHEN jsonb_typeof(feature.properties -> 'fireYear') = 'number'
        THEN (feature.properties ->> 'fireYear')::integer
    END AS fire_year,
    (feature.properties ->> 'ignitionDate')::date AS ignition_date,
    (feature.properties ->> 'observedAt')::timestamptz::date AS observed_day,
    (feature.properties ->> 'observedAt')::timestamptz AS data_available_at,
    feature.properties ->> 'fireName' AS fire_name,
    feature.properties ->> 'fireType' AS fire_type,
    feature.properties ->> 'assessmentType' AS assessment_type,
    CASE WHEN jsonb_typeof(feature.properties -> 'acres') = 'number'
        THEN (feature.properties ->> 'acres')::double precision
    END AS acres,
    feature.properties ->> 'severityClass' AS severity_class,
    CASE WHEN jsonb_typeof(feature.properties -> 'severityThresholds' -> 'dnbr_offset') = 'number'
        THEN (feature.properties -> 'severityThresholds' ->> 'dnbr_offset')::integer
    END AS dnbr_offset,
    CASE WHEN jsonb_typeof(feature.properties -> 'severityThresholds' -> 'dnbr_standard_deviation') = 'number'
        THEN (feature.properties -> 'severityThresholds' ->> 'dnbr_standard_deviation')::integer
    END AS dnbr_standard_deviation,
    CASE WHEN jsonb_typeof(feature.properties -> 'severityThresholds' -> 'nodata_threshold') = 'number'
        THEN (feature.properties -> 'severityThresholds' ->> 'nodata_threshold')::integer
    END AS nodata_threshold,
    CASE WHEN jsonb_typeof(feature.properties -> 'severityThresholds' -> 'greenness_threshold') = 'number'
        THEN (feature.properties -> 'severityThresholds' ->> 'greenness_threshold')::integer
    END AS greenness_threshold,
    CASE WHEN jsonb_typeof(feature.properties -> 'severityThresholds' -> 'low_threshold') = 'number'
        THEN (feature.properties -> 'severityThresholds' ->> 'low_threshold')::integer
    END AS low_threshold,
    CASE WHEN jsonb_typeof(feature.properties -> 'severityThresholds' -> 'moderate_threshold') = 'number'
        THEN (feature.properties -> 'severityThresholds' ->> 'moderate_threshold')::integer
    END AS moderate_threshold,
    CASE WHEN jsonb_typeof(feature.properties -> 'severityThresholds' -> 'high_threshold') = 'number'
        THEN (feature.properties -> 'severityThresholds' ->> 'high_threshold')::integer
    END AS high_threshold,
    FALSE AS allowed_client_exposure,
    ST_AsBinary(feature.geom) AS geom
FROM geo.features AS feature
JOIN geo.layers AS layer ON layer.id = feature.layer_id
WHERE layer.name = 'burn-severity'
  AND feature.status = 'published'
  AND feature.geom IS NOT NULL
  AND feature.properties ->> 'observedAt' IS NOT NULL
  AND (feature.properties ->> 'observedAt')::timestamptz::date = (:release_day)::date
