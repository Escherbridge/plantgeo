-- Serve the NASA POWER climate field: the meteorology and soil-wetness signals that have
-- been sitting in agri.signal_observation with no reader in src/ since the historical
-- backfill landed them.
--
-- A SECOND view rather than a widening of geo.soil_field_observation. That view's governed
-- list is the ERA5-Land lane -- one lattice (0.25-degree `sentinel2-ndvi-0p25deg`), one
-- support key (`era5-land-0.1deg`), one aggregation function keyed to it. NASA POWER rides a
-- different lattice (397 cells of `nasa-power-0.5-degree`), a different support key
-- (`surface`) and a different source (`nasa-power-daily`), and it is served at ONE tier with
-- no isobands -- see src/lib/server/db/AGENTS.md §climate-field-view. Folding both lanes into
-- one view would make every reader carry a grid predicate to tell them apart.
--
-- DROP + CREATE rather than CREATE OR REPLACE, so a database that already carries a
-- hand-applied earlier shape of this view is replaced rather than rejected on a column-list
-- mismatch, and a re-run of this file is a no-op either way.

-- The flattened lane: one row per cell per day per signal, carrying the cell geometry, the
-- provenance the licence requires, and the two release columns the reader dedupes on.
--
-- `governed` states the reviewed (signal_name, signal, normalized_unit) triples ONCE, exactly
-- as geo.soil_field_observation does. Joining it is what gates the rows, so the row filter,
-- the client-facing `signal` label and the accepted unit cannot disagree -- widening this list
-- is the whole edit, and there is no second list to forget. A signal arriving in an unexpected
-- unit (Kelvin instead of Celsius, mm/hour instead of mm/day) matches nothing and is invisible
-- here rather than served beside comparable values and coloured as one of them. `is_observed`
-- and `quality_flag = 'accepted'` stay applied HERE rather than at each call site, so no reader
-- can serve a rejected or imputed value as a measurement.
--
-- `source.key = 'nasa-power-daily'` is the LANE gate, and it is not redundant with the
-- support key: `surface` is a generic support the ERA5-Land writer also emits, so support_key
-- does not discriminate between the two lanes. Without the source gate a future ERA5 signal
-- that happened to share one of the names below would be served here, on this lane's ramps
-- and against this lane's 0.5-degree grid predicate. The view already joins `data_source` for
-- provenance, so the gate costs nothing.
--
-- `observation_id` and `release_retrieved_at` are published because this lane has DUPLICATES:
-- ~47k (cell_id, signal_name, observed_at) keys carry two rows from overlapping archive
-- releases, and the reader picks exactly one -- newest `retrieved_at` wins, tiebroken on the
-- observation's own id. Neither column can be derived downstream, so the view has to carry
-- them; the alternative is a reader that silently draws whichever duplicate the plan happened
-- to emit first.
DROP VIEW IF EXISTS geo.climate_field_observation;
--> statement-breakpoint

CREATE VIEW geo.climate_field_observation AS
SELECT
  observation.id AS observation_id,
  observation.cell_id,
  cell.cell_key,
  cell.grid_name,
  cell.resolution_m,
  cell.geometry AS cell_geometry,
  cell.centroid AS cell_centroid,
  governed.signal,
  observation.signal_name,
  observation.support_key,
  observation.observed_at,
  (observation.observed_at AT TIME ZONE 'UTC')::date AS observed_day,
  observation.normalized_value,
  observation.normalized_unit,
  observation.coverage_fraction,
  release.retrieved_at AS release_retrieved_at,
  source.key AS data_source_key,
  source.name AS data_source_name,
  source.license_name,
  source.allowed_client_exposure
FROM agri.signal_observation AS observation
JOIN (
  VALUES
    ('air_temperature_mean'::text, 'air-temperature'::text, 'C'::text),
    ('air_temperature_max', 'air-temperature', 'C'),
    ('air_temperature_min', 'air-temperature', 'C'),
    ('dew_point_temperature', 'dew-point', 'C'),
    ('precipitation', 'precipitation', 'mm/day'),
    ('relative_humidity', 'relative-humidity', '%'),
    ('surface_shortwave_radiation', 'shortwave-radiation', 'MJ/m^2/day'),
    ('wind_speed', 'wind-speed', 'm/s'),
    -- The three soil-wetness signals are a 4-cell PILOT against the lane's 397-cell lattice.
    -- They are listed anyway: a reader that cannot ask for them cannot report that they are
    -- sparse, and hiding a measured signal is a worse answer than drawing four honest cells.
    ('soil_wetness_surface', 'soil-wetness-surface', 'fraction_of_saturation'),
    ('soil_wetness_root_zone', 'soil-wetness-root-zone', 'fraction_of_saturation'),
    ('soil_wetness_profile', 'soil-wetness-profile', 'fraction_of_saturation')
) AS governed(signal_name, signal, normalized_unit)
  ON governed.signal_name = observation.signal_name
 AND governed.normalized_unit = observation.normalized_unit
JOIN agri.spatial_cell AS cell ON cell.id = observation.cell_id
JOIN agri.source_release AS release ON release.id = observation.source_release_id
JOIN agri.data_source AS source ON source.id = release.data_source_id
WHERE source.key = 'nasa-power-daily'
  AND observation.is_observed
  AND observation.quality_flag = 'accepted'
  AND observation.normalized_value IS NOT NULL;
--> statement-breakpoint

COMMENT ON VIEW geo.climate_field_observation IS
  'NASA POWER daily meteorology (air temperature mean/max/min, dew point, precipitation, '
  'relative humidity, shortwave radiation, wind speed) and pilot soil wetness (surface, root '
  'zone, profile) per 0.5-degree cell per day per signal, with the cell geometry and the '
  'data_source row that licences it. Read by getPublishedClimateField at its single serving '
  'tier. Carries observation_id and release_retrieved_at because overlapping archive releases '
  'leave duplicate (cell, signal, day) keys the reader must resolve.';
--> statement-breakpoint

-- No new index, and none is needed. Reads drive from agri.spatial_cell -- `grid_name` plus a
-- bbox against the GiST `ix_spatial_cell_geometry` -- and then probe one day per covered cell
-- through the existing btree `ix_signal_observation_cell_time_signal (cell_id, observed_at,
-- signal_name)`, which is the same access path the ERA5-Land detail tier measured at 27 ms
-- PNW-wide. This lattice is 397 cells against that one's 1,568, so it can only be cheaper. An
-- index built here would also lock a table the ingestion crawl writes to, for no measured gain.
COMMENT ON COLUMN geo.climate_field_observation.signal IS
  'The client-facing signal id from src/lib/environmental/climate-field.ts. The wire never '
  'carries a raw signal_name: the client sends this id and the server resolves it.';
