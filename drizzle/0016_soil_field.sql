-- Widen the ERA5-Land serving surface from soil MOISTURE to soil FIELD: moisture and
-- temperature, on one lattice, through one reader.
--
-- 0014 shipped `geo.soil_moisture_observation` and `geo.soil_moisture_field` for the three
-- volumetric-water signals. The same Open-Meteo ERA5-Land archive lane also writes four
-- soil-temperature signals -- `soil_temperature_level_1..4` in degrees Celsius, the same
-- `support_key = 'era5-land-0.1deg'`, the same 0.25-degree `sentinel2-ndvi-0p25deg` cells,
-- the same daily-at-midnight-UTC grain -- and nothing in `src/` could read them.
--
-- Only ONE of the two 0014 objects actually constrained the measure:
--
--   * `geo.soil_moisture_field(...)` was already measure-agnostic. It takes `target_signal`
--     and `target_support_key` as parameters and reads `agri.signal_observation` directly,
--     so it aggregates temperature correctly today with no change to its body. It is
--     RENAMED here, not rewritten -- `ALTER FUNCTION ... RENAME TO` preserves the body
--     byte-for-byte, which is the point: a DROP + CREATE would fork the Gaussian-blur
--     definition into a second copy that could drift from the one 0014 reviewed.
--   * `geo.soil_moisture_observation` hard-coded the three moisture signal names and
--     `normalized_unit = 'm^3/m^3'` in its WHERE clause. That is what this replaces.
--
-- The replacement is a DROP + CREATE rather than CREATE OR REPLACE because the view gains a
-- column (`measure`) and changes name; PostgreSQL permits neither in place. Nothing but
-- `environmental-read-model.ts` reads it -- verified by grep across `src/`, `docs/`,
-- `infra/` -- so the drop is safe.
--
-- STILL NOT A TILE FUNCTION, and Martin is still unaffected: `infra/martin/martin.yaml`
-- sets `auto_publish: false` and names its function sources explicitly, so nothing here can
-- join a composite source id and no tile server needs restarting.
--
-- STILL NO NEW INDEX, for the reason 0014 gives: the bbox resolves the cell list first and
-- the day is then one index search per cell on the existing
-- `ix_signal_observation_cell_time_signal (cell_id, observed_at, signal_name)`. That index
-- is keyed on `signal_name` without regard to which signal, so the temperature signals ride
-- it exactly as the moisture ones do. Building an index here would lock a table the live
-- backfill is writing to, for no measured gain.

-- The flattened lane, both measures: one row per cell per day per signal, carrying the cell
-- geometry and the provenance the licence requires.
--
-- The (signal, unit) pairs are enumerated rather than matched by prefix. A signal arriving
-- in an unexpected unit -- Kelvin instead of Celsius, say -- must be invisible here rather
-- than served alongside comparable values and coloured as if it were one of them.
--
-- `measure` is derived from the signal name so a reader can filter to one quantity without
-- restating the signal list. `is_observed` / `quality_flag = 'accepted'` stay applied HERE
-- rather than at each call site, so no reader can serve a rejected or imputed value as a
-- measurement.
DROP VIEW IF EXISTS geo.soil_moisture_observation;
--> statement-breakpoint

CREATE VIEW geo.soil_field_observation AS
SELECT
  observation.cell_id,
  cell.cell_key,
  cell.grid_name,
  cell.resolution_m,
  cell.geometry AS cell_geometry,
  cell.centroid AS cell_centroid,
  CASE
    WHEN starts_with(observation.signal_name, 'soil_water_content') THEN 'moisture'
    ELSE 'temperature'
  END AS measure,
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
WHERE (
        (
          observation.signal_name IN (
            'soil_water_content_layer_1',
            'soil_water_content_layer_2',
            'soil_water_content_layer_3'
          )
          AND observation.normalized_unit = 'm^3/m^3'
        )
        OR (
          observation.signal_name IN (
            'soil_temperature_level_1',
            'soil_temperature_level_2',
            'soil_temperature_level_3',
            'soil_temperature_level_4'
          )
          AND observation.normalized_unit = 'C'
        )
      )
  AND observation.is_observed
  AND observation.quality_flag = 'accepted'
  AND observation.normalized_value IS NOT NULL;
--> statement-breakpoint

COMMENT ON VIEW geo.soil_field_observation IS
  'ERA5-Land volumetric soil water (m^3/m^3) and soil temperature (C) per 0.25-degree cell '
  'per day per depth layer, with the cell geometry and the data_source row that licences it. '
  'Read by getPublishedSoilField at the detail zoom tier. Supersedes '
  'geo.soil_moisture_observation, which covered moisture only.';
--> statement-breakpoint

-- Renamed, not redefined. The body already parameterizes the signal, so the only thing that
-- was ever moisture-specific about it was the name. Guarded so a database that already
-- carries the new name (a re-run, or one built from a later baseline) is a no-op rather
-- than an error.
DO $$
BEGIN
  IF EXISTS (
    SELECT 1
    FROM pg_proc AS proc
    JOIN pg_namespace AS namespace ON namespace.oid = proc.pronamespace
    WHERE namespace.nspname = 'geo'
      AND proc.proname = 'soil_moisture_field'
  ) THEN
    ALTER FUNCTION geo.soil_moisture_field(
      double precision, double precision, double precision, double precision,
      text, text, date, integer, double precision, double precision, integer
    ) RENAME TO soil_field;
  END IF;
END
$$;
--> statement-breakpoint

COMMENT ON FUNCTION geo.soil_field IS
  'Zoom-aggregated ERA5-Land soil field: native 0.25-degree cells for one signal averaged '
  'onto a coarser lattice and Gaussian-smoothed across it. Measure-agnostic -- the caller '
  'names the signal -- so soil moisture and soil temperature share one definition. Returns '
  'grid nodes, not geometry; the marching-squares isobands are built from these in '
  'src/lib/geo/isobands.ts.';
