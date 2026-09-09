-- Widen the ERA5-Land serving surface from soil MOISTURE to soil FIELD: moisture and
-- temperature, on one lattice, through one reader.
--
-- 0014 shipped `geo.soil_moisture_observation` and `geo.soil_moisture_field` for the three
-- volumetric-water signals. The same Open-Meteo ERA5-Land archive lane also writes four
-- soil-temperature signals -- `soil_temperature_level_1..4` in degrees Celsius, the same
-- `support_key = 'era5-land-0.1deg'`, the same 0.25-degree `sentinel2-ndvi-0p25deg` cells,
-- the same daily-at-midnight-UTC grain -- and nothing in `src/` could read them.
--
-- The function was already measure-agnostic and is RENAMED, not rewritten; only the view
-- hard-coded the measure, and it is replaced. Why the rename must not become a DROP +
-- CREATE, why the view's drop is safe, why Martin is unaffected, and why no index is added:
-- src/lib/server/db/AGENTS.md §soil-field-view.

-- The flattened lane, both measures: one row per cell per day per signal, carrying the cell
-- geometry and the provenance the licence requires.
--
-- `governed` states the reviewed (signal, measure, unit) triples ONCE. Joining it is what
-- gates the rows, so the row filter, the `measure` label and the accepted unit cannot
-- disagree -- widening this list is the whole edit, and there is no second list to forget.
-- A signal arriving in an unexpected unit (Kelvin instead of Celsius, say) matches nothing
-- and is invisible here rather than served beside comparable values and coloured as one of
-- them; a signal absent from the list -- this lane also carries `vapour_pressure_deficit` --
-- is likewise absent rather than silently relabelled. `is_observed` /
-- `quality_flag = 'accepted'` stay applied HERE rather than at each call site, so no reader
-- can serve a rejected or imputed value as a measurement.
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
  governed.measure,
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
JOIN (
  VALUES
    ('soil_water_content_layer_1'::text, 'moisture'::text, 'm^3/m^3'::text),
    ('soil_water_content_layer_2', 'moisture', 'm^3/m^3'),
    ('soil_water_content_layer_3', 'moisture', 'm^3/m^3'),
    ('soil_temperature_level_1', 'temperature', 'C'),
    ('soil_temperature_level_2', 'temperature', 'C'),
    ('soil_temperature_level_3', 'temperature', 'C'),
    ('soil_temperature_level_4', 'temperature', 'C')
) AS governed(signal_name, measure, normalized_unit)
  ON governed.signal_name = observation.signal_name
 AND governed.normalized_unit = observation.normalized_unit
JOIN agri.spatial_cell AS cell ON cell.id = observation.cell_id
JOIN agri.source_release AS release ON release.id = observation.source_release_id
JOIN agri.data_source AS source ON source.id = release.data_source_id
WHERE observation.is_observed
  AND observation.quality_flag = 'accepted'
  AND observation.normalized_value IS NOT NULL;
--> statement-breakpoint

COMMENT ON VIEW geo.soil_field_observation IS
  'ERA5-Land volumetric soil water (m^3/m^3) and soil temperature (C) per 0.25-degree cell '
  'per day per depth layer, with the cell geometry and the data_source row that licences it. '
  'Read by getPublishedSoilField at the detail zoom tier. Supersedes '
  'geo.soil_moisture_observation, which covered moisture only.';
--> statement-breakpoint

-- Renamed, not redefined: the body already parameterizes the signal, so the only thing that
-- was ever moisture-specific about it was the name. Guarded so a database that already
-- carries the new name (a re-run, or one built from a later baseline) is a no-op.
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
