-- Serving surface for the ERA5-Land soil-moisture lane.
--
-- The lane lands in the MODEL plane (`agri.signal_observation` joined to
-- `agri.spatial_cell`): three signals -- soil_water_content_layer_1/_2/_3 for the
-- 0-7 cm, 7-28 cm and 28-100 cm ECMWF layers -- normalized to m^3/m^3 on the
-- 0.25-degree `sentinel2-ndvi-0p25deg` lattice, daily at midnight UTC, 1,568 cells
-- over the PNW. Nothing in the app could read it: there were zero references to
-- `soil_water_content` or `era5-land` anywhere in `src/`.
--
-- Both objects are created in `geo`, the SERVING plane this application owns, and
-- read from `agri`, the model plane the ingestion service owns. That keeps the
-- ownership boundary intact: this migration adds nothing to `agri` and takes no
-- lock on a table the ingestion crawl writes.
--
-- MARTIN IS NOT AFFECTED. `infra/martin/martin.yaml` sets `auto_publish: false` and
-- names its six function sources explicitly, so a new `geo` function cannot join a
-- composite source id and no tile server needs restarting -- unlike 0009 and 0012,
-- which did add published tile functions. Nothing here creates a tile function.
--
-- No index is added to `agri.signal_observation` on purpose. Measured against
-- production 2026-08-05 with EXPLAIN (ANALYZE): a whole-PNW coarse aggregation runs
-- in 12 ms on the existing `ix_signal_observation_cell_time_signal (cell_id,
-- observed_at, signal_name)` via an index-only scan, because the bbox resolves the
-- cell list first and the day probe is then one index search per cell. An index
-- built here would also lock a table a live crawl is writing to, for no measured gain.

-- The flattened lane: one row per cell per day per depth, carrying the cell geometry
-- and the provenance the licence requires. A view rather than a materialization --
-- predicates push straight through to the indexes above, and a copy would need a
-- refresh path the ingestion service knows nothing about.
--
-- `is_observed` / `quality_flag = 'accepted'` are applied HERE rather than at each call
-- site, so no reader can accidentally serve a rejected or imputed value as a measurement.
CREATE OR REPLACE VIEW geo.soil_moisture_observation AS
SELECT
  observation.cell_id,
  cell.cell_key,
  cell.grid_name,
  cell.resolution_m,
  cell.geometry AS cell_geometry,
  cell.centroid AS cell_centroid,
  observation.signal_name,
  observation.support_key,
  observation.observed_at,
  (observation.observed_at AT TIME ZONE 'UTC')::date AS observed_day,
  observation.normalized_value,
  observation.normalized_unit,
  observation.coverage_fraction,
  source.key AS data_source_key,
  source.name AS data_source_name,
  source.license_name,
  source.allowed_client_exposure
FROM agri.signal_observation AS observation
JOIN agri.spatial_cell AS cell ON cell.id = observation.cell_id
JOIN agri.source_release AS release ON release.id = observation.source_release_id
JOIN agri.data_source AS source ON source.id = release.data_source_id
WHERE observation.signal_name IN (
        'soil_water_content_layer_1',
        'soil_water_content_layer_2',
        'soil_water_content_layer_3'
      )
  AND observation.normalized_unit = 'm^3/m^3'
  AND observation.is_observed
  AND observation.quality_flag = 'accepted'
  AND observation.normalized_value IS NOT NULL;
--> statement-breakpoint

COMMENT ON VIEW geo.soil_moisture_observation IS
  'ERA5-Land volumetric soil water per 0.25-degree cell per day per depth layer, with the '
  'cell geometry and the data_source row that licences it. Read by '
  'getPublishedSoilMoisture at the detail zoom tier.';
--> statement-breakpoint

-- The zoom-aggregated field: a flattened average over a coarser lattice, then a
-- Gaussian blur across that lattice. This is the whole "do not ship 1,568 discrete
-- squares to the browser" step, and it runs in SQL because the repo rule is that
-- geospatial aggregation goes through PostGIS.
--
-- Contouring is NOT done here. PostGIS `ST_Contour` needs the `postgis_raster`
-- extension, which is AVAILABLE but NOT INSTALLED on this server (verified against
-- production 2026-08-05: pg_available_extensions lists postgis_raster 3.6.4 with
-- installed_version NULL, and pg_extension holds only postgis, timescaledb,
-- timescaledb_toolkit, vector, pgcrypto and plpgsql). Installing a raster extension is
-- a far larger change than this layer justifies, so the marching-squares step runs in
-- TypeScript on the server over the small node grid this function returns -- see
-- `src/lib/geo/isobands.ts`. What crosses the wire to the browser is the isobands, not
-- the grid.
--
-- Steps, in order:
--   1. halo   -- the viewport grown by the blur radius, so an edge node is averaged
--               against its real neighbours instead of against the absence of them.
--   2. served -- the newest observed day at or before `through_day`, inside
--               `max_age_days`. Resolved in SQL so one round trip answers "which day
--               are we actually showing" as well as "what does it look like".
--   3. node   -- native cell centroids snapped to `lattice_degrees` and averaged,
--               weighted by `coverage_fraction`, so a partially-covered cell counts
--               for less than a whole one.
--   4. blur   -- a discrete 2-D Gaussian over the lattice: every node within
--               `blur_radius_cells` contributes exp(-d^2 / 2 sigma^2). Normalized by
--               the weights actually present, so a node at the edge of coverage is
--               smoothed against what exists rather than pulled toward zero.
CREATE OR REPLACE FUNCTION geo.soil_moisture_field(
  bbox_west double precision,
  bbox_south double precision,
  bbox_east double precision,
  bbox_north double precision,
  target_signal text,
  target_support_key text,
  through_day date,
  max_age_days integer,
  lattice_degrees double precision,
  blur_sigma_degrees double precision,
  blur_radius_cells integer
)
RETURNS TABLE (
  observed_day date,
  node_lon double precision,
  node_lat double precision,
  smoothed_value double precision,
  raw_value double precision,
  source_cell_count integer
)
LANGUAGE sql
STABLE
PARALLEL SAFE
AS $$
  WITH halo AS (
    SELECT ST_MakeEnvelope(
      bbox_west  - blur_radius_cells * lattice_degrees,
      bbox_south - blur_radius_cells * lattice_degrees,
      bbox_east  + blur_radius_cells * lattice_degrees,
      bbox_north + blur_radius_cells * lattice_degrees,
      4326
    ) AS envelope
  ),
  covered_cell AS (
    SELECT cell.id, ST_X(cell.centroid) AS lon, ST_Y(cell.centroid) AS lat
    FROM agri.spatial_cell AS cell, halo
    WHERE cell.geometry && halo.envelope
  ),
  served AS (
    SELECT max(candidate.observed_at) AS observed_at
    FROM agri.signal_observation AS candidate
    WHERE candidate.cell_id IN (SELECT id FROM covered_cell)
      AND candidate.signal_name = target_signal
      AND candidate.support_key = target_support_key
      AND candidate.observed_at <= (through_day + 1)::timestamptz
      AND candidate.observed_at >  (through_day - max_age_days)::timestamptz
  ),
  observation AS (
    SELECT
      covered_cell.lon,
      covered_cell.lat,
      reading.normalized_value AS value,
      reading.coverage_fraction AS weight
    FROM covered_cell
    JOIN agri.signal_observation AS reading ON reading.cell_id = covered_cell.id
    CROSS JOIN served
    WHERE reading.signal_name = target_signal
      AND reading.support_key = target_support_key
      AND reading.observed_at = served.observed_at
      AND reading.is_observed
      AND reading.quality_flag = 'accepted'
      AND reading.normalized_value IS NOT NULL
  ),
  node AS (
    SELECT
      floor(observation.lon / lattice_degrees) * lattice_degrees + lattice_degrees / 2
        AS node_lon,
      floor(observation.lat / lattice_degrees) * lattice_degrees + lattice_degrees / 2
        AS node_lat,
      sum(observation.value * observation.weight)
        / NULLIF(sum(observation.weight), 0) AS raw_value,
      count(*)::integer AS source_cell_count
    FROM observation
    GROUP BY 1, 2
  )
  SELECT
    (SELECT (served.observed_at AT TIME ZONE 'UTC')::date FROM served) AS observed_day,
    target.node_lon,
    target.node_lat,
    sum(
      neighbour.raw_value
      * exp(-(   (neighbour.node_lon - target.node_lon) ^ 2
               + (neighbour.node_lat - target.node_lat) ^ 2)
            / (2 * GREATEST(blur_sigma_degrees, 1e-9::double precision) ^ 2))
    )
    / NULLIF(sum(
        exp(-(   (neighbour.node_lon - target.node_lon) ^ 2
               + (neighbour.node_lat - target.node_lat) ^ 2)
            / (2 * GREATEST(blur_sigma_degrees, 1e-9::double precision) ^ 2))
      ), 0) AS smoothed_value,
    target.raw_value,
    target.source_cell_count
  FROM node AS target
  JOIN node AS neighbour
    ON abs(neighbour.node_lon - target.node_lon) <= blur_radius_cells * lattice_degrees
   AND abs(neighbour.node_lat - target.node_lat) <= blur_radius_cells * lattice_degrees
  WHERE target.node_lon BETWEEN bbox_west AND bbox_east
    AND target.node_lat BETWEEN bbox_south AND bbox_north
  GROUP BY target.node_lon, target.node_lat, target.raw_value, target.source_cell_count
  ORDER BY target.node_lat, target.node_lon;
$$;
--> statement-breakpoint

COMMENT ON FUNCTION geo.soil_moisture_field IS
  'Zoom-aggregated ERA5-Land soil moisture: native 0.25-degree cells averaged onto a '
  'coarser lattice and Gaussian-smoothed across it. Returns grid nodes, not geometry -- '
  'the marching-squares isobands are built from these in src/lib/geo/isobands.ts.';
