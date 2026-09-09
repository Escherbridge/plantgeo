-- Widen the governed (signal, measure, unit) list in geo.soil_field_observation with the
-- lane's one atmospheric covariate: vapor pressure deficit, in kilopascals.
--
-- 0016 predicted this exact edit ("widening this list is the whole edit, and there is no
-- second list to forget"): the Open-Meteo ERA5-Land archive lane already writes
-- `vapor_pressure_deficit` on the same 0.25-degree cells, the same
-- `support_key = 'era5-land-0.1deg'` and the same daily grain as the seven soil-state
-- signals -- 2,149,140 accepted rows on production 2026-08-08 -- and the only thing keeping
-- them out of `src/` was their absence from this list. `geo.soil_field()` needs nothing: it
-- parameterizes the signal and never had a list to widen.
--
-- CREATE OR REPLACE rather than DROP + CREATE: the output column list is unchanged, so the
-- replace is legal and dependent readers never see the view missing.
CREATE OR REPLACE VIEW geo.soil_field_observation AS
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
    ('soil_temperature_level_4', 'temperature', 'C'),
    ('vapor_pressure_deficit', 'vpd', 'kPa')
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
  'ERA5-Land volumetric soil water (m^3/m^3), soil temperature (C) and daily-max vapor '
  'pressure deficit (kPa) per 0.25-degree cell per day per signal, with the cell geometry '
  'and the data_source row that licences it. Read by getPublishedSoilField at the detail '
  'zoom tier.';
